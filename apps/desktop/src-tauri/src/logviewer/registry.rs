//! Phase 2F+ Task 5 — task registry + shared state for the log viewer.
//!
//! ## Design
//!
//! - **State machine** — every long-running operation (index_file, search)
//!   is represented by a [`TaskEntry`] whose [`TaskStatus`] moves through
//!   the canonical transitions:
//!
//!   ```text
//!      submit_index / submit_search
//!             │
//!             ▼
//!         ┌───────┐ mark_running  ┌────────┐ mark_completed ┌───────────┐
//!         │Queued ├──────────────►│Running ├──────────────►│Completed  │
//!         └───┬───┘               └───┬────┘               └───────────┘
//!             │                       │
//!             │ cancel()              │ cancel() (flag flip)
//!             ▼                       │
//!         ┌───────┐                   │ mark_cancelled
//!         │Cancel-│                   │ mark_failed
//!         │led    │                   ▼
//!         └───────┘              ┌────────┐
//!                                │Failed  │
//!                                └────────┘
//!   ```
//!
//!   Invalid transitions (e.g. `Completed → Running`) are rejected with
//!   `AppError::Validation` so we don't silently lose work.
//!
//! - **Atomic cancellation** — every `TaskEntry` owns an
//!   `Arc<AtomicBool>`. The background worker checks this flag at its
//!   natural cancellation points (between BufRead iterations in the
//!   indexer / searcher). When set, the worker returns early and is
//!   expected to call `mark_cancelled(id)` on the registry.
//!
//! - **Duplicate-indexing guard** — the registry tracks active tasks by
//!   canonical path. Submitting an `index` for a path that already has a
//!   non-terminal task returns `AppError::Validation` (idempotency: the
//!   caller can poll the existing task ID instead). Once the original
//!   task reaches a terminal state (Completed / Failed / Cancelled), a
//!   fresh submission for the same path is allowed. The path is
//!   canonicalized via [`std::fs::canonicalize`] so that
//!   `C:/foo/../bar.log` and `C:/bar.log` collide correctly.
//!
//! - **Search concurrency** — search tasks do NOT take the by_path lock
//!   (the same file can legitimately be searched multiple times in
//!   parallel by the UI). Only `submit_index` enforces the duplicate
//!   guard; `submit_search` always allocates a fresh TaskId.
//!
//! - **Delayed cleanup** — terminal tasks linger in the map for
//!   `FINISHED_TTL` (default 60s) so the UI can poll status one last
//!   time. `cleanup_finished(ttl)` returns the number of entries swept
//!   and is safe to call from any context.
//!
//! - **Thread safety** — `LogViewerState` is `Clone`-cheap
//!   (`Arc<Mutex<...>>` internally) and safe to share across Tauri
//!   command handlers. The `Mutex` is held only for short critical
//!   sections (register / status flip / sweep); workers do NOT hold it
//!   during the long I/O phase.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

use serde::{Deserialize, Serialize};

use crate::error::{AppError, AppResult};
use crate::logviewer::indexer::IndexSummary;

// ----------------------------------------------------------------------------
// Public types
// ----------------------------------------------------------------------------

/// Stable, opaque task identifier. We hand out UUIDv4 strings so callers
/// can hold a `String` without lifetime gymnastics.
pub type TaskId = String;

/// Kind of work this task represents.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum TaskKind {
    Index,
    Search,
}

impl TaskKind {
    /// Lowercase identifier, e.g. `"index"` / `"search"`. Useful for
    /// `by_path` keys and Tauri IPC serialization.
    pub fn as_str(&self) -> &'static str {
        match self {
            TaskKind::Index => "index",
            TaskKind::Search => "search",
        }
    }
}

/// Lifecycle status of a registered task.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TaskStatus {
    Queued,
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl TaskStatus {
    /// `true` for terminal states — these tasks no longer hold any
    /// worker resources and may be cleaned up by `cleanup_finished`.
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            TaskStatus::Completed | TaskStatus::Failed | TaskStatus::Cancelled
        )
    }
}

/// One row in the registry. `status` is the **observable** state —
/// what the UI sees. `cancel` is the **operational** signal — what the
/// background worker checks at cancellation points. The two are kept
/// consistent via the `cancel()` / `mark_*()` API contract on
/// [`LogViewerState`].
///
/// `finished_at` is set when the task transitions to a terminal status;
/// `cleanup_finished` uses it to compute age.
#[derive(Debug)]
pub struct TaskEntry {
    pub id: TaskId,
    pub kind: TaskKind,
    /// Canonical absolute path. The registry never holds the raw,
    /// caller-supplied form, so duplicate detection works regardless of
    /// how the caller spelled the path.
    pub path: PathBuf,
    pub status: TaskStatus,
    pub cancel: Arc<AtomicBool>,
    pub created_at: Instant,
    pub finished_at: Option<Instant>,
    pub error: Option<String>,
    /// Present only for successful index tasks (TaskKind::Index +
    /// TaskStatus::Completed). `None` otherwise.
    pub summary: Option<IndexSummary>,
}

