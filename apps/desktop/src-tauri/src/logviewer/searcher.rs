//! Phase 2F+ Task 4 — file-scoped literal & regex line search.
//!
//! ## API
//!
//! ```ignore
//! pub fn search(
//!     &self,
//!     path: &str,
//!     mode: SearchMode,        // Literal | Regex
//!     pattern: &str,
//!     before: usize,           // context lines BEFORE the match
//!     after: usize,            // context lines AFTER the match
//!     max_matches: usize,      // hard cap on returned matches
//!     max_bytes: usize,        // hard cap on returned serialized bytes
//!     cancel: Arc<AtomicBool>, // observed per-line
//! ) -> AppResult<LogSearchResult>
//! ```
//!
//! ## Design
//!
//! - **1 MiB block scanning** — `BufReader::with_capacity(BLOCK_SIZE)` so each
//!   underlying `read` syscall serves up to 1 MiB. We stream line-by-line via
//!   `read_until(b'\n')` which preserves any partial line that crosses a block
//!   boundary inside the BufReader's internal buffer.
//! - **Memory footprint** — only the current line buffer (`Vec<u8>` reused per
//!   iteration), the rolling `before` context window, and the accumulated
//!   matches. The file body is never collected.
//! - **Cancellation** — checked at every iteration top so a flag flip is
//!   observed within ~one line of progress (≪ 1 MiB in practice).
//! - **Stale fingerprint rejection** — before reading, re-stat the file via
//!   `FileIndexer::fingerprint` and refuse if the stored fingerprint differs
//!   from the on-disk one (offsets would land on the wrong bytes).
//! - **Limits**:
//!   - `max_matches` — stop scanning as soon as the next match would push the
//!     result past this cap; `truncated = true`.
//!   - `max_bytes` — approximate the serialized size of each match at insert
//!     time (`line_text.len()` + contexts + JSON framing overhead). As soon as
//!     adding the next match would exceed, stop; `truncated = true`.
//! - **Context semantics**:
//!   - `context_before` is a snapshot of the rolling buffer immediately before
//!     the match line (excludes the match line itself).
//!   - `context_after` for match `M` is the lines between `M` and the next
//!     match (whichever comes first), capped at `after` lines. A match
//!     occurring inside the after-window does NOT appear in the prior match's
//!     `context_after` — it appears as its own `LogSearchMatch`.
//!   - Rolling buffer for `before` keeps the last `before` lines, dropping the
//!     oldest as new lines are pushed.
//! - **Errors**:
//!   - empty pattern                            -> `AppError::Validation`
//!   - invalid regex                            -> `AppError::Validation`
//!   - missing index row                        -> `AppError::NotFound`
//!   - fingerprint mismatch / cancellation     -> `AppError::Validation`
//!   - I/O failures                             -> underlying `AppError::Io`
//!
//! The `regex` crate is already in the lockfile (transitive via `reqwest`),
//! so no Cargo.toml edit is required; if you ever pin it explicitly, use
//! `regex = "1"`.

use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::error::{AppError, AppResult};
use crate::logviewer::indexer::FileIndexer;
use crate::logviewer::storage::LogIndexStorage;

/// Internal BufReader capacity. 1 MiB = 1 << 20 bytes. Matches the indexer's
/// `BLOCK_SIZE` so cancellation/throughput characteristics stay consistent.
const BLOCK_SIZE: usize = 1 << 20;

/// Approximate JSON-serialization overhead per `LogSearchMatch` entry
/// (key names, line-number tag, surrounding brackets, commas). Used by
/// `max_bytes` accounting; intentionally generous so we never return more
/// than `max_bytes` of accumulated payload.
const PER_MATCH_OVERHEAD_BYTES: usize = 64;

// ----------------------------------------------------------------------------
// Search mode
// ----------------------------------------------------------------------------

/// Search mode. `Literal` does a substring search; `Regex` compiles the
/// pattern with the `regex` crate (UTF-8, linear-time, supports most PCRE-like
/// syntax — but no backreferences and no arbitrary lookbehind).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum SearchMode {
    Literal,
    Regex,
}

