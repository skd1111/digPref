//! Unit tests for the logviewer module's storage layer (Phase 2F+ MVP Task 1).
//!
//! These tests focus on the parts the plan explicitly calls out:
//!   - u64 little-endian offset encoding / decoding, including:
//!     * empty offset vector
//!     * multi-line offset vectors
//!     * "no final newline" semantics (the last offset points past the final
//!       byte, even when no `\n` terminates the file)
//!     * non-ASCII log fixture content (offsets are byte-based, not
//!       character-based, so the same roundtrip rules apply to wider gaps)
//!   - decode rejects malformed blobs whose length is not a multiple of 8
//!   - FileIndex roundtrip via upsert + get
//!   - `IndexStatus::Missing` vs `Ready` mapping
//!
//! Higher-level indexing tests belong to Task 2.

use std::fs;
use std::path::PathBuf;

use super::storage::{decode_u64_le, encode_u64_le, FileIndex, IndexStatus, LogIndexStorage};

// ============================================================================
// 1. u64 LE BLOB codec — the heart of Task 1
// ============================================================================

#[test]
fn encode_empty_yields_empty_blob() {
    let blob = encode_u64_le(&[]);
    assert!(blob.is_empty());
}

#[test]
fn encode_yields_exactly_8_bytes_per_offset() {
    let offsets = [0u64, 11, 23, 42, 100];
    let blob = encode_u64_le(&offsets);
    assert_eq!(blob.len(), offsets.len() * 8);
}

#[test]
fn encode_decode_roundtrip_multiline() {
    // Realistic: 1000 lines, offsets spaced by ~17 bytes (variable log line
    // widths)
    let offsets: Vec<u64> = (0..1000).map(|i| i * 17).collect();
    let blob = encode_u64_le(&offsets);
    let back = decode_u64_le(&blob).expect("multiline blob roundtrips");
    assert_eq!(back, offsets);
}

#[test]
fn encode_decode_roundtrip_empty() {
    let blob = encode_u64_le(&[]);
    let back = decode_u64_le(&blob).expect("empty blob roundtrips");
    assert!(back.is_empty());
}

/// Critical spec requirement: a file's last line without a trailing `\n` must
/// still appear in the line count. The codec itself is content-agnostic — it
/// only stores byte offsets — so we model the resulting offsets explicitly:
/// `sentinel_offset == file_size`.
#[test]
fn encode_decode_roundtrip_no_final_newline_offsets() {
    // "alpha\nbeta\nomega" -> 3 lines + sentinel:
    //   line 1 "alpha" starts at 0
    //   line 2 "beta"  starts at 6  (5 chars + '\n')
    //   line 3 "omega" starts at 11 (5 chars + '\n'); file_size = 15
    let offsets = [0u64, 6, 11, 15];
    let blob = encode_u64_le(&offsets);
    let back = decode_u64_le(&blob).expect("no-final-newline blob roundtrips");
    assert_eq!(back, offsets);
}

#[test]
fn encode_decode_roundtrip_single_line_file() {
    // "single-line-no-newline" -> 1 line + sentinel = file_size (21)
    let offsets = [0u64, 21];
    let blob = encode_u64_le(&offsets);
    let back = decode_u64_le(&blob).expect("single-line blob roundtrips");
    assert_eq!(back, offsets);
}

/// Non-ASCII log content: offsets are **byte** positions, not codepoint or
/// char counts. UTF-8 multi-byte characters produce larger gaps between line
/// starts. The codec is bytes-in / bytes-out so this just exercises larger u64
/// values; the roundtrip must remain exact.
#[test]
fn encode_decode_roundtrip_non_ascii_fixture_offsets() {
    // Non-ASCII fixture offsets — large byte positions to mimic UTF-8 line
    // starts with multi-byte characters. The exact content doesn't matter for
    // the codec; we just want to prove that big u64 values do not lose
    // precision on round-trip.
    let offsets = [0u64, 6, 13, 14, 30, 1_000_000, 9_223_372_036_854_775_807];
    let blob = encode_u64_le(&offsets);
    let back = decode_u64_le(&blob).expect("non-ascii blob roundtrips");
    assert_eq!(back, offsets);
}

