//! Phase 2F+ — 大文件查看与本地索引模块。
//!
//! 模块布局：
//!   * `storage`   — SQLite file_index 持久化 + u64 LE BLOB 偏移编解码
//!   * `indexer`   — 全量 / 增量索引构建
//!   * `reader`    — 按行号 seek + 区间读取
//!   * `searcher`  — literal / regex 行内搜索
//!   * `registry`  — 任务注册表 + 共享状态
//!   * `commands`  — Tauri IPC command 入口
//!   * `tailer`    — Tail -f 实时文件监控 (V1.5)

pub mod commands;
pub mod encoding;
pub mod indexer;
pub mod reader;
pub mod registry;
pub mod searcher;
pub mod storage;
pub mod tailer;

#[cfg(test)]
mod tests;

pub use indexer::{FileIndexer, IndexProgress, IndexSummary, IndexerError};
pub use reader::{LineReader, ReadLinesResult};
pub use registry::{
    LogViewerState, SubmitError, TaskEntry, TaskId, TaskKind, TaskSnapshot, TaskStatus,
    FINISHED_TTL_SECS,
};
pub use searcher::{LogSearchMatch, LogSearchResult, LogSearcher, SearchMode};
pub use storage::{
    decode_u64_le, encode_u64_le, FileIndex, IndexStatus, LogIndexStorage, SchemaError,
};
pub use tailer::{TailLineEvent, TailManager, TailSessionId, TailSessionInfo};
