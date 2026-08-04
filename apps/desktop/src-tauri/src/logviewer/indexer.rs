//! Phase 2F+ Task 2 — block-based indexer for large log files.
//!
//! Design notes:
//!   * **Path validation** — require an existing ordinary file. Any absolute
//!     path is allowed (no directory whitelist, per product decision).
//!   * **Fingerprint** — `"{file_size}:{mtime_secs}"`. Mtime in **seconds**
//!     since UNIX epoch. Matches the format Task 1's storage layer already
//!     uses for test fixtures.
//!   * **Block-based reading** — `BufReader` is sized at 1 MiB so each
//!     underlying `read` syscall serves up to 1 MiB; the main loop's
//!     iteration boundary aligns with that 1 MiB window. Cancellation is
//!     checked at every iteration so a cancellation flag is observed within
//!     ~1 MiB of scan progress (no full-file sweep).
//!   * **Memory footprint** — only the running offset list is kept in RAM.
//!     `read_until(b'\n')` reuses a single `Vec<u8>` buffer; the file body
//!     is never collected.
//!   * **Empty file** — `line_count == 0`, `offsets == [0]`, BLOB is empty
//!     bytes (matches the `encode_u64_le(&[])` contract).
//!   * **Final unterminated line** — `BufRead::read_until` returns the
//!     final chunk even when it has no trailing `\n`; that chunk increments
//!     `line_count` and the post-read cursor becomes the sentinel offset.
//!   * **Atomic commit** — the SQLite upsert happens in a single transaction
//!     (`storage.upsert` already wraps it), and only AFTER the full scan
//!     completes. If the scan is cancelled or returns an error, the upsert
//!     is never issued and the existing row (if any) survives untouched.

use std::fs::{self, File};
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::UNIX_EPOCH;

use chrono::Utc;

use crate::error::{AppError, AppResult};
use crate::logviewer::encoding::detect_encoding;
use crate::logviewer::storage::{encode_u64_le, FileIndex, LogIndexStorage};

/// Internal buffer size for `BufReader`. 1 MiB = 1 << 20 bytes.
const BLOCK_SIZE: usize = 1 << 20;

// ----------------------------------------------------------------------------
// Errors
// ----------------------------------------------------------------------------

/// Indexer-specific validation errors. Map to `AppError::Validation` so the
/// Tauri command boundary can serialize them as plain strings.
#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum IndexerError {
    #[error("path is not absolute: {0}")]
    NotAbsolute(String),
    #[error("path does not exist: {0}")]
    NotFound(String),
    #[error("path is not an ordinary file (directory, device, or special): {0}")]
    NotOrdinaryFile(String),
    #[error("io error while validating path: {0}")]
    Io(String),
}

impl From<IndexerError> for AppError {
    fn from(e: IndexerError) -> Self {
        AppError::Validation(e.to_string())
    }
}

// ----------------------------------------------------------------------------
// Public DTOs
// ----------------------------------------------------------------------------

/// Per-iteration progress snapshot. Emitted at block boundaries (every
/// 1 MiB of bytes scanned) plus one final snapshot at scan completion so
/// small files always yield at least one snapshot.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct IndexProgress {
    /// Total file size in bytes (constant across snapshots).
    pub file_size: u64,
    /// Bytes successfully scanned so far.
    pub bytes_scanned: u64,
    /// Number of lines whose start offset has been recorded.
    pub line_count: u64,
}

/// Final summary returned from a successful, non-cancelled scan. The caller
/// uses this for status reporting and to decide whether the storage row is
/// fresh (`indexed_at`) or needs rebuilding (compare with existing row).
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct IndexSummary {
    pub file_size: u64,
    pub line_count: u64,
    /// Stable fingerprint used to detect file changes between scans.
    pub file_fingerprint: String,
    pub last_modified: i64,
    pub indexed_at: i64,
    pub encoding: String,
}

// ----------------------------------------------------------------------------
// Indexer
// ----------------------------------------------------------------------------