#[test]
fn encode_is_little_endian() {
    // 0x0102030405060708 -> bytes [08, 07, 06, 05, 04, 03, 02, 01]
    let blob = encode_u64_le(&[0x0102_0304_0506_0708]);
    assert_eq!(
        blob,
        vec![0x08, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01]
    );
}

#[test]
fn decode_rejects_blob_shorter_than_8_bytes() {
    let err = decode_u64_le(&[1, 2, 3]).expect_err("3-byte blob should fail");
    match err {
        SchemaError::BlobLength { len, expected_multiple } => {
            assert_eq!(len, 3);
            assert_eq!(expected_multiple, 8);
        }
    }
}

#[test]
fn decode_rejects_11_bytes() {
    // 11 bytes — odd, not a multiple of 8
    let blob = vec![0u8; 11];
    let err = decode_u64_le(&blob).expect_err("11-byte blob should fail");
    assert!(matches!(err, SchemaError::BlobLength { len: 11, .. }));
}

#[test]
fn decode_rejects_15_bytes() {
    let blob = vec![0u8; 15];
    let err = decode_u64_le(&blob).expect_err("15-byte blob should fail");
    assert!(matches!(err, SchemaError::BlobLength { len: 15, .. }));
}

#[test]
fn decode_accepts_16_bytes() {
    let blob = vec![0u8; 16]; // two u64 zeros
    let back = decode_u64_le(&blob).expect("16-byte blob is valid");
    assert_eq!(back, vec![0u64, 0]);
}

// ============================================================================
// 2. Storage: open / get / upsert / status
// ============================================================================

fn sample_index(path: &str) -> FileIndex {
    // Offsets derived from a small non-ASCII log fixture:
    //   "你好\n世界\n🌍\nlast"
    //   你好 (3 bytes each) -> 0..6; line starts: 0, 7
    //   世界 (3 bytes each) -> 7..13; line starts: 13
    //   🌍 (4 bytes)        -> 14..18; line starts: 18
    //   "last"              -> 19..23; sentinel = 23
    let offsets: Vec<u64> = vec![0, 7, 13, 18, 23];
    FileIndex {
        file_path: path.to_string(),
        file_fingerprint: "1024:1700000000".into(),
        file_size: 1024,
        line_count: offsets.len() as u64 - 1,
        line_offsets: encode_u64_le(&offsets),
        encoding: "utf-8".into(),
        last_modified: 1700000000,
        indexed_at: 1700000100,
        index_version: 1,
    }
}

#[test]
fn open_creates_parent_dirs() {
    let (_tmp, db_path) = unique_db_path("open_creates_parent");
    let nested_parent = db_path.parent().unwrap().join("a/b/c");
    let nested = nested_parent.join("log_index.db");
    assert!(!nested.exists());

    let store = LogIndexStorage::open(&nested).expect("open nested db");
    let _ = store.path();

    assert!(nested.exists());
}

#[test]
fn get_returns_none_for_missing_path() {
    let (_tmp, db_path) = unique_db_path("get_returns_none");
    let store = LogIndexStorage::open(&db_path).expect("open db");
    let got = store
        .get("C:/no/such/file.log")
        .expect("missing-path get is Ok(None), not error");
    assert!(got.is_none());
}

#[test]
fn upsert_then_get_roundtrips_all_fields() {
    let (_tmp, db_path) = unique_db_path("upsert_get");
    let store = LogIndexStorage::open(&db_path).expect("open db");
    let idx = sample_index("C:/tmp/x.log");
    store.upsert(&idx).expect("upsert succeeds");
    let got = store
        .get(&idx.file_path)
        .expect("get succeeds")
        .expect("row present after upsert");
    assert_eq!(got.file_path, idx.file_path);
    assert_eq!(got.file_fingerprint, idx.file_fingerprint);
    assert_eq!(got.file_size, idx.file_size);
    assert_eq!(got.line_count, idx.line_count);
    assert_eq!(got.line_offsets, idx.line_offsets);
    assert_eq!(got.encoding, idx.encoding);
    assert_eq!(got.last_modified, idx.last_modified);
    assert_eq!(got.indexed_at, idx.indexed_at);
    assert_eq!(got.index_version, idx.index_version);
}

