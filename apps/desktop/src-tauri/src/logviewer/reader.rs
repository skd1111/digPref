//! Phase 2F+ Task 3 — `LineReader`: seek + bounded range read for large logs.
//!
//! ## API
//!
//! ```ignore
//! pub fn read_lines(
//!     &self,
//!     path: &str,
//!     start_line: u64,   // inclusive
//!     end_line: u64,     // exclusive (half-open range)
//!     max_bytes: usize,  // hard cap on bytes read; truncates & sets `truncated`
//! ) -> AppResult<ReadLinesResult>
//! ```
//!
//! ## Design
//!
//! - **Line terminator handling**: strip only a single trailing `\n` from
//!   each returned line. Any other byte (notably `\r` from CRLF line
//!   endings) is preserved verbatim — the caller can decide how to render
//!   it.
//! - **Half-open range**: the same convention as Rust slices
//!   (`[start_line, end_line)`). This matches the `line_offsets` BLOB
//!   layout where `offsets[i]` is the byte start of line `i` and
//!   `offsets[line_count] == file_size` is the sentinel.
//! - **Bounded read window**: when the requested range's byte length
//!   exceeds `max_bytes`, we read at most `max_bytes` bytes, drop any
//!   partial final line, and return `ReadLinesResult { truncated: true, .. }`.
//!   This is the memory-protection guardrail that lets the same API be
//!   used safely against accidental 100 GB windows.
//! - **Stale fingerprint rejection**: before opening the file we re-stat it
//!   via `FileIndexer::fingerprint` and compare against the fingerprint
//!   stored in the index. A mismatch means the file changed since the
//!   index was built and the offsets are no longer trustworthy, so the
//!   read is rejected (caller should re-index).
//! - **Error mapping**:
//!   - missing index row  -> `AppError::NotFound`
//!   - out-of-range / inverted / fingerprint mismatch / blob decode error
//!                         -> `AppError::Validation`
//!   - I/O failures       -> underlying `AppError::Io` / `AppError::Internal`
//!
//! Tests in the inner `mod tests` exercise every contract above. They are
//! written TDD-first (see this module's commit history).

use std::fs;
use std::io::{Read, Seek, SeekFrom};
use std::path::Path;
use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
use std::sync::Arc;

use serde::{Deserialize, Serialize};

use crate::error::{AppError, AppResult};
use crate::logviewer::encoding::decode_line;
use crate::logviewer::indexer::FileIndexer;
use crate::logviewer::storage::{decode_u64_le, FileIndex, LogIndexStorage};

/// Result of a single `read_lines` call.
///
/// - `lines` — UTF-8 lossy decoded lines with at most one trailing `\n`
///   stripped. `\r` is preserved when present.
/// - `truncated` — `true` when the requested byte window exceeded
///   `max_bytes` and the last complete line within `max_bytes` bytes was
///   used as the cut point.
/// - `bytes_read` — number of file bytes consumed (after any truncation
///   cut). 0 for an empty range.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReadLinesResult {
    pub lines: Vec<String>,
    pub truncated: bool,
    pub bytes_read: usize,
}

// ---------------------------------------------------------------------------
// Reader
// ---------------------------------------------------------------------------

/// Reads line ranges from a file using the offsets stored in
/// [`LogIndexStorage`]. One reader is cheap; it only holds the storage
/// path. Reuse it across calls to amortize path lookups.
pub struct LineReader {
    storage: LogIndexStorage,
}

impl LineReader {
    /// Create a reader bound to the given storage handle.
    pub fn new(storage: LogIndexStorage) -> Self {
        Self { storage }
    }

    /// Borrow the underlying storage handle.
    pub fn storage(&self) -> &LogIndexStorage {
        &self.storage
    }