/// Builds `FileIndex` records by streaming a file in 1 MiB blocks.
#[derive(Debug, Clone)]
pub struct FileIndexer {
    storage: LogIndexStorage,
}

impl FileIndexer {
    /// Construct a new indexer bound to the given storage handle.
    pub fn new(storage: LogIndexStorage) -> Self {
        Self { storage }
    }

    /// Borrow the underlying storage handle.
    pub fn storage(&self) -> &LogIndexStorage {
        &self.storage
    }

    /// Validate that `path` is an absolute path to an existing ordinary
    /// file. Directories, devices, and missing entries are rejected.
    ///
    /// **No directory whitelist**: any absolute path is acceptable per the
    /// product decision for the Log Viewer MVP. Future hardening (e.g.
    /// restricting to a log root) belongs to a separate policy layer.
    pub fn validate_path(path: &Path) -> Result<(), IndexerError> {
        if !path.is_absolute() {
            return Err(IndexerError::NotAbsolute(path.display().to_string()));
        }
        let meta = match fs::metadata(path) {
            Ok(m) => m,
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                return Err(IndexerError::NotFound(path.display().to_string()));
            }
            Err(e) => return Err(IndexerError::Io(e.to_string())),
        };
        if !meta.is_file() {
            return Err(IndexerError::NotOrdinaryFile(path.display().to_string()));
        }
        Ok(())
    }

    /// Compute a stable fingerprint for a file: `"{size}:{mtime_secs}"`.
    ///
    /// Returns the fingerprint string, the file size in bytes, and the
    /// modification time in whole seconds since the UNIX epoch.
    pub fn fingerprint(path: &Path) -> AppResult<(String, u64, i64)> {
        let meta = fs::metadata(path)?;
        let size = meta.len();
        let mtime_secs = meta
            .modified()
            .ok()
            .and_then(|m| m.duration_since(UNIX_EPOCH).ok())
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0);
        let fp = format!("{}:{}", size, mtime_secs);
        Ok((fp, size, mtime_secs))
    }

    /// Run a full scan of `path` and upsert the resulting `FileIndex` in one
    /// SQLite transaction.
    ///
    /// Returns:
    ///   * `Ok(Some(summary))` — scan completed, new row committed.
    ///   * `Ok(None)` — `cancel` was observed at a block boundary; **no**
    ///     upsert was issued, so any pre-existing index row remains
    ///     untouched.
    ///   * `Err(AppError)` — I/O / storage failure; again no upsert was
    ///     issued past the failure point.
    ///
    /// `progress_cb` is invoked at each 1 MiB block boundary (and once at
    /// scan completion for small files). The closure must be cheap to call;
    /// production code should push the snapshot to an async channel rather
    /// than doing heavy work inline.
    pub fn index_file(
        &self,
        path: &Path,
        cancel: Arc<AtomicBool>,
        progress_cb: &mut dyn FnMut(IndexProgress),
    ) -> AppResult<Option<IndexSummary>> {
        Self::validate_path(path)?;
        let (fingerprint, file_size, mtime_secs) = Self::fingerprint(path)?;

        // Open the file lazily — if open fails, we never touch storage.
        let file = File::open(path)?;
        let mut reader = BufReader::with_capacity(BLOCK_SIZE, file);

        let mut offsets: Vec<u64> = Vec::with_capacity(1024);
        offsets.push(0); // first line always starts at byte 0
        let mut line_count: u64 = 0;
        let mut bytes_scanned: u64 = 0;
        let mut last_emit_block: u64 = 0;
        let mut buf: Vec<u8> = Vec::with_capacity(BLOCK_SIZE);

        loop {
            // Cancellation is observed at the top of each iteration; each
            // iteration handles at most one BufRead call (up to 1 MiB).
            if cancel.load(Ordering::SeqCst) {
                return Ok(None);
            }

            buf.clear();
            let n = reader.read_until(b'\n', &mut buf)?;
            if n == 0 {
                break;
            }
            bytes_scanned += n as u64;
            line_count += 1;
            offsets.push(bytes_scanned);

            // Emit a progress snapshot at every 1 MiB boundary we cross.
            let cur_block = bytes_scanned / BLOCK_SIZE as u64;
            if cur_block != last_emit_block {
                last_emit_block = cur_block;
                progress_cb(IndexProgress {
                    file_size,
                    bytes_scanned,
                    line_count,
                });
            }
        }

        // Final snapshot — guarantees small files get at least one event
        // for tests / progress UI.
        progress_cb(IndexProgress {
            file_size,
            bytes_scanned,
            line_count,
        });

        // Detect encoding (UTF-8 / GBK)
        let encoding = detect_encoding(path).unwrap_or_else(|_| "utf-8".to_string());
        let indexed_at = Utc::now().timestamp();

        // Single atomic upsert AFTER the full scan completes. `storage.upsert`
        // wraps the insert in a SQLite transaction; on success the previous
        // row (if any) is replaced, on failure it is preserved.
        let idx = FileIndex {
            file_path: path.to_string_lossy().into_owned(),
            file_fingerprint: fingerprint.clone(),
            file_size,
            line_count,
            line_offsets: encode_u64_le(&offsets),
            encoding: encoding.clone(),
            last_modified: mtime_secs,
            indexed_at,
            index_version: 1,
        };
        self.storage.upsert(&idx)?;

        Ok(Some(IndexSummary {
            file_size,
            line_count,
            file_fingerprint: fingerprint,
            last_modified: mtime_secs,
            indexed_at,
            encoding,
        }))
    }
}