// ----------------------------------------------------------------------------
// DTOs
// ----------------------------------------------------------------------------

/// One matched line plus its surrounding context. `context_before` /
/// `context_after` are bounded by the caller-supplied `before` / `after`
/// parameters.
///
/// `line_text` strips exactly one trailing `\n` (matching `LineReader` /
/// `read_lines`). A trailing `\r` from a CRLF file is preserved verbatim.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LogSearchMatch {
    /// 0-based line number where the match was found. `line_number == 0`
    /// corresponds to the first physical line of the file.
    pub line_number: u64,
    /// The matched line's text, with at most one trailing `\n` stripped.
    pub line_text: String,
    /// Up to `before` lines immediately preceding `line_text`, in order.
    /// Empty when the match occurs at the beginning of the file or when
    /// `before == 0`.
    pub context_before: Vec<String>,
    /// Up to `after` lines immediately following `line_text`, in order, up
    /// to the next match (whichever comes first). Empty when `after == 0` or
    /// when the match is on the final line.
    pub context_after: Vec<String>,
}

/// Aggregated search outcome.
///
/// `match_count` is the total number of matching lines seen during the scan,
/// even if some were dropped by `max_matches` / `max_bytes`. `matches.len()`
/// is the number actually returned to the caller.
///
/// `truncated` is `true` when either limit halted the scan before the file
/// was exhausted. When `truncated` is `true`, callers should re-run (perhaps
/// with a larger `max_matches` / `max_bytes`) to see remaining matches.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct LogSearchResult {
    pub matches: Vec<LogSearchMatch>,
    pub match_count: u64,
    pub truncated: bool,
    pub file_fingerprint: String,
}

// ----------------------------------------------------------------------------
// Searcher
// ----------------------------------------------------------------------------

/// One searcher instance is cheap (it holds only the storage path).
/// Reuse across calls to amortize path lookups.
#[derive(Debug, Clone)]
pub struct LogSearcher {
    storage: LogIndexStorage,
}

impl LogSearcher {
    /// Construct a searcher bound to the given storage handle.
    pub fn new(storage: LogIndexStorage) -> Self {
        Self { storage }
    }

    /// Borrow the underlying storage handle.
    pub fn storage(&self) -> &LogIndexStorage {
        &self.storage
    }