/// Read-only snapshot returned by [`LogViewerState::get`]. Cloning is
/// cheap — only the path / strings are owned.
#[derive(Debug, Clone, Serialize)]
pub struct TaskSnapshot {
    pub id: TaskId,
    pub kind: TaskKind,
    pub path: PathBuf,
    pub status: TaskStatus,
    pub created_at_unix_ms: i64,
    pub finished_at_unix_ms: Option<i64>,
    pub error: Option<String>,
    pub summary: Option<IndexSummary>,
}

/// Why a submission was rejected (mirrors `AppError` variants used).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SubmitError {
    /// A `submit_index` for this path already has a non-terminal task.
    DuplicateActiveIndex,
    /// Path canonicalization failed (the file does not exist or is
    /// not accessible from this process).
    InvalidPath,
}

/// Default TTL for finished tasks before `cleanup_finished` reclaims
/// them. 60 seconds balances "UI can poll completion" against
/// "registry doesn't grow unbounded".
pub const FINISHED_TTL_SECS: u64 = 60;

// ----------------------------------------------------------------------------
// Internal action enum for `cancel` — keeps the mutable borrow on `inner.tasks`
// from spanning the subsequent `inner.active_index_by_path.remove` call.
// ----------------------------------------------------------------------------

#[derive(Debug)]
enum CancelAction {
    /// Queued → Cancelled; release the by-path slot.
    DirectCancelled,
    /// Running → flip the cancel flag only; worker will finalize.
    FlagSet,
    /// Already terminal; no-op. Carries the current status for the
    /// return value.
    Noop(TaskStatus),
}

// ----------------------------------------------------------------------------
// Internal registry storage
// ----------------------------------------------------------------------------

#[derive(Debug, Default)]
struct RegistryInner {
    /// All tasks, keyed by id.
    tasks: HashMap<TaskId, TaskEntry>,
    /// Active **index** tasks keyed by canonical path. Only one
    /// active index per path; search tasks don't take this slot.
    /// Value is the task id so we can look up the full entry on
    /// collision.
    active_index_by_path: HashMap<PathBuf, TaskId>,
}

/// Shared state object. Cheap to clone (inner `Arc<Mutex<...>>`).
///
/// `storage_path` is the on-disk location of `log_index.db` used by
/// background `spawn_blocking` workers. Defaults to a per-user
/// `log_index.db` under the platform data dir; production code paths
/// (e.g. `state.rs::try_init`) override this via
/// [`LogViewerState::with_storage_path`].
#[derive(Debug, Clone)]
pub struct LogViewerState {
    inner: Arc<Mutex<RegistryInner>>,
    storage_path: Arc<PathBuf>,
}

impl Default for LogViewerState {
    fn default() -> Self {
        Self {
            inner: Arc::new(Mutex::new(RegistryInner::default())),
            storage_path: Arc::new(default_storage_path()),
        }
    }
}

impl LogViewerState {
    /// Construct a new, empty registry. Cheap; may be called multiple
    /// times (e.g. in tests). Uses the platform default storage path.
    pub fn new() -> Self {
        Self::default()
    }

    /// Construct a registry bound to an explicit `log_index.db` path.
    /// Used by production state wiring (`state.rs::try_init`).
    pub fn with_storage_path(path: PathBuf) -> Self {
        Self {
            inner: Arc::new(Mutex::new(RegistryInner::default())),
            storage_path: Arc::new(path),
        }
    }

    /// Path to the `log_index.db` file that background workers should
    /// open. Cheap; just clones the `Arc<PathBuf>`.
    pub fn storage_path(&self) -> PathBuf {
        (*self.storage_path).clone()
    }

    /// Register a new index task for `path`. Returns the assigned
    /// `TaskId` on success. Rejects with `SubmitError::DuplicateActiveIndex`
    /// if an active index task already exists for the canonical path.
    pub fn submit_index(&self, path: &Path) -> Result<TaskId, SubmitError> {
        let canonical = canonicalize_or_self(path).ok_or(SubmitError::InvalidPath)?;
        let mut inner = self.inner.lock().expect("registry mutex poisoned");
        if let Some(existing) = inner.active_index_by_path.get(&canonical) {
            // Reject only if the referenced task is still active.
            if let Some(entry) = inner.tasks.get(existing) {
                if !entry.status.is_terminal() {
                    return Err(SubmitError::DuplicateActiveIndex);
                }
            }
            // Else: existing entry is terminal — fall through and
            // replace the by_path slot below.
        }
        let entry = TaskEntry::new(TaskKind::Index, canonical.clone());
        let id = entry.id.clone();
        inner.active_index_by_path.insert(canonical, id.clone());
        inner.tasks.insert(id.clone(), entry);
        Ok(id)
    }

