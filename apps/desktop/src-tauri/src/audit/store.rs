//! SQLite-backed audit log. Schema kept in sync with the Python Agent's
//! `services/agent/src/agent/audit/schema.sql`.
//!
//! V1.5 (2026-07-31): 加 5 列（correlation_id / actor_type / event_type /
//! task_id / parent_task_id），用于 Phase 12 子 Agent 决策树回放。Python +
//! Rust 双 schema INSERT 列序必须严格镜像（CLAUDE.md §6 红线）。

use std::path::Path;

use chrono::Utc;
use rusqlite::{params, Connection};

use crate::error::{AppError, AppResult};

pub struct AuditStore {
    conn: Mutex<Connection>,
}

use std::sync::Mutex;

impl AuditStore {
    pub fn open(path: &Path) -> AppResult<Self> {
        // rusqlite::Connection::open 不会自动建父目录，
        // 全新安装时 `%APPDATA%\eaide\` 不存在会报错，导致整个 setup() 失败、窗口不弹出。
        if let Some(parent) = path.parent() {
            if !parent.as_os_str().is_empty() {
                std::fs::create_dir_all(parent)?;
            }
        }
        let conn = Connection::open(path)?;
        // 启用 WAL 模式 —— 允许 Rust 和 Python 进程同时读写同一个数据库
        // 默认的 rollback journal 模式在跨进程并发时会导致频繁 SQLITE_BUSY
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;")?;
        // 应用共享 schema（幂等 CREATE IF NOT EXISTS）
        conn.execute_batch(SCHEMA)?;
        // V1.5：老库 ALTER TABLE ADD COLUMN（CREATE IF NOT EXISTS 已包含新列时跳过）
        for col_def in V15_COLUMNS {
            let col_name = col_def.split_whitespace().next().unwrap_or("");
            let sql = format!("ALTER TABLE audit ADD COLUMN {} DEFAULT NULL", col_def);
            if let Err(e) = conn.execute_batch(&sql) {
                // "duplicate column name" 表示列已存在，忽略；其他错则记录但继续
                let msg = e.to_string().to_lowercase();
                if !msg.contains("duplicate column") {
                    eprintln!(
                        "[audit] ALTER TABLE audit ADD COLUMN {} 失败（旧库迁移跳过）: {}",
                        col_name, e
                    );
                }
            }
        }
        Ok(Self { conn: Mutex::new(conn) })
    }

    /// 向后兼容的旧 API —— 仅写 action / payload / ts / run_id 4 列。
    /// V1.5 之前调用方零改动；新调用方请使用 `append_v15`。
    pub fn append(&self, action: &str, payload: serde_json::Value) -> AppResult<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| AppError::Internal(format!("审计数据库锁 poisoned（前次 panic 导致）: {}", e)))?;
        conn.execute(
            "INSERT INTO audit(action, payload, ts) VALUES (?, ?, ?)",
            params![action, payload.to_string(), Utc::now().to_rfc3339()],
        )?;
        Ok(())
    }

    /// V1.5 新增：写入全 9 列（与 Python audit() INSERT 列序严格镜像）。
    ///
    /// 列序：action / payload / ts / run_id / correlation_id / actor_type /
    ///       event_type / task_id / parent_task_id
    #[allow(clippy::too_many_arguments)]
    pub fn append_v15(
        &self,
        action: &str,
        payload: serde_json::Value,
        run_id: Option<&str>,
        correlation_id: Option<&str>,
        actor_type: Option<&str>,
        event_type: Option<&str>,
        task_id: Option<&str>,
        parent_task_id: Option<&str>,
    ) -> AppResult<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| AppError::Internal(format!("审计数据库锁 poisoned（前次 panic 导致）: {}", e)))?;
        conn.execute(
            concat!(
                "INSERT INTO audit(",
                "  action, payload, ts, run_id,",
                "  correlation_id, actor_type, event_type, task_id, parent_task_id",
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ),
            params![
                action,
                payload.to_string(),
                Utc::now().to_rfc3339(),
                run_id,
                correlation_id,
                actor_type,
                event_type,
                task_id,
                parent_task_id,
            ],
        )?;
        Ok(())
    }

    pub fn search(&self, query: &str, limit: u32) -> AppResult<Vec<serde_json::Value>> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| AppError::Internal(format!("审计数据库锁 poisoned（前次 panic 导致）: {}", e)))?;
        let mut stmt = conn.prepare(
            "SELECT action, payload, ts FROM audit
             WHERE action LIKE ?1 OR payload LIKE ?1
             ORDER BY id DESC LIMIT ?2",
        )?;
        let pat = format!("%{}%", query);
        let rows = stmt.query_map(params![pat, limit], |row| {
            let action: String = row.get(0)?;
            let payload: String = row.get(1)?;
            let ts: String = row.get(2)?;
            Ok(serde_json::json!({
                "action": action,
                "payload": serde_json::from_str::<serde_json::Value>(&payload)
                    .unwrap_or(serde_json::Value::String(payload)),
                "ts": ts,
            }))
        })?;
        let mut out = Vec::new();
        for r in rows { out.push(r?); }
        Ok(out)
    }

    /// V1.5 新增：按 correlation_id 回放一整棵决策树。
    pub fn search_by_correlation(
        &self,
        correlation_id: &str,
        limit: u32,
    ) -> AppResult<Vec<serde_json::Value>> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| AppError::Internal(format!("审计数据库锁 poisoned（前次 panic 导致）: {}", e)))?;
        let mut stmt = conn.prepare(
            concat!(
                "SELECT action, payload, ts, run_id, correlation_id, actor_type, ",
                "       event_type, task_id, parent_task_id ",
                "FROM audit WHERE correlation_id = ?1 ",
                "ORDER BY id ASC LIMIT ?2",
            ),
        )?;
        let rows = stmt.query_map(params![correlation_id, limit], |row| {
            let action: String = row.get(0)?;
            let payload: String = row.get(1)?;
            let ts: String = row.get(2)?;
            let run_id: Option<String> = row.get(3)?;
            let correlation_id: Option<String> = row.get(4)?;
            let actor_type: Option<String> = row.get(5)?;
            let event_type: Option<String> = row.get(6)?;
            let task_id: Option<String> = row.get(7)?;
            let parent_task_id: Option<String> = row.get(8)?;
            Ok(serde_json::json!({
                "action": action,
                "payload": serde_json::from_str::<serde_json::Value>(&payload)
                    .unwrap_or(serde_json::Value::String(payload)),
                "ts": ts,
                "run_id": run_id,
                "correlation_id": correlation_id,
                "actor_type": actor_type,
                "event_type": event_type,
                "task_id": task_id,
                "parent_task_id": parent_task_id,
            }))
        })?;
        let mut out = Vec::new();
        for r in rows { out.push(r?); }
        Ok(out)
    }
}