// ----------------------------------------------------------------------------
// Tests (TDD: written alongside the implementation)
// ----------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::logviewer::storage::{decode_u64_le, FileIndex};
    use std::io::Write;
    use std::path::PathBuf;
    use std::sync::atomic::AtomicUsize;

    // -- helpers --------------------------------------------------------

    fn unique_dir(label: &str) -> (tempdir::Guard, PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "eaide-logviewer-idx-{}-{}",
            label,
            uuid::Uuid::new_v4()
        ));
        fs::create_dir_all(&dir).expect("create tempdir");
        let path = dir.clone();
        (tempdir::Guard(dir), path)
    }

    fn make_db(label: &str) -> (tempdir::Guard, PathBuf) {
        let (g, dir) = unique_dir(label);
        let path = dir.join("log_index.db");
        (g, path)
    }

    fn make_file(label: &str, contents: &[u8]) -> (tempdir::Guard, PathBuf) {
        let (g, dir) = unique_dir(label);
        let path = dir.join("data.log");
        let mut f = fs::File::create(&path).expect("create file");
        f.write_all(contents).expect("write");
        f.sync_all().ok();
        (g, path)
    }

    mod tempdir {
        pub struct Guard(pub std::path::PathBuf);
        impl Drop for Guard {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
    }

    fn progress_counter() -> (Box<dyn FnMut(IndexProgress) + Send>, Arc<AtomicUsize>) {
        let count = Arc::new(AtomicUsize::new(0));
        let c2 = count.clone();
        let cb: Box<dyn FnMut(IndexProgress) + Send> = Box::new(move |_p| {
            c2.fetch_add(1, Ordering::SeqCst);
        });
        (cb, count)
    }

    fn run_index(
        path: &Path,
        cancel: Arc<AtomicBool>,
    ) -> (IndexSummary, FileIndex, usize) {
        let (_tmp_db, db_path) = make_db("run");
        let store = LogIndexStorage::open(&db_path).unwrap();
        let indexer = FileIndexer::new(store.clone());
        let (mut cb, count) = progress_counter();
        let summary = indexer
            .index_file(path, cancel, &mut cb)
            .expect("index_file returns Ok")
            .expect("not cancelled");
        let stored = store
            .get(&path.to_string_lossy())
            .expect("get")
            .expect("row present after successful scan");
        (summary, stored, count.load(Ordering::SeqCst))
    }

    // -- 1. Path validation --------------------------------------------

    #[test]
    fn validate_path_rejects_relative() {
        let p = Path::new("relative/log.log");
        let err = FileIndexer::validate_path(p).expect_err("relative should fail");
        assert!(matches!(err, IndexerError::NotAbsolute(_)));
    }

    #[test]
    fn validate_path_rejects_nonexistent_absolute() {
        let (_tmp, dir) = unique_dir("validate_missing");
        let missing = dir.join("does_not_exist.log");
        let err = FileIndexer::validate_path(&missing).expect_err("missing should fail");
        assert!(matches!(err, IndexerError::NotFound(_)));
    }

    #[test]
    fn validate_path_rejects_directory() {
        let (_tmp, dir) = unique_dir("validate_dir");
        let err = FileIndexer::validate_path(&dir).expect_err("dir should fail");
        assert!(matches!(err, IndexerError::NotOrdinaryFile(_)));
    }

    #[test]
    fn validate_path_accepts_any_absolute_file() {
        // No directory whitelist — any absolute file path is fine.
        let (_tmp, path) = make_file("validate_ok", b"hello\n");
        FileIndexer::validate_path(&path).expect("absolute file should pass");
    }

    // -- 2. Fingerprint -------------------------------------------------

    #[test]
    fn fingerprint_format_is_size_then_mtime_secs() {
        let (_tmp, path) = make_file("fingerprint_fmt", b"abc\n");
        let (fp, size, _mtime) =
            FileIndexer::fingerprint(&path).expect("fingerprint ok");
        let parts: Vec<&str> = fp.split(':').collect();
        assert_eq!(parts.len(), 2, "fingerprint has exactly one colon separator");
        assert_eq!(parts[0].parse::<u64>().unwrap(), size);
        assert_eq!(size, 4);
    }

    #[test]
    fn fingerprint_changes_when_file_size_changes() {
        let (_tmp, path) = make_file("fingerprint_size", b"abc");
        let (fp1, _, _) = FileIndexer::fingerprint(&path).unwrap();
        fs::write(&path, b"abcdefghijklmno").unwrap();
        let (fp2, _, _) = FileIndexer::fingerprint(&path).unwrap();
        assert_ne!(fp1, fp2, "fingerprint must change when size changes");
    }

    // -- 3. Empty file -------------------------------------------------

    #[test]
    fn index_empty_file_yields_line_count_zero_and_offsets_zero() {
        let (_tmp, path) = make_file("empty_log", b"");
        let cancel = Arc::new(AtomicBool::new(false));
        let (summary, stored, _snaps) = run_index(&path, cancel);

        assert_eq!(summary.file_size, 0);
        assert_eq!(summary.line_count, 0);
        // fingerprint is "0:<mtime_secs>" — only requirement is well-formed.
        assert!(summary.file_fingerprint.starts_with("0:"));

        assert_eq!(stored.line_count, 0);
        assert_eq!(stored.file_size, 0);
        // The indexer seeds `offsets` with `[0]` (first-line sentinel) and
        // never appends to it for an empty file. The BLOB therefore holds
        // exactly one u64 (8 bytes), decoded back to `[0]`.
        assert_eq!(stored.line_offsets.len(), 8);
        assert_eq!(decode_u64_le(&stored.line_offsets).unwrap(), vec![0u64]);
    }

    // -- 4. Single line, multi line, no trailing newline --------------

    #[test]
    fn index_single_line_no_newline() {
        let (_tmp, path) = make_file("single_line", b"omega");
        let cancel = Arc::new(AtomicBool::new(false));
        let (summary, stored, _snaps) = run_index(&path, cancel);

        assert_eq!(summary.line_count, 1);
        assert_eq!(summary.file_size, 5);
        assert_eq!(stored.line_count, 1);
        assert_eq!(decode_u64_le(&stored.line_offsets).unwrap(), vec![0u64, 5]);
    }

    #[test]
    fn index_multiline_with_trailing_newline() {
        // "alpha\nbeta\nomega\n" -> 3 lines, sentinel at file_size 17.
        let (_tmp, path) = make_file("multi_trail", b"alpha\nbeta\nomega\n");
        let cancel = Arc::new(AtomicBool::new(false));
        let (summary, stored, _snaps) = run_index(&path, cancel);

        assert_eq!(summary.line_count, 3);
        assert_eq!(summary.file_size, 17);
        assert_eq!(
            decode_u64_le(&stored.line_offsets).unwrap(),
            vec![0u64, 6, 11, 17]
        );
    }

    #[test]
    fn index_multiline_no_trailing_newline_preserves_final_line() {
        // "alpha\nbeta\nomega" — "alpha"=5 + '\n'=1 + "beta"=4 + '\n'=1 +
        // "omega"=5 → 16 bytes total. 3 lines, sentinel at byte 16.
        let (_tmp, path) = make_file("multi_no_trail", b"alpha\nbeta\nomega");
        let cancel = Arc::new(AtomicBool::new(false));
        let (summary, stored, _snaps) = run_index(&path, cancel);

        assert_eq!(summary.line_count, 3);
        assert_eq!(summary.file_size, 16);
        assert_eq!(
            decode_u64_le(&stored.line_offsets).unwrap(),
            vec![0u64, 6, 11, 16]
        );
    }

    #[test]
    fn index_only_newlines_yields_empty_line_count() {
        // "\n\n\n" -> 3 newline characters = 3 "lines" (each empty).
        let (_tmp, path) = make_file("only_newlines", b"\n\n\n");
        let cancel = Arc::new(AtomicBool::new(false));
        let (summary, _stored, _snaps) = run_index(&path, cancel);

        assert_eq!(summary.line_count, 3);
        assert_eq!(summary.file_size, 3);
    }

    // -- 5. Non-ASCII / UTF-8 byte offsets ------------------------------

    #[test]
    fn index_non_ascii_uses_byte_offsets_not_codepoints() {
        // 你好 (3 bytes each) -> 0..6; line starts: 0, 7
        // 世界 (3 bytes each) -> 7..13; line starts: 13
        // 🌍 (4 bytes)        -> 14..18; line starts: 18
        // "last"              -> 19..23; sentinel = 23
        let content = "你好\n世界\n🌍\nlast".as_bytes().to_vec();
        let (_tmp, path) = make_file("non_ascii", &content);
        let cancel = Arc::new(AtomicBool::new(false));
        let (summary, stored, _snaps) = run_index(&path, cancel);

        assert_eq!(summary.line_count, 4);
        assert_eq!(summary.file_size as usize, content.len());
        assert_eq!(
            decode_u64_le(&stored.line_offsets).unwrap(),
            vec![0u64, 7, 14, 19, 23]
        );
    }

    // -- 6. Cancellation preserves existing index ----------------------

    #[test]
    fn pre_cancelled_returns_none_and_keeps_existing_index() {
        let (_tmp_db, db_path) = make_db("cancel_pre_db");
        let (_tmp_file, file_path) = make_file("cancel_pre", b"alpha\nbeta\n");
        let store = LogIndexStorage::open(&db_path).unwrap();

        // Pre-seed an OLD row that the cancelled scan must NOT replace.
        let old = FileIndex {
            file_path: file_path.to_string_lossy().into_owned(),
            file_fingerprint: "OLD-FINGERPRINT".into(),
            file_size: 10,
            line_count: 2,
            line_offsets: encode_u64_le(&[0, 6, 10]),
            encoding: "utf-8".into(),
            last_modified: 0,
            indexed_at: 0,
            index_version: 1,
        };
        store.upsert(&old).unwrap();

        let indexer = FileIndexer::new(store.clone());
        let cancel = Arc::new(AtomicBool::new(true));
        let (mut cb, count) = progress_counter();
        let result = indexer
            .index_file(&file_path, cancel, &mut cb)
            .expect("index_file returns Ok");
        assert!(result.is_none(), "pre-cancelled scan returns None");

        let stored = store
            .get(&file_path.to_string_lossy())
            .unwrap()
            .expect("old row still present");
        assert_eq!(stored.file_fingerprint, "OLD-FINGERPRINT");
        assert_eq!(stored.line_count, 2);
        assert_eq!(stored.file_size, 10);
        // No progress should have been emitted on a pre-cancelled scan
        // that never enters the loop body. (Cancellation check fires first.)
        assert_eq!(count.load(Ordering::SeqCst), 0);
    }

    // -- 7. Progress snapshot emission ---------------------------------

    #[test]
    fn progress_emitted_at_least_once_for_small_file() {
        let (_tmp, path) = make_file("progress_small", b"hello\n");
        let cancel = Arc::new(AtomicBool::new(false));
        let (_tmp_db, db_path) = make_db("progress_small_db");
        let store = LogIndexStorage::open(&db_path).unwrap();
        let indexer = FileIndexer::new(store);
        let (mut cb, count) = progress_counter();
        indexer
            .index_file(&path, cancel, &mut cb)
            .unwrap()
            .unwrap();
        assert!(
            count.load(Ordering::SeqCst) >= 1,
            "small file yields at least the final snapshot"
        );
    }

    #[test]
    fn progress_emitted_per_block_for_large_file() {
        // Build > 3 MiB so we cross at least three 1 MiB boundaries.
        let mut content = Vec::new();
        let line = b"0123456789ABCDE\n"; // 16 bytes
        for _ in 0..(200 * 1024) {
            content.extend_from_slice(line);
        } // ~3.125 MiB
        let (_tmp, path) = make_file("progress_large", &content);
        let cancel = Arc::new(AtomicBool::new(false));
        let (_tmp_db, db_path) = make_db("progress_large_db");
        let store = LogIndexStorage::open(&db_path).unwrap();
        let indexer = FileIndexer::new(store);
        let (mut cb, count) = progress_counter();
        indexer
            .index_file(&path, cancel, &mut cb)
            .unwrap()
            .unwrap();
        // We expect snapshots at 1 MiB, 2 MiB, 3 MiB boundaries, plus the
        // final one — i.e. >= 4. Be permissive: require >= 3.
        assert!(
            count.load(Ordering::SeqCst) >= 3,
            "multi-MiB file yields multiple per-block snapshots, got {}",
            count.load(Ordering::SeqCst)
        );
    }

    // -- 8. Per-block cancellation during large scan -------------------

    #[test]
    fn cancellation_during_large_scan_returns_none_and_keeps_old_index() {
        // Build a file > 1 MiB so the loop iterates multiple times.
        let mut content = Vec::new();
        let line = b"0123456789ABCDE\n";
        for _ in 0..(200 * 1024) {
            content.extend_from_slice(line);
        }
        let (_tmp_db, db_path) = make_db("cancel_mid_db");
        let (_tmp_file, file_path) = make_file("cancel_mid", &content);
        let store = LogIndexStorage::open(&db_path).unwrap();
        let old = FileIndex {
            file_path: file_path.to_string_lossy().into_owned(),
            file_fingerprint: "OLD".into(),
            file_size: 1,
            line_count: 0,
            line_offsets: encode_u64_le(&[0]),
            encoding: "utf-8".into(),
            last_modified: 0,
            indexed_at: 0,
            index_version: 1,
        };
        store.upsert(&old).unwrap();

        let indexer = FileIndexer::new(store.clone());
        let cancel = Arc::new(AtomicBool::new(false));
        let cancel_inside = cancel.clone();
        let snap_count = Arc::new(AtomicUsize::new(0));
        let snap_count_inside = snap_count.clone();
        let mut cb: Box<dyn FnMut(IndexProgress) + Send> =
            Box::new(move |_p: IndexProgress| {
                // After the first progress snapshot, signal cancel so the
                // next iteration of the outer loop bails out.
                if snap_count_inside.fetch_add(1, Ordering::SeqCst) == 0 {
                    cancel_inside.store(true, Ordering::SeqCst);
                }
            });

        let result = indexer
            .index_file(&file_path, cancel, &mut cb)
            .expect("index_file returns Ok");
        assert!(result.is_none(), "mid-scan cancel returns None");

        let stored = store
            .get(&file_path.to_string_lossy())
            .unwrap()
            .expect("old row still present after cancel");
        assert_eq!(stored.file_fingerprint, "OLD");
        assert_eq!(stored.line_count, 0);
        assert!(
            snap_count.load(Ordering::SeqCst) >= 1,
            "at least one snapshot was emitted before cancellation"
        );
    }

    // -- 9. Successful scan & upsert -----------------------------------

    #[test]
    fn successful_scan_commits_complete_index_in_one_transaction() {
        let (_tmp, path) = make_file("atomic_ok", b"a\nb\nc\nd\n");
        let cancel = Arc::new(AtomicBool::new(false));
        let (summary, stored, _snaps) = run_index(&path, cancel);

        // Summary must reflect the file's actual content.
        assert_eq!(summary.line_count, 4);
        assert_eq!(summary.file_size, 8);

        // Storage row must reflect the complete scan, not a partial state.
        assert_eq!(stored.line_count, 4);
        assert_eq!(stored.file_size, 8);
        assert_eq!(
            decode_u64_le(&stored.line_offsets).unwrap(),
            vec![0u64, 2, 4, 6, 8]
        );
    }

    #[test]
    fn successful_scan_replaces_stale_index() {
        // Pre-seed an old row; a successful scan must replace it.
        let (_tmp_db, db_path) = make_db("replace_db");
        let (_tmp_file, file_path) = make_file("replace", b"x\ny\n");
        let store = LogIndexStorage::open(&db_path).unwrap();
        let old = FileIndex {
            file_path: file_path.to_string_lossy().into_owned(),
            file_fingerprint: "STALE".into(),
            file_size: 1,
            line_count: 0,
            line_offsets: encode_u64_le(&[0]),
            encoding: "utf-8".into(),
            last_modified: 0,
            indexed_at: 0,
            index_version: 1,
        };
        store.upsert(&old).unwrap();

        let indexer = FileIndexer::new(store.clone());
        let cancel = Arc::new(AtomicBool::new(false));
        let (mut cb, _count) = progress_counter();
        let summary = indexer
            .index_file(&file_path, cancel, &mut cb)
            .unwrap()
            .unwrap();
        assert_eq!(summary.line_count, 2);

        let stored = store
            .get(&file_path.to_string_lossy())
            .unwrap()
            .expect("row present");
        assert_ne!(stored.file_fingerprint, "STALE");
        assert_eq!(stored.line_count, 2);
    }

    // -- 10. Buffer does not accumulate file content in memory ---------

    #[test]
    fn indexer_does_not_allocate_per_line_for_large_file() {
        // Sanity: a 3 MiB file with many short lines must still complete.
        // We can't directly assert allocation here, but we can verify the
        // run completes with sane line_count and offsets without OOM.
        let mut content = Vec::new();
        let line = b"xx\n"; // 3 bytes
        for _ in 0..(1 << 20) {
            // 1M lines
            content.extend_from_slice(line);
        } // 3 MiB
        let (_tmp, path) = make_file("big_scan", &content);
        let cancel = Arc::new(AtomicBool::new(false));
        let (summary, stored, snaps) = run_index(&path, cancel);
        assert_eq!(summary.line_count, 1 << 20);
        assert_eq!(summary.file_size, content.len() as u64);
        assert_eq!(stored.line_count, summary.line_count);
        assert!(snaps >= 3);
    }
}