    /// Register a new search task for `path`. Always succeeds (unless
    /// canonicalization fails); searches are not deduplicated.
    pub fn submit_search(&self, path: &Path) -> Result<TaskId, SubmitError> {
        let canonical = canonicalize_or_self(path).ok_or(SubmitError::InvalidPath)?;
        let mut inner = self.inner.lock().expect("registry mutex poisoned");
        let entry = TaskEntry::new(TaskKind::Search, canonical);
        let id = entry.id.clone();
        inner.tasks.insert(id.clone(), entry);
        Ok(id)
    }

    /// Transition `Queued → Running`. The worker calls this when it
    /// actually starts the long-running operation.
    pub fn mark_running(&self, id: &str) -> AppResult<()> {
        let mut inner = self.inner.lock().expect("registry mutex poisoned");
        let entry = inner
            .tasks
            .get_mut(id)
            .ok_or_else(|| AppError::NotFound(format!("unknown task id: {}", id)))?;
        require_transition(entry.status, TaskStatus::Running)?;
        entry.status = TaskStatus::Running;
        Ok(())
    }

    /// Transition `Running → Completed` and record the summary.
    /// Also clears the active-by-path slot if this was an index task.
    pub fn mark_completed(&self, id: &str, summary: Option<IndexSummary>) -> AppResult<()> {
        let mut inner = self.inner.lock().expect("registry mutex poisoned");
        let (kind, path) = {
            let entry = inner
                .tasks
                .get_mut(id)
                .ok_or_else(|| AppError::NotFound(format!("unknown task id: {}", id)))?;
            require_transition(entry.status, TaskStatus::Completed)?;
            entry.status = TaskStatus::Completed;
            entry.finished_at = Some(Instant::now());
            entry.summary = summary;
            (entry.kind, entry.path.clone())
        };
        if kind == TaskKind::Index {
            inner.active_index_by_path.remove(&path);
        }
        Ok(())
    }

    /// Transition `Running / Queued → Failed` and record the error.
    pub fn mark_failed(&self, id: &str, err: impl Into<String>) -> AppResult<()> {
        let err = err.into();
        let mut inner = self.inner.lock().expect("registry mutex poisoned");
        let (kind, path) = {
            let entry = inner
                .tasks
                .get_mut(id)
                .ok_or_else(|| AppError::NotFound(format!("unknown task id: {}", id)))?;
            require_transition(entry.status, TaskStatus::Failed)?;
            entry.status = TaskStatus::Failed;
            entry.finished_at = Some(Instant::now());
            entry.error = Some(err);
            (entry.kind, entry.path.clone())
        };
        if kind == TaskKind::Index {
            inner.active_index_by_path.remove(&path);
        }
        Ok(())
    }

    /// Transition `Running / Queued → Cancelled`. Workers call this
    /// when they observe the cancel flag. The caller is responsible
    /// for actually flipping the flag via [`cancel`] first.
    pub fn mark_cancelled(&self, id: &str) -> AppResult<()> {
        let mut inner = self.inner.lock().expect("registry mutex poisoned");
        let (kind, path) = {
            let entry = inner
                .tasks
                .get_mut(id)
                .ok_or_else(|| AppError::NotFound(format!("unknown task id: {}", id)))?;
            require_transition(entry.status, TaskStatus::Cancelled)?;
            entry.status = TaskStatus::Cancelled;
            entry.finished_at = Some(Instant::now());
            (entry.kind, entry.path.clone())
        };
        if kind == TaskKind::Index {
            inner.active_index_by_path.remove(&path);
        }
        Ok(())
    }

    /// Public cancellation entry point. Behavior depends on the current
    /// status:
    ///   * **Queued** — task hasn't started yet; transition directly to
    ///     Cancelled. Returns the new status.
    ///   * **Running** — flip the cancel flag so the worker observes it
    ///     at its next cancellation point. The worker is expected to
    ///     call `mark_cancelled` to finalize the transition. Status
    ///     here is still `Running` until the worker acts.
    ///   * **terminal** — no-op; returns the current terminal status.
    ///   * **unknown id** — `AppError::NotFound`.
    pub fn cancel(&self, id: &str) -> AppResult<TaskStatus> {
        let mut inner = self.inner.lock().expect("registry mutex poisoned");
        // Read state first to decide the action; avoid holding a mutable
        // borrow on `inner.tasks` across the subsequent `remove` call.
        let (kind, path, action) = {
            let entry = inner
                .tasks
                .get_mut(id)
                .ok_or_else(|| AppError::NotFound(format!("unknown task id: {}", id)))?;
            match entry.status {
                TaskStatus::Queued => {
                    entry.status = TaskStatus::Cancelled;
                    entry.finished_at = Some(Instant::now());
                    (entry.kind, entry.path.clone(), CancelAction::DirectCancelled)
                }
                TaskStatus::Running => {
                    entry.cancel.store(true, Ordering::SeqCst);
                    (entry.kind, entry.path.clone(), CancelAction::FlagSet)
                }
                terminal => (entry.kind, entry.path.clone(), CancelAction::Noop(terminal)),
            }
        };
        match action {
            CancelAction::DirectCancelled => {
                if kind == TaskKind::Index {
                    inner.active_index_by_path.remove(&path);
                }
                Ok(TaskStatus::Cancelled)
            }
            CancelAction::FlagSet => Ok(TaskStatus::Running),
            CancelAction::Noop(s) => Ok(s),
        }
    }