    /// Read a half-open line range `[start_line, end_line)` from `path`,
    /// using the offsets in storage. See module docs for full contract.
    pub fn read_lines(
        &self,
        path: &str,
        start_line: u64,
        end_line: u64,
        max_bytes: usize,
    ) -> AppResult<ReadLinesResult> {
        // 1. Inverted range — reject before touching storage.
        if start_line > end_line {
            return Err(AppError::Validation(format!(
                "invalid range: start_line ({}) > end_line ({})",
                start_line, end_line
            )));
        }

        // 2. Look up the index row.
        let index = self
            .storage
            .get(path)?
            .ok_or_else(|| AppError::NotFound(format!("no index for path: {}", path)))?;

        // 3. Re-stat the file and reject on fingerprint mismatch — offsets
        //    from a stale index would land on the wrong bytes.
        let path_buf = PathBuf::from(path);
        let (current_fp, _size, _mtime) = FileIndexer::fingerprint(&path_buf)?;
        if current_fp != index.file_fingerprint {
            return Err(AppError::Validation(format!(
                "file changed (fingerprint mismatch): expected {}, got {}",
                index.file_fingerprint, current_fp
            )));
        }

        // 4. Decode the offsets BLOB. Storage layer already maps decode
        //    errors to AppError::Validation via `?`.
        let offsets = decode_u64_le(&index.line_offsets).map_err(|e| {
            AppError::Validation(format!("decode offsets failed: {}", e))
        })?;

        // 5. Range vs. line_count. `end_line == line_count` is allowed and
        //    means "read to end of file".
        let line_count = index.line_count;
        if end_line > line_count {
            return Err(AppError::Validation(format!(
                "end_line ({}) > line_count ({})",
                end_line, line_count
            )));
        }
        if start_line > line_count {
            return Err(AppError::Validation(format!(
                "start_line ({}) > line_count ({})",
                start_line, line_count
            )));
        }

        // 6. Compute byte range. `offsets[end_line]` is the sentinel
        //    (`file_size`) for `end_line == line_count`; we still go
        //    through the array branch to be explicit.
        let start_offset = offsets[start_line as usize];
        let end_offset = if (end_line as usize) < offsets.len() {
            offsets[end_line as usize]
        } else {
            index.file_size
        };
        let range_len = end_offset - start_offset;

        // 7. Empty range — nothing to read, fingerprint already verified.
        if range_len == 0 {
            return Ok(ReadLinesResult {
                lines: Vec::new(),
                truncated: false,
                bytes_read: 0,
            });
        }

        // 8. Apply max_bytes cap. Anything beyond is dropped; we set
        //    `truncated = true` and only return complete lines.
        let max_bytes_u64 = max_bytes as u64;
        let (read_len, truncated) = if range_len > max_bytes_u64 {
            (max_bytes_u64, true)
        } else {
            (range_len, false)
        };

        // 9. Seek + bounded read.
        let mut file = fs::File::open(&path_buf)?;
        file.seek(SeekFrom::Start(start_offset))?;
        let mut buffer = vec![0u8; read_len as usize];
        file.read_exact(&mut buffer)?;

        // 10. When truncated, cut at the last `\n` so we never return a
        //     partial final line. If no `\n` falls inside the window the
        //     range started mid-line and we have nothing complete to
        //     return.
        let effective_end = if truncated {
            match buffer.iter().rposition(|&b| b == b'\n') {
                Some(pos) => pos + 1, // include the `\n`
                None => 0,            // no complete line in window
            }
        } else {
            buffer.len()
        };

        if effective_end == 0 {
            return Ok(ReadLinesResult {
                lines: Vec::new(),
                truncated: true,
                bytes_read: 0,
            });
        }

        // 11. Decode using the file's detected encoding, then split on `\n`.
        //     If the effective slice ended with `\n`, the final split element
        //     is the empty string — drop it so the caller never sees a phantom
        //     empty line.
        let decoded = decode_line(&buffer[..effective_end], &index.encoding);
        let mut lines: Vec<String> = decoded
            .split('\n')
            .map(String::from)
            .collect();
        if buffer[..effective_end].last() == Some(&b'\n') {
            if matches!(lines.last(), Some(s) if s.is_empty()) {
                lines.pop();
            }
        }

        Ok(ReadLinesResult {
            lines,
            truncated,
            bytes_read: effective_end,
        })
    }
}