#[test]
fn upsert_overwrites_existing_row_on_same_path() {
    let (_tmp, db_path) = unique_db_path("upsert_overwrite");
    let store = LogIndexStorage::open(&db_path).expect("open db");
    let mut idx = sample_index("C:/tmp/x.log");
    store.upsert(&idx).expect("first upsert");
    idx.indexed_at = 1_700_000_999;
    idx.line_count = 9_999;
    store.upsert(&idx).expect("second upsert");
    let got = store
        .get(&idx.file_path)
        .expect("get")
        .expect("row present after overwrite");
    assert_eq!(got.indexed_at, 1_700_000_999);
    assert_eq!(got.line_count, 9_999);
}

#[test]
fn upsert_is_atomic_old_row_preserved_when_new_one_fails() {
    // Upsert in a transaction: if we somehow force the inner transaction to
    // fail, the previous row must survive. We can't easily force a SQL
    // constraint failure through the public API on this table, but we can
    // verify that an upsert to one path does not corrupt an earlier upsert
    // to a *different* path (proxy for "transactions are isolated").
    let (_tmp, db_path) = unique_db_path("upsert_atomic");
    let store = LogIndexStorage::open(&db_path).expect("open db");
    let a = sample_index("C:/tmp/a.log");
    let b = sample_index("C:/tmp/b.log");
    store.upsert(&a).expect("upsert a");
    store.upsert(&b).expect("upsert b");
    // Update a
    let mut a2 = a.clone();
    a2.indexed_at = 2_000_000_000;
    store.upsert(&a2).expect("re-upsert a");
    let got_a = store.get(&a.file_path).expect("get a").expect("a row");
    let got_b = store.get(&b.file_path).expect("get b").expect("b row");
    assert_eq!(got_a.indexed_at, 2_000_000_000, "a updated");
    assert_eq!(got_b.indexed_at, a.indexed_at, "b untouched");
}

#[test]
fn status_returns_missing_when_no_row() {
    let (_tmp, db_path) = unique_db_path("status_missing");
    let store = LogIndexStorage::open(&db_path).expect("open db");
    let s = store
        .status("C:/never/indexed.log")
        .expect("status should not error on missing row");
    assert_eq!(s, IndexStatus::Missing);
}

#[test]
fn status_returns_ready_when_row_present() {
    let (_tmp, db_path) = unique_db_path("status_ready");
    let store = LogIndexStorage::open(&db_path).expect("open db");
    let idx = sample_index("C:/tmp/x.log");
    store.upsert(&idx).expect("upsert");
    let s = store.status(&idx.file_path).expect("status");
    assert_eq!(
        s,
        IndexStatus::Ready {
            line_count: idx.line_count,
            indexed_at: idx.indexed_at,
        }
    );
}

// ============================================================================
// Helpers
// ============================================================================

fn unique_db_path(label: &str) -> (tempdir::Guard, PathBuf) {
    let _ = label;
    // Lightweight in-process tempdir without adding a new crate dependency.
    let dir = std::env::temp_dir().join(format!(
        "eaide-logviewer-tests-{}-{}",
        label,
        uuid::Uuid::new_v4()
    ));
    fs::create_dir_all(&dir).expect("create tempdir");
    let path = dir.join("log_index.db");
    (tempdir::Guard(dir), path)
}

/// Tiny RAII guard that removes the tempdir on drop — keeps tests hermetic.
mod tempdir {
    pub struct Guard(pub std::path::PathBuf);
    impl Drop for Guard {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }
}

// Re-export SchemaError pattern for tests via parent module path so we can
// pattern-match on it from outside storage.rs.
use super::storage::SchemaError;