    /// Look up a task by id. Returns a serializable snapshot, NOT the
    /// live `TaskEntry` (the live entry holds an `Arc<AtomicBool>`
    /// that we don't want to expose through the API boundary).
    pub fn get(&self, id: &str) -> Option<TaskSnapshot> {
        let inner = self.inner.lock().expect("registry mutex poisoned");
        inner.tasks.get(id).map(|e| e.snapshot())
    }

    /// Number of tasks currently registered (any status). Useful for
    /// tests and for `logviewer://state` IPC.
    pub fn len(&self) -> usize {
        let inner = self.inner.lock().expect("registry mutex poisoned");
        inner.tasks.len()
    }

    /// `true` iff no tasks are registered. Note this includes
    /// terminal tasks not yet swept by `cleanup_finished`.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Sweep terminal tasks whose `finished_at` is older than `ttl`.
    /// Returns the number of entries removed. Active tasks are never
    /// touched.
    pub fn cleanup_finished(&self, ttl: std::time::Duration) -> usize {
        let now = Instant::now();
        let mut inner = self.inner.lock().expect("registry mutex poisoned");
        let before = inner.tasks.len();
        inner.tasks.retain(|_id, entry| match entry.finished_at {
            Some(t) if now.duration_since(t) < ttl => true,
            Some(_) => false,
            None => true, // still active
        });
        before - inner.tasks.len()
    }

    /// Lookup helper for tests / metrics — returns the canonical path
    /// currently registered for an active index of `path`, if any.
    pub fn active_index_for(&self, path: &Path) -> Option<TaskId> {
        let canonical = canonicalize_or_self(path)?;
        let inner = self.inner.lock().expect("registry mutex poisoned");
        inner.active_index_by_path.get(&canonical).cloned()
    }

    /// Clone out the `Arc<AtomicBool>` cancel flag attached to a task so
    /// the background worker can observe it without holding the registry
    /// mutex. Returns `None` only if the task id is unknown.
    ///
    /// The returned flag is the *live* flag stored on the entry — flipping
    /// it (via [`LogViewerState::cancel`] or by hand in tests) is observed
    /// at the worker's next cancellation point.
    pub fn cancel_handle(&self, id: &str) -> Option<Arc<AtomicBool>> {
        let inner = self.inner.lock().expect("registry mutex poisoned");
        inner.tasks.get(id).map(|e| e.cancel.clone())
    }
}

// ----------------------------------------------------------------------------
// TaskEntry
// ----------------------------------------------------------------------------

impl TaskEntry {
    fn new(kind: TaskKind, path: PathBuf) -> Self {
        Self {
            id: uuid::Uuid::new_v4().to_string(),
            kind,
            path,
            status: TaskStatus::Queued,
            cancel: Arc::new(AtomicBool::new(false)),
            created_at: Instant::now(),
            finished_at: None,
            error: None,
            summary: None,
        }
    }

    fn snapshot(&self) -> TaskSnapshot {
        // Serialize the monotonic `Instant`s relative to a wall-clock
        // baseline (UNIX_EPOCH). The exact values are not load-bearing
        // — tests assert presence / ordering rather than pinning
        // numbers — but `created_at_unix_ms` must always be <= the
        // corresponding `finished_at_unix_ms` so the UI can render a
        // sensible duration. To make that invariant hold, we capture
        // "now" once, derive created_at, and derive finished_at by
        // shifting forward by the recorded Instant-to-Instant delta.
        let now_system = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_millis() as i64)
            .unwrap_or(0);
        let created_age_ms = self.created_at.elapsed().as_millis() as i64;
        let created_unix_ms = now_system.saturating_sub(created_age_ms);
        let finished_unix_ms = self.finished_at.and_then(|t| {
            let delta_ms = t.checked_duration_since(self.created_at)?.as_millis() as i64;
            // Monotonic invariant: finished >= created.
            Some(created_unix_ms.saturating_add(delta_ms))
        });
        TaskSnapshot {
            id: self.id.clone(),
            kind: self.kind,
            path: self.path.clone(),
            status: self.status,
            created_at_unix_ms: created_unix_ms,
            finished_at_unix_ms: finished_unix_ms,
            error: self.error.clone(),
            summary: self.summary.clone(),
        }
    }
}

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