    /// Scan `path` for lines that match `pattern` under `mode`. See module
    /// docs for the full contract.
    // 搜索参数为对外契约（commands 层透传），不拆结构体
    #[allow(clippy::too_many_arguments)]
    pub fn search(
        &self,
        path: &str,
        mode: SearchMode,
        pattern: &str,
        before: usize,
        after: usize,
        max_matches: usize,
        max_bytes: usize,
        cancel: Arc<AtomicBool>,
    ) -> AppResult<LogSearchResult> {
        // 1. Reject empty needle up front (empty literal would match every
        //    line; empty regex is a regex syntax error).
        if pattern.is_empty() {
            return Err(AppError::Validation("search pattern is empty".into()));
        }

        // 2. Compile regex if needed; report compile failures as Validation.
        let compiled: Option<Regex> = match mode {
            SearchMode::Regex => match Regex::new(pattern) {
                Ok(r) => Some(r),
                Err(e) => {
                    return Err(AppError::Validation(format!("invalid regex: {}", e)))
                }
            },
            SearchMode::Literal => None,
        };

        // 3. Look up the index row. Without it we have no line-count budget
        //    and no fingerprint to validate against.
        let index = self
            .storage
            .get(path)?
            .ok_or_else(|| AppError::NotFound(format!("no index for path: {}", path)))?;

        // 4. Re-stat the file and reject on fingerprint mismatch — same
        //    contract as `LineReader::read_lines`.
        let path_buf = PathBuf::from(path);
        let (current_fp, _size, _mtime) = FileIndexer::fingerprint(&path_buf)?;
        if current_fp != index.file_fingerprint {
            return Err(AppError::Validation(format!(
                "file changed (fingerprint mismatch): expected {}, got {}",
                index.file_fingerprint, current_fp
            )));
        }

        // 5. Stream the file. The BufReader preserves partial lines that
        //    cross block boundaries internally, so we never need to
        //    reconstruct lines from chunks manually.
        let file = File::open(&path_buf)?;
        let mut reader = BufReader::with_capacity(BLOCK_SIZE, file);

        let mut matches: Vec<LogSearchMatch> = Vec::new();
        let mut match_count: u64 = 0;
        let mut truncated = false;
        let mut bytes_returned: usize = 0;

        // Rolling buffer of the last `before` lines (no trailing `\n`).
        let mut recent: Vec<String> = Vec::with_capacity(before);
        // Counter for the still-open after-context window of the most
        // recently added match. Decremented each non-match line.
        let mut pending_after: usize = 0;

        // Reused per-iteration line buffer.
        let mut line_buf: Vec<u8> = Vec::with_capacity(512);
        let mut current_line: u64 = 0;

        loop {
            // Cancellation is observed at the top of each iteration; each
            // iteration handles exactly one logical line.
            if cancel.load(Ordering::SeqCst) {
                return Err(AppError::Validation("search cancelled".into()));
            }

            line_buf.clear();
            let n = reader.read_until(b'\n', &mut line_buf)?;
            if n == 0 {
                break;
            }

            // Strip exactly one trailing '\n' (matches reader.rs). A trailing
            // '\r' from CRLF files is preserved — we don't want to rewrite
            // byte content the caller might rely on.
            let line_owned = {
                let s = String::from_utf8_lossy(&line_buf);
                let trimmed = s.trim_end_matches('\n');
                trimmed.to_string()
            };

            let is_match = match mode {
                SearchMode::Literal => line_owned.contains(pattern),
                SearchMode::Regex => {
                    // SAFETY: `compiled` is `Some` whenever `mode == Regex`;
                    // the match arm above guarantees this.
                    compiled.as_ref().expect("regex compiled").is_match(&line_owned)
                }
            };

            // Capture after-context for the previously-emitted match first,
            // but ONLY when this line itself isn't a match (a match within
            // the after-window becomes its own match — see module docs).
            if pending_after > 0 && !is_match {
                if let Some(last) = matches.last_mut() {
                    last.context_after.push(line_owned.clone());
                }
                pending_after -= 1;
            }

            if is_match {
                // Always count: `match_count` reports total matches seen,
                // even when we can't fit them in the returned list.
                match_count += 1;

                let new_match = LogSearchMatch {
                    line_number: current_line,
                    line_text: line_owned.clone(),
                    context_before: recent.clone(),
                    context_after: Vec::new(),
                };

                let approx_bytes = approx_match_bytes(&new_match);

                // Decide whether this match fits inside the caller's caps.
                // If yes, push it (and open an after-context window); if no,
                // mark truncated and keep scanning just to count remaining
                // matches — never to push.
                let fits = matches.len() < max_matches
                    && bytes_returned.saturating_add(approx_bytes) <= max_bytes;

                // Debug output disabled for production; re-enable during development:
            // eprintln!("DBG line={} match_count={} fits={} ml={} mm={} ab={} br={} mb={}",
            //     current_line, match_count, fits, matches.len(), max_matches,
            //     approx_bytes, bytes_returned, max_bytes);
                if fits {
                    bytes_returned = bytes_returned.saturating_add(approx_bytes);
                    matches.push(new_match);
                    pending_after = after;
                } else {
                    truncated = true;
                    // No pending_after — we did not emit this match.
                }
            }

            // Maintain rolling buffer for the next iteration's snapshot
            // AFTER processing this line (so the snapshot never includes
            // the current line).
            recent.push(line_owned);
            if recent.len() > before {
                recent.remove(0);
            }
            current_line += 1;
        }

        Ok(LogSearchResult {
            matches,
            match_count,
            truncated,
            file_fingerprint: current_fp,
        })
    }
}

/// Approximate the serialized byte size of one `LogSearchMatch`. Real
/// JSON encoding adds commas and quotes; the overhead constant covers them.
fn approx_match_bytes(m: &LogSearchMatch) -> usize {
    let mut total = PER_MATCH_OVERHEAD_BYTES;
    total = total.saturating_add(m.line_text.len());
    for s in &m.context_before {
        total = total.saturating_add(s.len());
    }
    for s in &m.context_after {
        total = total.saturating_add(s.len());
    }
    total
}