// ---------------------------------------------------------------------------
// Tests — written TDD-first (run this file alone to see them go RED before
// the real implementation is added).
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    // -- shared fixture helpers ----------------------------------------

    fn unique_dir(label: &str) -> (tempdir::Guard, PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "eaide-logviewer-reader-{}-{}",
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

    /// Build a single tempdir containing both the data file and the
    /// `log_index.db`, index the file, and return the (guard, store, file
    /// path) triple. Tests MUST keep the guard alive for the entire test
    /// body (typically via `let (_g, store, path) = ...`) so the tempdir
    /// is not removed before `read_lines` is called.
    fn setup_indexed(
        label: &str,
        contents: &[u8],
    ) -> (tempdir::Guard, LogIndexStorage, PathBuf) {
        let (g, dir) = unique_dir(label);
        let file_path = dir.join("data.log");
        std::fs::write(&file_path, contents).expect("write file");
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

    /// Open a fresh, empty `log_index.db` with no rows indexed.
    fn empty_store(label: &str) -> (tempdir::Guard, LogIndexStorage) {
        let (g, dir) = unique_dir(label);
        let db_path = dir.join("log_index.db");
        let store = LogIndexStorage::open(&db_path).expect("open storage");
        (g, store)
    }

    // -- 1. Valid range -------------------------------------------------

    #[test]
    fn read_lines_valid_range_returns_correct_lines() {
        // "alpha\nbeta\nomega\n" -> 3 lines, offsets [0, 6, 11, 17]
        let (_g, store, path) = setup_indexed("valid", b"alpha\nbeta\nomega\n");
        let reader = LineReader::new(store);

        let result = reader
            .read_lines(&path.to_string_lossy(), 0, 2, 1024)
            .expect("read_lines ok");
        assert_eq!(result.lines, vec!["alpha".to_string(), "beta".to_string()]);
        assert!(!result.truncated);
        assert_eq!(result.bytes_read, 11);
    }

    #[test]
    fn read_lines_full_range_returns_all_lines() {
        let (_g, store, path) = setup_indexed("full", b"alpha\nbeta\nomega\n");
        let reader = LineReader::new(store);

        let result = reader
            .read_lines(&path.to_string_lossy(), 0, 3, 1024)
            .expect("read_lines ok");
        assert_eq!(
            result.lines,
            vec!["alpha".to_string(), "beta".to_string(), "omega".to_string()]
        );
        assert!(!result.truncated);
        assert_eq!(result.bytes_read, 17);
    }

    // -- 2. Empty file --------------------------------------------------

    #[test]
    fn read_lines_empty_file_returns_empty_vec() {
        let (_g, store, path) = setup_indexed("empty", b"");
        let reader = LineReader::new(store);

        // Empty file: line_count=0, only valid call is (0, 0) -> empty.
        let result = reader
            .read_lines(&path.to_string_lossy(), 0, 0, 1024)
            .expect("read_lines ok for empty range on empty file");
        assert!(result.lines.is_empty());
        assert!(!result.truncated);
        assert_eq!(result.bytes_read, 0);
    }

    // -- 3. Final unterminated line preserved ---------------------------

    #[test]
    fn read_lines_unterminated_final_line_preserved() {
        // "alpha\nbeta\nomega" -> 3 lines, offsets [0, 6, 11, 15]
        let (_g, store, path) = setup_indexed("unterm", b"alpha\nbeta\nomega");
        let reader = LineReader::new(store);

        let result = reader
            .read_lines(&path.to_string_lossy(), 2, 3, 1024)
            .expect("read_lines ok");
        assert_eq!(result.lines, vec!["omega".to_string()]);
    }

    #[test]
    fn read_lines_strips_only_lf_preserves_cr() {
        // "alpha\r\nbeta" (10 bytes) -> 2 lines:
        //   line 0 "alpha" is terminated by \r\n -> we strip \n only -> "alpha\r"
        //   line 1 "beta"  has no terminator
        let (_g, store, path) = setup_indexed("crlf", b"alpha\r\nbeta");
        let reader = LineReader::new(store);

        let result = reader
            .read_lines(&path.to_string_lossy(), 0, 2, 1024)
            .expect("read_lines ok");
        assert_eq!(result.lines, vec!["alpha\r".to_string(), "beta".to_string()]);
    }

    // -- 4. Range edge cases --------------------------------------------

    #[test]
    fn read_lines_start_equals_end_returns_empty() {
        let (_g, store, path) = setup_indexed("empty_range", b"alpha\nbeta\nomega\n");
        let reader = LineReader::new(store);

        let result = reader
            .read_lines(&path.to_string_lossy(), 1, 1, 1024)
            .expect("read_lines ok");
        assert!(result.lines.is_empty());
        assert!(!result.truncated);
        assert_eq!(result.bytes_read, 0);
    }

    // -- 5. Out-of-range errors -----------------------------------------

    #[test]
    fn read_lines_start_line_out_of_range_errors() {
        let (_g, store, path) = setup_indexed("start_oor", b"alpha\nbeta\nomega\n");
        let reader = LineReader::new(store);

        // line_count = 3; start=4 is past the end.
        let err = reader
            .read_lines(&path.to_string_lossy(), 4, 4, 1024)
            .expect_err("start past end must error");
        assert!(
            matches!(err, AppError::Validation(_)),
            "expected Validation, got {:?}",
            err
        );
    }

    #[test]
    fn read_lines_end_line_out_of_range_errors() {
        let (_g, store, path) = setup_indexed("end_oor", b"alpha\nbeta\nomega\n");
        let reader = LineReader::new(store);

        let err = reader
            .read_lines(&path.to_string_lossy(), 0, 4, 1024)
            .expect_err("end past file end must error");
        assert!(
            matches!(err, AppError::Validation(_)),
            "expected Validation, got {:?}",
            err
        );
    }

    #[test]
    fn read_lines_inverted_range_errors() {
        let (_g, store, path) = setup_indexed("inverted", b"alpha\nbeta\nomega\n");
        let reader = LineReader::new(store);

        let err = reader
            .read_lines(&path.to_string_lossy(), 2, 1, 1024)
            .expect_err("start > end must error");
        assert!(
            matches!(err, AppError::Validation(_)),
            "expected Validation, got {:?}",
            err
        );
    }

    // -- 6. Missing index -----------------------------------------------

    #[test]
    fn read_lines_missing_index_errors() {
        let (_g, store) = empty_store("missing_db");
        let reader = LineReader::new(store);

        let err = reader
            .read_lines("C:/no/such/file.log", 0, 1, 1024)
            .expect_err("missing index must error");
        assert!(
            matches!(err, AppError::NotFound(_)),
            "expected NotFound, got {:?}",
            err
        );
    }

    // -- 7. Stale fingerprint -------------------------------------------

    #[test]
    fn read_lines_stale_fingerprint_errors() {
        // Index a 1-byte file, then grow it so the fingerprint changes.
        let (_g, store, path) = setup_indexed("stale", b"x");
        let reader = LineReader::new(store);

        // Grow the file -> size changes -> fingerprint changes.
        std::fs::write(&path, b"xxxxxxxxxxxxxxxxxxxx").expect("rewrite");

        let err = reader
            .read_lines(&path.to_string_lossy(), 0, 1, 1024)
            .expect_err("stale fingerprint must error");
        // Contract: the read is rejected (Validation) with a message that
        // mentions "fingerprint" so callers can show a useful re-index
        // prompt.
        assert!(
            matches!(err, AppError::Validation(_)),
            "expected Validation, got {:?}",
            err
        );
        let msg = format!("{}", err).to_lowercase();
        assert!(
            msg.contains("fingerprint"),
            "expected fingerprint-related message, got {:?}",
            err
        );
    }

    // -- 8. max_bytes truncation ----------------------------------------

    #[test]
    fn read_lines_max_bytes_truncates_when_above_range() {
        // 5 lines "line0\n..line4\n" = 30 bytes total.
        let content: Vec<u8> = (0..5)
            .flat_map(|i| format!("line{}\n", i).into_bytes())
            .collect();
        let (_g, store, path) = setup_indexed("trunc_above", &content);
        let reader = LineReader::new(store);

        // Request full range (30 bytes) but only allow 12 -> fits exactly
        // "line0\nline1\n" -> two complete lines, truncated = true.
        let result = reader
            .read_lines(&path.to_string_lossy(), 0, 5, 12)
            .expect("read_lines ok");
        assert_eq!(
            result.lines,
            vec!["line0".to_string(), "line1".to_string()]
        );
        assert!(result.truncated, "range > max_bytes must set truncated=true");
        assert_eq!(result.bytes_read, 12);
    }

    #[test]
    fn read_lines_max_bytes_drops_partial_final_line() {
        // Same 5-line fixture, allow 16 bytes. We read 16 bytes:
        //   "line0\nline1\nline2"  <- "line2" is partial, must be dropped.
        let content: Vec<u8> = (0..5)
            .flat_map(|i| format!("line{}\n", i).into_bytes())
            .collect();
        let (_g, store, path) = setup_indexed("trunc_partial", &content);
        let reader = LineReader::new(store);

        let result = reader
            .read_lines(&path.to_string_lossy(), 0, 5, 16)
            .expect("read_lines ok");
        assert_eq!(
            result.lines,
            vec!["line0".to_string(), "line1".to_string()]
        );
        assert!(result.truncated);
    }

    #[test]
    fn read_lines_max_bytes_larger_than_range_does_not_truncate() {
        let content: Vec<u8> = (0..5)
            .flat_map(|i| format!("line{}\n", i).into_bytes())
            .collect();
        let (_g, store, path) = setup_indexed("no_trunc", &content);
        let reader = LineReader::new(store);

        let result = reader
            .read_lines(&path.to_string_lossy(), 0, 5, 1024)
            .expect("read_lines ok");
        assert_eq!(result.lines.len(), 5);
        assert!(!result.truncated);
        assert_eq!(result.bytes_read, content.len());
    }
}

// ---------------------------------------------------------------------------
// Smoke compile-time marker (kept consistent with the other logviewer sub-
// modules: an empty `cfg(test)` inner module so the file always compiles
// even if `#[cfg(test)]` is the only thing keeping the tests alive).
// ---------------------------------------------------------------------------
#[cfg(test)]
mod _reader_compile_smoke {}