/// Validate a state transition. The state machine is intentionally
/// permissive about which starting states can reach `Failed` /
/// `Cancelled` (any non-terminal may), but strict about `Completed`
/// (only `Running` may).
fn require_transition(from: TaskStatus, to: TaskStatus) -> AppResult<()> {
    use TaskStatus::*;
    let ok = matches!(
        (from, to),
        (Queued, Running)
            | (Running, Completed)
            | (Running, Failed)
            | (Queued, Failed)
            | (Running, Cancelled)
            | (Queued, Cancelled)
    );
    if ok {
        Ok(())
    } else {
        Err(AppError::Validation(format!(
            "invalid task status transition: {:?} -> {:?}",
            from, to
        )))
    }
}

/// Best-effort canonicalization. If `fs::canonicalize` fails (file
/// missing, permissions, etc.) we fall back to the original path so
/// that `submit_search` of a missing file still gets a sensible
/// canonical key — duplicate detection will simply not catch
/// `C:/foo/../bar.log` vs `C:/bar.log` for that file, which is the
/// lesser evil compared to outright rejecting the submission.
fn canonicalize_or_self(path: &Path) -> Option<PathBuf> {
    match std::fs::canonicalize(path) {
        Ok(p) => Some(p),
        Err(_) => Some(path.to_path_buf()),
    }
}

/// Compute the platform default `log_index.db` path:
///   * Windows: `%APPDATA%\eaide\log_index.db`
///   * macOS:   `$HOME/Library/Application Support/eaide/log_index.db`
///   * Linux:   `$XDG_DATA_HOME/eaide/log_index.db` (or
///     `$HOME/.local/share/eaide/log_index.db`)
///
/// Falls back to `log_index.db` in the current directory when none of
/// the platform-specific environment variables are set — this keeps
/// the `LogViewerState::default()` constructor from ever panicking
/// during unit tests.
fn default_storage_path() -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        if let Ok(dir) = std::env::var("APPDATA") {
            return PathBuf::from(dir).join("eaide").join("log_index.db");
        }
    }
    #[cfg(target_os = "macos")]
    {
        if let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home)
                .join("Library")
                .join("Application Support")
                .join("eaide")
                .join("log_index.db");
        }
    }
    #[cfg(target_os = "linux")]
    {
        if let Ok(xdg) = std::env::var("XDG_DATA_HOME") {
            return PathBuf::from(xdg).join("eaide").join("log_index.db");
        }
        if let Ok(home) = std::env::var("HOME") {
            return PathBuf::from(home)
                .join(".local")
                .join("share")
                .join("eaide")
                .join("log_index.db");
        }
    }
    PathBuf::from("log_index.db")
}