/// V1.5 新增 5 列的 ALTER TABLE 定义。列序与 schema.sql 镜像。
const V15_COLUMNS: &[&str] = &[
    "correlation_id TEXT",
    "actor_type TEXT",
    "event_type TEXT",
    "task_id TEXT",
    "parent_task_id TEXT",
];

const SCHEMA: &str = include_str!("schema.sql");

impl AuditStore {
    /// 内存版空 AuditStore —— 真实 DB 初始化失败时的兜底，所有方法可用，只是写入会丢失。
    pub fn empty() -> AppResult<Self> {
        let conn = Connection::open_in_memory()?;
        conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;")?;
        conn.execute_batch(SCHEMA)?;
        // 内存版也执行 V15 迁移（虽无意义，保持一致性）
        for col_def in V15_COLUMNS {
            let sql = format!("ALTER TABLE audit ADD COLUMN {} DEFAULT NULL", col_def);
            let _ = conn.execute_batch(&sql);
        }
        Ok(Self { conn: Mutex::new(conn) })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// V1.5：验证 append_v15 写入 9 列无误 + search_by_correlation 能按 correlation_id 拉回。
    #[test]
    fn audit_v15_roundtrip() {
        let store = AuditStore::empty().unwrap();
        let payload = serde_json::json!({"sub_agent_id": "sub-1"});
        store
            .append_v15(
                "SUB_AGENT_SPAWN",
                payload,
                Some("run-1"),
                Some("corr-1"),
                Some("sub_agent"),
                Some("spawn"),
                Some("sub-1"),
                None,
            )
            .unwrap();
        store
            .append_v15(
                "SUB_AGENT_DONE",
                serde_json::json!({"status": "ok"}),
                Some("run-1"),
                Some("corr-1"),
                Some("sub_agent"),
                Some("done"),
                Some("sub-1"),
                None,
            )
            .unwrap();
        let rows = store.search_by_correlation("corr-1", 10).unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["action"], "SUB_AGENT_SPAWN");
        assert_eq!(rows[0]["task_id"], "sub-1");
        assert_eq!(rows[0]["actor_type"], "sub_agent");
        assert_eq!(rows[1]["action"], "SUB_AGENT_DONE");
    }

    /// V1.5：旧 API `append` 仍然只写 3 列（向后兼容）
    #[test]
    fn audit_legacy_append_still_works() {
        let store = AuditStore::empty().unwrap();
        store
            .append("OLD_ACTION", serde_json::json!({"k": "v"}))
            .unwrap();
        let rows = store.search("OLD_ACTION", 10).unwrap();
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["action"], "OLD_ACTION");
    }
}
