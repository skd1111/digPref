//! Phase 2F+ Task 1 — SQLite-backed file index storage + u64 LE BLOB offset codec.
//!
//! 设计要点：
//!   * **物理隔离**：`log_index.db` 与审计 / 会话 / 代码导航等数据库完全分离，
//!     路径由调用方传入（默认 `%APPDATA%/Enterprise AI IDE/log_index.db`）。
//!   * **WAL + NORMAL synchronous**：高并发读 + 单写不阻塞；fsync 频率合理。
//!   * **每个公开方法打开一条短连接**：避免在 `spawn_blocking` 任务之间长持
//!     锁。PRAGMA + schema 是幂等的 (`CREATE TABLE IF NOT EXISTS`)，开销可
//!     忽略，确保 Task 5 的并发索引 / 搜索 worker 不会相互阻塞。
//!   * **upsert 在单事务中执行**：旧行在事务提交前不动；失败回滚后旧行完整
//!     保留，符合"老索引必须活到新索引成功提交"的红线。
//!   * **u64 LE 偏移**：空文件编码为 `[]`；末尾无换行的最后一行偏移指向
//!     `file_size`（哨兵），便于 reader 用半开区间 `[start, end)` 切片。
//!
//! 该模块不引入新 crate；`regex` 待 Task 4 由 Cargo.toml 加（lock 已传递可用）。

use std::path::{Path, PathBuf};

use rusqlite::{params, Connection, Transaction};
use serde::Serialize;
use thiserror::Error;

use crate::error::{AppError, AppResult};

// ----------------------------------------------------------------------------
// Errors
// ----------------------------------------------------------------------------

/// Codec-layer 错误。仅在编解码失败时产生；store 层的 SQLite 错误走 `AppError::Sqlite`。
#[derive(Debug, Error)]
pub enum SchemaError {
    #[error("blob length {len} is not a multiple of {expected_multiple} bytes (u64)")]
    BlobLength { len: usize, expected_multiple: usize },
}

impl From<SchemaError> for AppError {
    fn from(e: SchemaError) -> Self {
        AppError::Validation(e.to_string())
    }
}

// ----------------------------------------------------------------------------
// DTOs
// ----------------------------------------------------------------------------

/// One row of `file_index`. Fields mirror `phase-2f-plus-log-viewer.md` §3.1.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileIndex {
    pub file_path: String,
    pub file_fingerprint: String,
    pub file_size: u64,
    pub line_count: u64,
    /// Binary u64 LE offsets BLOB. Always `len() % 8 == 0`.
    pub line_offsets: Vec<u8>,
    pub encoding: String,
    pub last_modified: i64,
    pub indexed_at: i64,
    pub index_version: i64,
}

/// Lightweight index status for a path — returned by `logviewer_index_status`.
///
/// Wire shape (matches the JS shim's `LogViewerIndexStatus`):
/// ```json
/// { "kind": "missing" }
/// { "kind": "ready", "line_count": 12345, "indexed_at": 1700000000 }
/// ```
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
#[serde(tag = "kind", rename_all = "lowercase")]
pub enum IndexStatus {
    Missing,
    Ready { line_count: u64, indexed_at: i64 },
}

// ----------------------------------------------------------------------------
// Storage handle
// ----------------------------------------------------------------------------

/// SQLite-backed storage for the `file_index` table. Holds the database path
/// only; each public operation opens its own short-lived connection so that
/// `spawn_blocking` workers do not contend for a shared lock.
#[derive(Debug, Clone)]
pub struct LogIndexStorage {
    path: PathBuf,
}