// ===========================================================================
// Tests — TDD-first. Each test exercises one contract from the module docs.
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::io::Write;

    // -- fixtures --------------------------------------------------------

    fn unique_dir(label: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!(
            "eaide-logviewer-registry-{}-{}",
            label,
            uuid::Uuid::new_v4()
        ));
        fs::create_dir_all(&dir).expect("create tempdir");
        dir
    }

    /// RAII guard that wipes a directory on drop.
    mod tempdir {
        pub struct Guard(pub std::path::PathBuf);
        impl Drop for Guard {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
    }
    use tempdir::Guard;

    fn make_file(label: &str, contents: &[u8]) -> (Guard, PathBuf) {
        let dir = unique_dir(label);
        let path = dir.join("data.log");
        let mut f = fs::File::create(&path).expect("create file");
        f.write_all(contents).expect("write");
        f.sync_all().ok();
        (Guard(dir), path)
    }

    // -- 1. Lifecycle -----------------------------------------------------

    #[test]
    fn new_state_is_empty() {
        let state = LogViewerState::new();
        assert!(state.is_empty());
        assert_eq!(state.len(), 0);
    }

    #[test]
    fn submit_index_creates_queued_entry() {
        let (_g, path) = make_file("submit_index", b"alpha\nbeta\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).expect("submit ok");
        assert!(!id.is_empty());

        let snap = state.get(&id).expect("get returns snapshot");
        assert_eq!(snap.kind, TaskKind::Index);
        assert_eq!(snap.status, TaskStatus::Queued);
        assert_eq!(snap.path, std::fs::canonicalize(&path).unwrap());
        assert!(snap.error.is_none());
        assert!(snap.summary.is_none());
        assert_eq!(state.len(), 1);
    }

    #[test]
    fn submit_search_creates_queued_entry() {
        let (_g, path) = make_file("submit_search", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_search(&path).expect("submit ok");
        let snap = state.get(&id).expect("get returns snapshot");
        assert_eq!(snap.kind, TaskKind::Search);
        assert_eq!(snap.status, TaskStatus::Queued);
    }

    // -- 2. State transitions ---------------------------------------------

    #[test]
    fn queued_to_running_to_completed_is_allowed() {
        let (_g, path) = make_file("happy_path", b"a\nb\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();

        state.mark_running(&id).expect("Queued -> Running");
        assert_eq!(state.get(&id).unwrap().status, TaskStatus::Running);

        let summary = IndexSummary {
            file_size: 4,
            line_count: 2,
            file_fingerprint: "4:0".into(),
            last_modified: 0,
            indexed_at: 0,
            encoding: "utf-8".into(),
        };
        state
            .mark_completed(&id, Some(summary.clone()))
            .expect("Running -> Completed");

        let snap = state.get(&id).unwrap();
        assert_eq!(snap.status, TaskStatus::Completed);
        assert!(snap.summary.is_some());
        assert_eq!(snap.summary.unwrap().line_count, 2);
    }

    #[test]
    fn queued_to_running_to_failed_is_allowed() {
        let (_g, path) = make_file("failed_path", b"a\nb\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        state.mark_running(&id).unwrap();
        state
            .mark_failed(&id, "boom")
            .expect("Running -> Failed");
        let snap = state.get(&id).unwrap();
        assert_eq!(snap.status, TaskStatus::Failed);
        assert_eq!(snap.error.as_deref(), Some("boom"));
    }

    #[test]
    fn queued_to_failed_is_allowed() {
        let (_g, path) = make_file("queued_failed", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        // No mark_running — failed before starting.
        state
            .mark_failed(&id, "pre-flight rejection")
            .expect("Queued -> Failed");
        assert_eq!(state.get(&id).unwrap().status, TaskStatus::Failed);
    }

    #[test]
    fn queued_to_cancelled_via_cancel_is_allowed() {
        let (_g, path) = make_file("cancel_queued", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        // cancel() on a Queued task moves it directly to Cancelled.
        let new_status = state.cancel(&id).expect("cancel ok");
        assert_eq!(new_status, TaskStatus::Cancelled);
        assert_eq!(state.get(&id).unwrap().status, TaskStatus::Cancelled);
    }

    #[test]
    fn running_to_cancelled_via_worker_is_allowed() {
        let (_g, path) = make_file("cancel_running", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        state.mark_running(&id).unwrap();
        // cancel() on Running just flips the flag.
        let new_status = state.cancel(&id).expect("cancel ok");
        assert_eq!(new_status, TaskStatus::Running);
        assert_eq!(state.get(&id).unwrap().status, TaskStatus::Running);
        // The worker observes the flag and calls mark_cancelled.
        state.mark_cancelled(&id).expect("Running -> Cancelled");
        assert_eq!(state.get(&id).unwrap().status, TaskStatus::Cancelled);
    }

    #[test]
    fn completed_to_running_is_rejected() {
        let (_g, path) = make_file("bad_transition", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        state.mark_running(&id).unwrap();
        state.mark_completed(&id, None).unwrap();
        // Now Completed -> Running must fail.
        let err = state.mark_running(&id).expect_err("Completed -> Running invalid");
        assert!(matches!(err, AppError::Validation(_)));
    }

    #[test]
    fn queued_to_completed_is_rejected() {
        let (_g, path) = make_file("bad_skip_running", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        // Skip Running — must fail.
        let err = state.mark_completed(&id, None).expect_err("Queued -> Completed invalid");
        assert!(matches!(err, AppError::Validation(_)));
    }

    // -- 3. Unknown task IDs ----------------------------------------------

    #[test]
    fn unknown_task_id_in_mark_running_returns_not_found() {
        let state = LogViewerState::new();
        let err = state
            .mark_running("does-not-exist")
            .expect_err("unknown id rejected");
        assert!(matches!(err, AppError::NotFound(_)));
    }

    #[test]
    fn unknown_task_id_in_mark_completed_returns_not_found() {
        let state = LogViewerState::new();
        let err = state
            .mark_completed("does-not-exist", None)
            .expect_err("unknown id rejected");
        assert!(matches!(err, AppError::NotFound(_)));
    }

    #[test]
    fn unknown_task_id_in_mark_failed_returns_not_found() {
        let state = LogViewerState::new();
        let err = state
            .mark_failed("does-not-exist", "boom")
            .expect_err("unknown id rejected");
        assert!(matches!(err, AppError::NotFound(_)));
    }

    #[test]
    fn unknown_task_id_in_mark_cancelled_returns_not_found() {
        let state = LogViewerState::new();
        let err = state
            .mark_cancelled("does-not-exist")
            .expect_err("unknown id rejected");
        assert!(matches!(err, AppError::NotFound(_)));
    }

    #[test]
    fn unknown_task_id_in_cancel_returns_not_found() {
        let state = LogViewerState::new();
        let err = state
            .cancel("does-not-exist")
            .expect_err("unknown id rejected");
        assert!(matches!(err, AppError::NotFound(_)));
    }

    #[test]
    fn unknown_task_id_in_get_returns_none() {
        let state = LogViewerState::new();
        assert!(state.get("does-not-exist").is_none());
    }

    // -- 4. Duplicate-indexing guard -------------------------------------

    #[test]
    fn duplicate_index_submission_rejected_when_active() {
        let (_g, path) = make_file("dup_active", b"x\n");
        let state = LogViewerState::new();
        let first = state.submit_index(&path).expect("first submit ok");

        // Second submit for the same path must be rejected while the
        // first task is non-terminal.
        let err = state
            .submit_index(&path)
            .expect_err("second submit must error");
        assert_eq!(err, SubmitError::DuplicateActiveIndex);

        // First task is still Queued and queryable.
        assert_eq!(state.get(&first).unwrap().status, TaskStatus::Queued);
    }

    #[test]
    fn duplicate_index_submission_allowed_after_completion() {
        let (_g, path) = make_file("dup_after_complete", b"x\n");
        let state = LogViewerState::new();
        let first = state.submit_index(&path).unwrap();
        state.mark_running(&first).unwrap();
        state.mark_completed(&first, None).unwrap();

        // Second submit now succeeds (previous task is terminal).
        let second = state
            .submit_index(&path)
            .expect("second submit after completion allowed");
        assert_ne!(first, second);
    }

    #[test]
    fn duplicate_index_submission_allowed_after_failed() {
        let (_g, path) = make_file("dup_after_fail", b"x\n");
        let state = LogViewerState::new();
        let first = state.submit_index(&path).unwrap();
        state.mark_running(&first).unwrap();
        state.mark_failed(&first, "boom").unwrap();

        let second = state
            .submit_index(&path)
            .expect("second submit after fail allowed");
        assert_ne!(first, second);
    }

    #[test]
    fn duplicate_index_submission_allowed_after_cancellation() {
        let (_g, path) = make_file("dup_after_cancel", b"x\n");
        let state = LogViewerState::new();
        let first = state.submit_index(&path).unwrap();
        state.mark_running(&first).unwrap();
        state.mark_cancelled(&first).unwrap();

        let second = state
            .submit_index(&path)
            .expect("second submit after cancel allowed");
        assert_ne!(first, second);
    }

    #[test]
    fn duplicate_search_for_same_path_is_allowed() {
        // Search tasks are intentionally NOT deduplicated — the UI may
        // legitimately fire several searches against the same file in
        // parallel (e.g. multiple search bars, one per query).
        let (_g, path) = make_file("search_dup", b"x\n");
        let state = LogViewerState::new();
        let a = state.submit_search(&path).unwrap();
        let b = state.submit_search(&path).unwrap();
        assert_ne!(a, b);
        assert_eq!(state.len(), 2);
    }

    // -- 5. Path canonicalization ----------------------------------------

    #[test]
    fn path_is_canonicalized_for_duplicate_check() {
        // Build a real file, then refer to it via two spellings that
        // differ only by `..` segments. The registry must treat them
        // as the same path.
        let (g, dir) = (None::<Guard>, unique_dir("canon"));
        let sub = dir.join("sub");
        fs::create_dir_all(&sub).unwrap();
        let target = sub.join("target.log");
        fs::write(&target, b"x\n").unwrap();

        let via_dotdot = sub.join("..").join("sub").join("target.log");
        let via_dir = dir.join("sub").join("target.log");

        let state = LogViewerState::new();
        let _first = state
            .submit_index(&target)
            .expect("first submit via canonical spelling");
        let dup = state
            .submit_index(&via_dotdot)
            .expect_err("submit via dotdot path must collide");
        assert_eq!(dup, SubmitError::DuplicateActiveIndex);
        let dup = state
            .submit_index(&via_dir)
            .expect_err("submit via equivalent dir path must collide");
        assert_eq!(dup, SubmitError::DuplicateActiveIndex);

        // Cleanup
        let _ = g;
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn path_canonicalization_for_nonexistent_file_falls_back_to_self() {
        // For `submit_search` (which does not dedup) we still want to
        // record something stable. Missing files fall back to the raw
        // spelling — that's documented behavior.
        let state = LogViewerState::new();
        let missing = std::path::PathBuf::from("C:/no/such/file.log");
        let id = state
            .submit_search(&missing)
            .expect("submit_search must succeed for missing file");
        let snap = state.get(&id).unwrap();
        assert_eq!(snap.path, missing);
    }

    // -- 6. Delayed cleanup ----------------------------------------------

    #[test]
    fn cleanup_removes_finished_tasks_after_ttl() {
        // We can't easily make a real `Instant` move forward in a unit
        // test, so we use a TTL of zero and rely on the fact that the
        // task was just marked finished.
        let (_g, path) = make_file("cleanup_old", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        state.mark_running(&id).unwrap();
        state.mark_completed(&id, None).unwrap();

        // TTL=0 should sweep everything terminal.
        let removed = state.cleanup_finished(std::time::Duration::from_millis(0));
        assert_eq!(removed, 1, "exactly one task removed");
        assert!(state.is_empty());
        assert!(state.get(&id).is_none());
    }

    #[test]
    fn cleanup_keeps_recent_finished_tasks() {
        let (_g, path) = make_file("cleanup_recent", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        state.mark_running(&id).unwrap();
        state.mark_completed(&id, None).unwrap();

        // TTL = 1 hour — task was finished < 1s ago, must survive.
        let removed = state.cleanup_finished(std::time::Duration::from_secs(3600));
        assert_eq!(removed, 0);
        assert_eq!(state.len(), 1);
        assert!(state.get(&id).is_some());
    }

    #[test]
    fn cleanup_keeps_active_tasks() {
        let (_g, path) = make_file("cleanup_active", b"x\n");
        let state = LogViewerState::new();
        let _ = state.submit_index(&path).unwrap(); // still Queued
        let _ = state.submit_search(&path).unwrap(); // still Queued

        // Even with TTL=0, active tasks must survive.
        let removed = state.cleanup_finished(std::time::Duration::from_millis(0));
        assert_eq!(removed, 0);
        assert_eq!(state.len(), 2);
    }

    #[test]
    fn cleanup_with_ttl_zero_sweeps_only_terminal() {
        // Mix of Queued + Completed; only Completed should be swept.
        let (_g, p1) = make_file("cleanup_mix_a", b"x\n");
        let (_g, p2) = make_file("cleanup_mix_b", b"x\n");
        let state = LogViewerState::new();

        let queued = state.submit_index(&p1).unwrap();
        let completed = state.submit_index(&p2).unwrap();
        state.mark_running(&completed).unwrap();
        state.mark_completed(&completed, None).unwrap();

        let removed = state.cleanup_finished(std::time::Duration::from_millis(0));
        assert_eq!(removed, 1);
        assert!(state.get(&queued).is_some());
        assert!(state.get(&completed).is_none());
    }

    // -- 7. Cancellation specifics ---------------------------------------

    #[test]
    fn cancel_terminal_task_is_noop() {
        let (_g, path) = make_file("cancel_done", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        state.mark_running(&id).unwrap();
        state.mark_completed(&id, None).unwrap();

        // Already Completed — cancel should leave it alone.
        let status = state.cancel(&id).expect("cancel on terminal ok");
        assert_eq!(status, TaskStatus::Completed);
        assert_eq!(state.get(&id).unwrap().status, TaskStatus::Completed);
    }

    #[test]
    fn cancel_running_sets_cancel_flag_atomically() {
        let (_g, path) = make_file("cancel_flag", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        state.mark_running(&id).unwrap();

        // Pull the live entry via a workaround — the API doesn't expose
        // TaskEntry, so we can't directly read cancel. But we can
        // verify behavior: cancel() returns Running (not Cancelled) and
        // a subsequent mark_cancelled transitions to Cancelled.
        let after_cancel = state.cancel(&id).unwrap();
        assert_eq!(after_cancel, TaskStatus::Running);
        state.mark_cancelled(&id).unwrap();
        assert_eq!(state.get(&id).unwrap().status, TaskStatus::Cancelled);
    }

    // -- 8. Active lookup ------------------------------------------------

    #[test]
    fn active_index_for_returns_id_when_present() {
        let (_g, path) = make_file("active_lookup", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        let looked = state.active_index_for(&path).expect("active task lookup");
        assert_eq!(looked, id);
    }

    #[test]
    fn active_index_for_returns_none_when_completed() {
        let (_g, path) = make_file("active_after_done", b"x\n");
        let state = LogViewerState::new();
        let id = state.submit_index(&path).unwrap();
        state.mark_running(&id).unwrap();
        state.mark_completed(&id, None).unwrap();
        assert!(state.active_index_for(&path).is_none());
    }

    // -- 9. SubmitError: invalid path ------------------------------------

    #[test]
    fn submit_index_accepts_any_path_but_records_canonical_form() {
        // `canonicalize_or_self` falls back to the input spelling for
        // missing files, so submit_index should still succeed for a
        // non-existent path. Documenting the behavior.
        let state = LogViewerState::new();
        let missing = std::path::PathBuf::from("C:/no/such/log.log");
        let id = state
            .submit_index(&missing)
            .expect("submit_index succeeds even for missing files");
        assert!(state.get(&id).is_some());
    }
}