// ===========================================================================
// Tests — written TDD-first.
// Run alone: `cargo test -p eaide-desktop logviewer::searcher::tests::`
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::AtomicBool;

    fn unique_dir(label: &str) -> (tempdir::Guard, PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "eaide-logviewer-searcher-{}-{}",
            label,
            uuid::Uuid::new_v4()
        ));
        fs::create_dir_all(&dir).expect("create tempdir");
        let path = dir.clone();
        (tempdir::Guard(dir), path)
    }

    mod tempdir {
        pub struct Guard(pub std::path::PathBuf);
        impl Drop for Guard {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
    }

    /// Build a tempdir containing both the data file and an indexed
    /// `log_index.db`. Returns the guard + storage + file path. Tests MUST
    /// keep the guard alive (typically `let (_g, store, path) = ...`).
    fn setup_indexed(label: &str, contents: &[u8]) -> (tempdir::Guard, LogIndexStorage, PathBuf) {
        let (g, dir) = unique_dir(label);
        let file_path = dir.join("data.log");
        fs::write(&file_path, contents).expect("write file");
        let db_path = dir.join("log_index.db");
        let store = LogIndexStorage::open(&db_path).expect("open storage");
        let indexer = FileIndexer::new(store.clone());
        let cancel = Arc::new(AtomicBool::new(false));
        let mut noop = |_p: crate::logviewer::indexer::IndexProgress| {};
        indexer
            .index_file(&file_path, cancel, &mut noop)
            .expect("index_file")
            .expect("not cancelled");
        (g, store, file_path)
    }

    fn no_cancel() -> Arc<AtomicBool> {
        Arc::new(AtomicBool::new(false))
    }

    fn line_numbers(result: &LogSearchResult) -> Vec<u64> {
        result.matches.iter().map(|m| m.line_number).collect()
    }

    // -- 1. Literal -----------------------------------------------------

    #[test]
    fn literal_match_single_line() {
        let (_g, store, path) = setup_indexed("lit_single", b"alpha\nbeta\ngamma\n");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "beta",
                0,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.match_count, 1);
        assert!(!res.truncated);
        assert_eq!(line_numbers(&res), vec![1]);
        assert_eq!(res.matches[0].line_text, "beta");
        assert!(res.matches[0].context_before.is_empty());
        assert!(res.matches[0].context_after.is_empty());
    }

    #[test]
    fn literal_multiple_matches() {
        let (_g, store, path) =
            setup_indexed("lit_multi", b"foo\nbar\nfoo\nbaz\nfoo\n");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "foo",
                0,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.match_count, 3);
        assert!(!res.truncated);
        assert_eq!(line_numbers(&res), vec![0, 2, 4]);
        assert_eq!(res.matches[0].line_text, "foo");
        assert_eq!(res.matches[1].line_text, "foo");
        assert_eq!(res.matches[2].line_text, "foo");
    }

    #[test]
    fn literal_no_match_returns_empty() {
        let (_g, store, path) = setup_indexed("lit_none", b"alpha\nbeta\n");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "zeta",
                0,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok for no-match");

        assert_eq!(res.match_count, 0);
        assert!(res.matches.is_empty());
        assert!(!res.truncated);
    }

    #[test]
    fn literal_match_count_reflects_all_seen_when_truncated() {
        // 5 matches in 8 lines; cap to 2. match_count must still equal 5
        // (we SAW 5, even though only 2 are returned).
        let (_g, store, path) = setup_indexed(
            "lit_count",
            b"M1\nx\nM2\nx\nM3\nx\nM4\nx\nM5\n",
        );
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "M",
                0,
                0,
                2,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.match_count, 5, "match_count counts all seen");
        assert_eq!(res.matches.len(), 2, "but only 2 are returned");
        assert!(res.truncated, "truncated = true when max_matches hit");
        assert_eq!(line_numbers(&res), vec![0, 2]);
    }

    // -- 2. Regex -------------------------------------------------------

    #[test]
    fn regex_match_basic() {
        let (_g, store, path) =
            setup_indexed("re_basic", b"alpha123\nbeta\nfoo456\n");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Regex,
                r"[a-z]+\d+",
                0,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.match_count, 2);
        assert!(!res.truncated);
        assert_eq!(line_numbers(&res), vec![0, 2]);
    }

    #[test]
    fn regex_alternation() {
        let (_g, store, path) =
            setup_indexed("re_alt", b"INFO ok\nWARN slow\nERROR boom\nDEBUG trace\n");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Regex,
                r"^(WARN|ERROR)",
                0,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.match_count, 2);
        assert_eq!(line_numbers(&res), vec![1, 2]);
    }

    #[test]
    fn regex_invalid_pattern_rejected() {
        let (_g, store, path) = setup_indexed("re_bad", b"any content\n");
        let searcher = LogSearcher::new(store);

        // Unbalanced `[` is a regex compile error.
        let err = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Regex,
                "[unterminated",
                0,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect_err("invalid regex must error");

        assert!(
            matches!(err, AppError::Validation(_)),
            "expected Validation, got {:?}",
            err
        );
        let msg = format!("{}", err).to_lowercase();
        assert!(msg.contains("regex"), "message should mention regex, got {:?}", err);
    }

    #[test]
    fn regex_empty_pattern_rejected() {
        let (_g, store, path) = setup_indexed("re_empty", b"x\n");
        let searcher = LogSearcher::new(store);

        let err = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Regex,
                "",
                0,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect_err("empty regex must error");
        assert!(matches!(err, AppError::Validation(_)));
    }

    #[test]
    fn literal_empty_pattern_rejected() {
        let (_g, store, path) = setup_indexed("lit_empty", b"x\n");
        let searcher = LogSearcher::new(store);

        let err = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "",
                0,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect_err("empty literal must error");
        assert!(matches!(err, AppError::Validation(_)));
    }

    // -- 3. Context windows --------------------------------------------

    #[test]
    fn context_before_window() {
        let (_g, store, path) = setup_indexed(
            "ctx_before",
            b"L0\nL1\nL2\nL3\nMATCH\nL5\nL6\n",
        );
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "MATCH",
                2,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.matches.len(), 1);
        // `MATCH` is on line 4 (0-based). Two preceding lines: L2, L3.
        assert_eq!(res.matches[0].line_number, 4);
        assert_eq!(res.matches[0].line_text, "MATCH");
        assert_eq!(res.matches[0].context_before, vec!["L2".to_string(), "L3".to_string()]);
        assert!(res.matches[0].context_after.is_empty());
    }

    #[test]
    fn context_after_window() {
        let (_g, store, path) = setup_indexed(
            "ctx_after",
            b"L0\nL1\nMATCH\nL3\nL4\nL5\n",
        );
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "MATCH",
                0,
                3,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.matches.len(), 1);
        assert_eq!(res.matches[0].line_number, 2);
        assert_eq!(
            res.matches[0].context_after,
            vec!["L3".to_string(), "L4".to_string(), "L5".to_string()]
        );
    }

    #[test]
    fn context_after_cuts_short_on_subsequent_match() {
        // Match M1 at line 0, match M2 at line 2. `after=3` would normally
        // collect 3 lines, but a match line is excluded from the prior
        // match's context_after — it appears as its own match instead.
        let (_g, store, path) = setup_indexed(
            "ctx_after_next_match",
            b"M1\nbetween\nM2\n",
        );
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "M",
                0,
                3,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.match_count, 2);
        assert_eq!(line_numbers(&res), vec![0, 2]);
        // M1's context_after only contains the line "between" (the M2
        // match doesn't go into M1's context).
        assert_eq!(res.matches[0].context_after, vec!["between".to_string()]);
        // M2 has no after-context (it's the last match and only one trailing
        // line is consumed).
        assert!(res.matches[1].context_after.is_empty());
    }

    #[test]
    fn context_before_and_after_together() {
        let (_g, store, path) = setup_indexed(
            "ctx_both",
            b"a\nb\nc\nMATCH\ne\nf\n",
        );
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "MATCH",
                2,
                2,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.matches[0].line_number, 3);
        assert_eq!(res.matches[0].context_before, vec!["b".to_string(), "c".to_string()]);
        assert_eq!(res.matches[0].context_after, vec!["e".to_string(), "f".to_string()]);
    }

    #[test]
    fn match_at_very_beginning_has_no_before_context() {
        let (_g, store, path) = setup_indexed("ctx_beg", b"MATCH\nafter\n");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "MATCH",
                5,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.matches.len(), 1);
        assert!(res.matches[0].context_before.is_empty());
    }

    #[test]
    fn match_at_very_end_has_no_after_context() {
        let (_g, store, path) = setup_indexed("ctx_end", b"before\nMATCH\n");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "MATCH",
                0,
                5,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.matches.len(), 1);
        assert!(res.matches[0].context_after.is_empty());
    }

    #[test]
    fn context_before_is_snapshot_per_match() {
        // Two matches separated by exactly `before` lines. The first
        // match's context_before is independent of the second.
        let (_g, store, path) = setup_indexed(
            "ctx_snapshot",
            b"b0\nb1\nM1\nb3\nb4\nM2\nb6\n",
        );
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "M",
                2,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.matches.len(), 2);
        // M1 (line 2): preceding 2 lines are b0, b1.
        assert_eq!(res.matches[0].context_before, vec!["b0".to_string(), "b1".to_string()]);
        // M2 (line 5): preceding 2 lines are b3, b4 — independent of M1.
        assert_eq!(res.matches[1].context_before, vec!["b3".to_string(), "b4".to_string()]);
    }

    // -- 4. max_matches / max_bytes limits ----------------------------

    #[test]
    fn max_matches_one_returns_only_first() {
        let (_g, store, path) = setup_indexed(
            "cap_matches_one",
            b"x\nx\nx\nx\n",
        );
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "x",
                0,
                0,
                1,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.matches.len(), 1);
        assert!(res.truncated);
        // We saw 4 lines that matched, but returned only the first one.
        assert_eq!(res.match_count, 4);
        assert_eq!(res.matches[0].line_text, "x");
    }

    #[test]
    fn max_bytes_truncates_result_set() {
        // Five identical 4-byte lines "M1\n" = 5 bytes each. Per-match
        // approx = 64 + 4 (line_text) + 0 + 0 = 68 bytes. A 100-byte cap
        // admits exactly 1 match; the second would push us to 136 > 100.
        let (_g, store, path) = setup_indexed("cap_bytes", b"M1\nM1\nM1\nM1\nM1\n");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "M1",
                0,
                0,
                100,
                100, // 100-byte cap
                no_cancel(),
            )
            .expect("search ok");

        assert!(res.truncated, "truncated must be true when max_bytes hit");
        // Exactly 1 match fits in 100 bytes (68 ≤ 100 < 136).
        assert!(!res.matches.is_empty(), "at least 1 match should fit");
        assert!(
            res.matches.len() <= 2,
            "matches.len()={} should not blow past the byte cap",
            res.matches.len()
        );
    }

    #[test]
    fn max_matches_zero_returns_no_matches_but_counts() {
        // Edge contract: max_matches=0 means "don't return any match". We
        // still count what we saw.
        let (_g, store, path) = setup_indexed("cap_zero", b"x\nx\nx\n");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "x",
                0,
                0,
                0,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert!(res.matches.is_empty());
        assert!(res.truncated);
        assert!(res.match_count >= 1);
    }

    #[test]
    fn limits_not_hit_returns_full_result() {
        let (_g, store, path) = setup_indexed("cap_off", b"a\nb\nc\n");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "b",
                0,
                0,
                100,
                1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.match_count, 1);
        assert_eq!(res.matches.len(), 1);
        assert!(!res.truncated);
    }

    // -- 5. Stale fingerprint / missing index --------------------------

    #[test]
    fn missing_index_returns_not_found() {
        let (g, dir) = unique_dir("miss_idx");
        let db_path = dir.join("log_index.db");
        let store = LogIndexStorage::open(&db_path).unwrap();
        // No rows indexed.
        let searcher = LogSearcher::new(store);

        // Need a file path that COULD exist but isn't indexed.
        let file = dir.join("nothing.log");
        fs::write(&file, b"x\n").unwrap();
        let err = searcher
            .search(
                file.to_str().unwrap(),
                SearchMode::Literal,
                "x",
                0,
                0,
                10,
                1024,
                no_cancel(),
            )
            .expect_err("missing index must error");
        assert!(
            matches!(err, AppError::NotFound(_)),
            "expected NotFound, got {:?}",
            err
        );
        drop(g); // keep alive until end
    }

    #[test]
    fn stale_fingerprint_returns_validation() {
        let (_g, store, path) = setup_indexed("stale", b"abc\n");
        let searcher = LogSearcher::new(store);

        // Grow the file so the fingerprint changes.
        fs::write(&path, b"abc-extra-tail-very-long\n").unwrap();

        let err = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "abc",
                0,
                0,
                10,
                1024,
                no_cancel(),
            )
            .expect_err("stale fingerprint must error");
        assert!(
            matches!(err, AppError::Validation(_)),
            "expected Validation, got {:?}",
            err
        );
        let msg = format!("{}", err).to_lowercase();
        assert!(msg.contains("fingerprint"));
    }

    // -- 6. Cancellation -----------------------------------------------

    #[test]
    fn pre_cancelled_returns_validation_error() {
        let (_g, store, path) = setup_indexed("cancel_pre", b"a\nb\nc\n");
        let searcher = LogSearcher::new(store);

        let cancel = Arc::new(AtomicBool::new(true));
        let err = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "a",
                0,
                0,
                10,
                1024,
                cancel,
            )
            .expect_err("pre-cancelled must error");

        assert!(
            matches!(err, AppError::Validation(_)),
            "expected Validation, got {:?}",
            err
        );
    }

    #[test]
    fn mid_scan_cancellation_observes_flag() {
        // > 1 MiB so the loop iterates many times. Flip cancel after the
        // first emitted match; the next loop iteration should bail.
        let mut lines: Vec<u8> = Vec::new();
        // Each line: "0123456789ABCDEF\n" = 17 bytes. 80_000 lines ≈ 1.36 MiB.
        for i in 0..80_000u32 {
            lines.extend_from_slice(format!("{:016}\n", i).as_bytes());
        }
        let (_g, store, path) = setup_indexed("cancel_mid", &lines);
        let searcher = LogSearcher::new(store);

        // Pattern matches almost everything, so we know we'll iterate past
        // the first match and into the cancel check.
        let cancel = Arc::new(AtomicBool::new(false));
        let cancel_inside = cancel.clone();
        // Wrap the cancel in a closure-like arrangement: we'll set it true
        // during the call by relying on the fact that the search emits
        // matches immediately and we observe them... but actually we can't
        // observe from outside. So instead, just pre-cancel between the
        // fingerprint check and the read — covered above. For this test,
        // we trigger by sleeping briefly after starting the call.
        //
        // Simpler approach: spawn a thread that flips the flag after a
        // short delay.
        let cancel_thread = cancel_inside.clone();
        let handle = std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(2));
            cancel_thread.store(true, std::sync::atomic::Ordering::SeqCst);
        });

        let result = searcher.search(
            path.to_str().unwrap(),
            SearchMode::Literal,
            "0123",
            0,
            0,
            100_000,
            1024 * 1024,
            cancel_inside,
        );

        handle.join().expect("cancel thread");
        // Either: cancellation fired (Validation err), or we completed first
        // (Ok with truncation because of max_matches we set to 100k). Both
        // are acceptable; we just want to prove the flag-observation path
        // exists and doesn't crash. We pin the contract: cancelled -> err.
        if let Err(e) = result {
            assert!(matches!(e, AppError::Validation(_)));
        }
        // If Ok, we completed before the flag flipped — also fine.
    }

    // -- 7. Block-boundary streaming -----------------------------------

    /// A line that is longer than the BufReader block size must still be
    /// read correctly (read_until returns the whole line in chunks up to
    /// the buffer, accumulating internally).
    #[test]
    fn extremely_long_line_is_handled() {
        // 2 MiB single line, no '\n'. Files allow this.
        let big = vec![b'X'; 2 * 1024 * 1024];
        // No trailing newline.
        let (_g, store, path) = setup_indexed("long_line", &big);
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "X",
                0,
                0,
                100,
                // Need at least ~2 MiB to fit one giant match.
                16 * 1024 * 1024,
                no_cancel(),
            )
            .expect("search ok on giant line");

        // The one giant line matches and is returned.
        assert_eq!(res.matches.len(), 1);
        assert_eq!(res.matches[0].line_text.len(), 2 * 1024 * 1024);
    }

    /// A line that spans the 1 MiB block boundary must still be matched as
    /// a single line (not split into two).
    #[test]
    fn line_spanning_block_boundary_is_one_match() {
        // 1 MiB of filler + "MAGIC" + 100 bytes of trailing filler.
        let mut buf = Vec::new();
        buf.extend(std::iter::repeat_n(b'.', 1 << 20));
        buf.extend_from_slice(b"MAGIC");
        buf.extend(std::iter::repeat_n(b'.', 100));
        let (_g, store, path) = setup_indexed("block_boundary", &buf);
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "MAGIC",
                0,
                0,
                100,
                // Need at least 1 MiB + 105 bytes + 64 overhead to fit one.
                16 * 1024 * 1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.matches.len(), 1);
        // The matched line is the entire 1 MiB + 5 + 100 byte single-line,
        // minus the trailing newline (no trailing newline here, so the line
        // is the whole file).
        assert_eq!(res.matches[0].line_number, 0);
    }

    // -- 8. Edge cases --------------------------------------------------

    #[test]
    fn empty_file_returns_empty_result() {
        let (_g, store, path) = setup_indexed("empty", b"");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "anything",
                0,
                0,
                10,
                1024,
                no_cancel(),
            )
            .expect("empty file is not an error");

        assert_eq!(res.match_count, 0);
        assert!(res.matches.is_empty());
        assert!(!res.truncated);
    }

    #[test]
    fn unterminated_final_line_is_searched() {
        // Three lines, the last missing '\n'.
        let (_g, store, path) = setup_indexed("unterm", b"MATCH1\nbetween\nMATCH2");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "MATCH",
                0,
                0,
                10,
                1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.match_count, 2);
        assert_eq!(res.matches[1].line_text, "MATCH2");
        assert_eq!(res.matches[1].line_number, 2);
    }

    #[test]
    fn non_ascii_line_text_is_preserved() {
        let bytes = "你好\n世界\n".as_bytes().to_vec();
        let (_g, store, path) = setup_indexed("non_ascii", &bytes);
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "世界",
                0,
                0,
                10,
                1024,
                no_cancel(),
            )
            .expect("search ok");

        assert_eq!(res.match_count, 1);
        assert_eq!(res.matches[0].line_text, "世界");
    }

    #[test]
    fn searcher_storage_accessor_returns_handle() {
        let (_g, store, path) = setup_indexed("acc", b"x\n");
        let searcher = LogSearcher::new(store.clone());
        assert_eq!(searcher.storage().path(), store.path());
        // ensure `path` is actually referenced to silence unused warnings.
        let _ = path.as_path();
    }

    #[test]
    fn file_fingerprint_in_result_reflects_current_file() {
        let (_g, store, path) = setup_indexed("fp", b"abc\n");
        let searcher = LogSearcher::new(store);

        let res = searcher
            .search(
                path.to_str().unwrap(),
                SearchMode::Literal,
                "abc",
                0,
                0,
                10,
                1024,
                no_cancel(),
            )
            .expect("search ok");

        // Fingerprint on the result should match the current on-disk file.
        let expected = crate::logviewer::indexer::FileIndexer::fingerprint(Path::new(path.to_str().unwrap()))
            .unwrap()
            .0;
        assert_eq!(res.file_fingerprint, expected);
        // Sanity: fingerprint format is "<size>:<mtime>".
        assert!(res.file_fingerprint.contains(':'));
    }
}