impl LogIndexStorage {
    /// Open an on-disk database at `path`, creating parent dirs as needed and
    /// applying WAL + NORMAL synchronous + schema.
    pub fn open(path: &Path) -> AppResult<Self> {
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent)?;
            }
        }
        let conn = Connection::open(path)?;
        apply_pragmas(&conn)?;
        apply_schema(&conn)?;
        Ok(Self {
            path: path.to_path_buf(),
        })
    }

    /// Path the storage was opened with.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Fetch one row by primary key.
    pub fn get(&self, file_path: &str) -> AppResult<Option<FileIndex>> {
        let conn = self.open_conn()?;
        let mut stmt = conn.prepare(
            "SELECT file_path, file_fingerprint, file_size, line_count, line_offsets, \
             encoding, last_modified, indexed_at, index_version \
             FROM file_index WHERE file_path = ?1",
        )?;
        let mut rows = stmt.query(params![file_path])?;
        if let Some(row) = rows.next()? {
            Ok(Some(row_to_index(row)?))
        } else {
            Ok(None)
        }
    }

    /// Insert-or-replace in a single SQLite transaction.
    /// The previous row remains untouched until `COMMIT` succeeds; failure
    /// rolls back so the caller can retry without corrupting the existing
    /// index (matches the "preserve old index until new index commits" rule).
    pub fn upsert(&self, idx: &FileIndex) -> AppResult<()> {
        let mut conn = self.open_conn()?;
        let tx: Transaction<'_> = conn.transaction()?;
        tx.execute(
            "INSERT INTO file_index(file_path, file_fingerprint, file_size, \
             line_count, line_offsets, encoding, last_modified, indexed_at, \
             index_version) \
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9) \
             ON CONFLICT(file_path) DO UPDATE SET \
                file_fingerprint = excluded.file_fingerprint, \
                file_size = excluded.file_size, \
                line_count = excluded.line_count, \
                line_offsets = excluded.line_offsets, \
                encoding = excluded.encoding, \
                last_modified = excluded.last_modified, \
                indexed_at = excluded.indexed_at, \
                index_version = excluded.index_version",
            params![
                idx.file_path,
                idx.file_fingerprint,
                idx.file_size as i64,
                idx.line_count as i64,
                idx.line_offsets,
                idx.encoding,
                idx.last_modified,
                idx.indexed_at,
                idx.index_version,
            ],
        )?;
        tx.commit()?;
        Ok(())
    }

    /// Lightweight status snapshot — does not perform fingerprint comparison
    /// (that check belongs to the Task 2 indexer / Task 3 reader, which see
    /// the actual file on disk). This method merely answers "is there a row?".
    pub fn status(&self, file_path: &str) -> AppResult<IndexStatus> {
        let conn = self.open_conn()?;
        let mut stmt = conn.prepare(
            "SELECT line_count, indexed_at FROM file_index WHERE file_path = ?1",
        )?;
        let mut rows = stmt.query(params![file_path])?;
        if let Some(row) = rows.next()? {
            let line_count: i64 = row.get(0)?;
            let indexed_at: i64 = row.get(1)?;
            Ok(IndexStatus::Ready {
                line_count: line_count as u64,
                indexed_at,
            })
        } else {
            Ok(IndexStatus::Missing)
        }
    }

    /// Open a short-lived connection. Each public operation gets its own so
    /// concurrent `spawn_blocking` workers do not block on a shared lock.
    fn open_conn(&self) -> AppResult<Connection> {
        let conn = Connection::open(&self.path)?;
        apply_pragmas(&conn)?;
        apply_schema(&conn)?;
        Ok(conn)
    }
}

// ----------------------------------------------------------------------------
// Private helpers — pragmas / schema
// ----------------------------------------------------------------------------

fn apply_pragmas(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(
        "PRAGMA journal_mode = WAL;\n\
         PRAGMA synchronous = NORMAL;\n\
         PRAGMA busy_timeout = 5000;",
    )
}

fn apply_schema(conn: &Connection) -> rusqlite::Result<()> {
    conn.execute_batch(SCHEMA_SQL)
}

/// Schema for Task 1's `file_index` table only. Search cache, tail sessions,
/// and log analysis cache tables come in later tasks; each will append its
/// own block here.
const SCHEMA_SQL: &str = r#"
CREATE TABLE IF NOT EXISTS file_index (
    file_path        TEXT PRIMARY KEY,
    file_fingerprint TEXT NOT NULL,
    file_size        INTEGER NOT NULL,
    line_count       INTEGER NOT NULL,
    line_offsets     BLOB NOT NULL,
    encoding         TEXT NOT NULL DEFAULT 'utf-8',
    last_modified    INTEGER NOT NULL,
    indexed_at       INTEGER NOT NULL,
    index_version    INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_file_index_modified ON file_index(last_modified DESC);
"#;

fn row_to_index(row: &rusqlite::Row<'_>) -> rusqlite::Result<FileIndex> {
    Ok(FileIndex {
        file_path: row.get(0)?,
        file_fingerprint: row.get(1)?,
        file_size: row.get::<_, i64>(2)? as u64,
        line_count: row.get::<_, i64>(3)? as u64,
        line_offsets: row.get(4)?,
        encoding: row.get(5)?,
        last_modified: row.get(6)?,
        indexed_at: row.get(7)?,
        index_version: row.get(8)?,
    })
}

// ----------------------------------------------------------------------------
// Codec: u64 little-endian BLOB
// ----------------------------------------------------------------------------

/// Encode a slice of u64 values into a single little-endian BLOB.
///
/// Each value becomes 8 bytes; an empty slice yields an empty `Vec<u8>`.
/// The BLOB is "well-formed" iff its byte length is a multiple of 8.
pub fn encode_u64_le(values: &[u64]) -> Vec<u8> {
    let mut out = Vec::with_capacity(values.len() * 8);
    for &v in values {
        out.extend_from_slice(&v.to_le_bytes());
    }
    out
}

/// Decode a BLOB previously produced by [`encode_u64_le`]. Returns
/// [`SchemaError::BlobLength`] when `blob.len() % 8 != 0`. The caller is
/// responsible for any further semantic checks (offset ordering, sentinel
/// presence, etc.) which belong to the indexer / reader layers.
pub fn decode_u64_le(blob: &[u8]) -> Result<Vec<u64>, SchemaError> {
    if blob.len() % 8 != 0 {
        return Err(SchemaError::BlobLength {
            len: blob.len(),
            expected_multiple: 8,
        });
    }
    let mut out = Vec::with_capacity(blob.len() / 8);
    let mut buf = [0u8; 8];
    for chunk in blob.chunks_exact(8) {
        buf.copy_from_slice(chunk);
        out.push(u64::from_le_bytes(buf));
    }
    Ok(out)
}

#[cfg(test)]
mod _storage_compile_smoke {}
