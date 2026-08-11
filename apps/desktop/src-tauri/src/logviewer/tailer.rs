//! Phase 2F+ V1.5 — Tail -f 实时文件监控。
//!
//! 基于 `notify` crate 监听文件变更 + `BufReader` 增量读取新增行。
//! 每读到一批新行即通过 Tauri Event `logviewer://tail-line` 推送到前端，
//! 让 React 端可以实时追加到 VirtualLineList 底部。
//!
//! ## 架构
//!
//! ```text
//! tail_start(path) → TailSession { session_id, handle }
//!   ↓ spawn_blocking
//!   notify Watcher → Event::Modify → seek to last_pos → read new lines
//!   → app.emit("logviewer://tail-line", { session_id, lines, byte_offset })
//! tail_stop(session_id) → drop handle → Watcher 析构 → 线程退出
//! ```
//!
//! ## 安全边界
//!
//! - 只监听文件 Modify 事件（不监听 Create/Delete/Re name —— 文件被删后
//!   新文件同名 inode 不同，notify 可能跟丢；前端应检测并提示用户重开）
//! - 最大 tail 行数限制（`MAX_TAIL_LINES_PER_FLUSH = 500`）防止单次
//!   event payload 过大导致 Tauri IPC 阻塞
//! - 编码假设 UTF-8（非 UTF-8 字节用 replacement char 替换）
//! - 文件轮转检测：若文件 size 变小，认为发生了 truncation/rotation，
//!   重新 seek 到文件头

use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader, Seek, SeekFrom};
use std::path::PathBuf;
use std::sync::{Arc, Mutex, atomic::{AtomicBool, Ordering}};

use notify::{Config, Event, EventKind, RecommendedWatcher, RecursiveMode, Watcher};
use serde::{Deserialize, Serialize};
use tauri::Emitter;

/// Tail 会话 ID（UUIDv4 hex）。
pub type TailSessionId = String;

/// 单次 event 最大推送行数。
const MAX_TAIL_LINES_PER_FLUSH: usize = 500;

/// notify 轮询间隔（毫秒）。
const WATCHER_POLL_MS: u64 = 100;

// ---- 公开数据结构 -------------------------------------------------------

/// 前端收到的单行 tail 事件 payload。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TailLineEvent {
    pub session_id: TailSessionId,
    pub lines: Vec<String>,
    /// 当前已读到的字节偏移（供前端显示进度）。
    pub byte_offset: u64,
}

/// Tail 会话快照（`tail_status` 命令返回）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TailSessionInfo {
    pub session_id: TailSessionId,
    pub path: String,
    pub active: bool,
    pub bytes_read: u64,
    pub lines_read: u64,
}

// ---- 内部会话 -----------------------------------------------------------

struct TailSession {
    path: PathBuf,
    cancel: Arc<AtomicBool>,
    bytes_read: u64,
    lines_read: u64,
}

// ---- Tail 管理器 ---------------------------------------------------------

pub struct TailManager {
    sessions: Arc<Mutex<HashMap<TailSessionId, TailSession>>>,
}

impl TailManager {
    pub fn new() -> Self {
        Self {
            sessions: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// 启动 tail 监听。返回 session_id。
    ///
    /// `app` 用于 emit Tauri Event；`on_error` 在 watcher 出错时回调
    /// （例如文件被删除后 notify 报错）。
    pub fn start(
        &self,
        path: PathBuf,
        app: tauri::AppHandle,
    ) -> Result<TailSessionId, String> {
        // 快速失败：路径必须存在且是文件
        let meta = std::fs::metadata(&path).map_err(|e| format!("tail: stat failed: {}", e))?;
        if !meta.is_file() {
            return Err(format!("tail: path is not a file: {:?}", path));
        }

        let session_id = uuid::Uuid::new_v4().to_string();
        let cancel = Arc::new(AtomicBool::new(false));
        let cancel_clone = cancel.clone();
        let path_clone = path.clone();
        let app_clone = app.clone();
        let sessions = self.sessions.clone();
        let sid_clone = session_id.clone();

        // 记录会话
        {
            let mut guard = sessions.lock().map_err(|e| format!("tail: lock: {}", e))?;
            guard.insert(session_id.clone(), TailSession {
                path: path.clone(),
                cancel: cancel.clone(),
                bytes_read: 0,
                lines_read: 0,
            });
        }

        // 后台线程：notify + 增量读取
        std::thread::spawn(move || {
            if let Err(e) = run_tail(&path_clone, &sid_clone, &cancel_clone, &app_clone, &sessions) {
                let _ = app_clone.emit("logviewer://tail-error", serde_json::json!({
                    "session_id": sid_clone,
                    "error": e,
                }));
            }
            // 清理会话
            if let Ok(mut guard) = sessions.lock() {
                guard.remove(&sid_clone);
            }
        });

        Ok(session_id)
    }

    /// 停止 tail 会话。返回 `true` 表示找到并停止，`false` 表示不存在。
    pub fn stop(&self, session_id: &str) -> bool {
        if let Ok(guard) = self.sessions.lock() {
            if let Some(session) = guard.get(session_id) {
                session.cancel.store(true, Ordering::SeqCst);
                return true;
            }
        }
        false
    }

    /// 获取会话信息。`None` 表示会话不存在。
    pub fn info(&self, session_id: &str) -> Option<TailSessionInfo> {
        let guard = self.sessions.lock().ok()?;
        let session = guard.get(session_id)?;
        Some(TailSessionInfo {
            session_id: session_id.to_string(),
            path: session.path.to_string_lossy().to_string(),
            active: !session.cancel.load(Ordering::SeqCst),
            bytes_read: session.bytes_read,
            lines_read: session.lines_read,
        })
    }

    /// 列出所有会话。
    pub fn list(&self) -> Vec<TailSessionInfo> {
        let guard = match self.sessions.lock() {
            Ok(g) => g,
            Err(_) => return vec![],
        };
        guard.iter().map(|(id, s)| TailSessionInfo {
            session_id: id.clone(),
            path: s.path.to_string_lossy().to_string(),
            active: !s.cancel.load(Ordering::SeqCst),
            bytes_read: s.bytes_read,
            lines_read: s.lines_read,
        }).collect()
    }

    /// 停止所有会话。返回停止数量。
    pub fn stop_all(&self) -> usize {
        let guard = match self.sessions.lock() {
            Ok(g) => g,
            Err(_) => return 0,
        };
        let count = guard.len();
        for session in guard.values() {
            session.cancel.store(true, Ordering::SeqCst);
        }
        count
    }
}

// ---- 核心 tail 循环 ------------------------------------------------------

fn run_tail(
    path: &std::path::Path,
    session_id: &str,
    cancel: &Arc<AtomicBool>,
    app: &tauri::AppHandle,
    sessions: &Arc<Mutex<HashMap<TailSessionId, TailSession>>>,
) -> Result<(), String> {
    // 1. 打开文件，seek 到末尾
    let file = File::open(path).map_err(|e| format!("tail: open: {}", e))?;
    let file_size = file.metadata().map(|m| m.len()).unwrap_or(0);
    let mut reader = BufReader::new(file);
    reader.seek(SeekFrom::Start(file_size)).map_err(|e| format!("tail: seek: {}", e))?;

    // 2. 创建 notify watcher
    let (tx, rx) = std::sync::mpsc::channel();
    let mut watcher = RecommendedWatcher::new(
        move |res: Result<Event, notify::Error>| {
            let _ = tx.send(res);
        },
        Config::default().with_poll_interval(std::time::Duration::from_millis(WATCHER_POLL_MS)),
    ).map_err(|e| format!("tail: watcher create: {}", e))?;

    watcher
        .watch(path, RecursiveMode::NonRecursive)
        .map_err(|e| format!("tail: watch: {}", e))?;

    // 3. 主循环
    let mut last_size = file_size;
    let mut total_bytes = file_size;
    let mut total_lines: u64 = 0;

    loop {
        if cancel.load(Ordering::SeqCst) {
            break;
        }

        // 等待 notify 事件（带超时以便检查 cancel 标志）
        match rx.recv_timeout(std::time::Duration::from_millis(250)) {
            Ok(Ok(event)) => {
                // 只关心 Modify 事件
                if !matches!(event.kind, EventKind::Modify(_)) {
                    continue;
                }
            }
            Ok(Err(_)) => {
                // notify 内部错误 → 忽略继续
                continue;
            }
            Err(std::sync::mpsc::RecvTimeoutError::Timeout) => {
                // 超时 → 检查 cancel 继续循环
                continue;
            }
            Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => {
                break; // channel 断开
            }
        }

        // 检查文件是否被 truncate/rotation
        let current_size = match std::fs::metadata(path) {
            Ok(m) => m.len(),
            Err(_) => {
                let _ = app.emit("logviewer://tail-error", serde_json::json!({
                    "session_id": session_id,
                    "error": "file disappeared or became inaccessible",
                }));
                break;
            }
        };

        if current_size < last_size {
            // 文件被截断/轮转：回到文件头
            let file = File::open(path).map_err(|e| format!("tail: reopen: {}", e))?;
            reader = BufReader::new(file);
            reader.seek(SeekFrom::Start(0)).map_err(|e| format!("tail: reseek: {}", e))?;
            total_bytes = 0;
            total_lines = 0;
        }

        // 增量读取新行
        let mut new_lines: Vec<String> = vec![];
        let mut buf = String::new();
        loop {
            buf.clear();
            match reader.read_line(&mut buf) {
                Ok(0) => break, // EOF
                Ok(n) => {
                    total_bytes += n as u64;
                    new_lines.push(buf.trim_end_matches(&['\r', '\n'][..]).to_string());
                    if new_lines.len() >= MAX_TAIL_LINES_PER_FLUSH {
                        break;
                    }
                }
                Err(_) => break,
            }
        }

        last_size = current_size;

        if !new_lines.is_empty() {
            total_lines += new_lines.len() as u64;

            // 更新会话统计
            if let Ok(mut guard) = sessions.lock() {
                if let Some(session) = guard.get_mut(session_id) {
                    session.bytes_read = total_bytes;
                    session.lines_read = total_lines;
                }
            }

            let _ = app.emit("logviewer://tail-line", TailLineEvent {
                session_id: session_id.to_string(),
                lines: new_lines,
                byte_offset: total_bytes,
            });
        }
    }

    Ok(())
}

// ===========================================================================
// Tests
// ===========================================================================

#[cfg(test)]
mod tests {
    use super::*;
    

    #[test]
    fn tail_manager_start_stop() {
        let dir = std::env::temp_dir().join(format!("eaide-tailer-test-{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let file_path = dir.join("test.log");
        std::fs::write(&file_path, b"line 1\n").unwrap();

        let mgr = TailManager::new();

        // start 需要一个真实的 AppHandle；在单测中我们无法创建，
        // 所以这里只验证构造和 stop 路径。
        // （集成测试由 Tauri invoke 测试覆盖）
        let _ = mgr; // 确保构造不 panic

        // stop 不存在 session
        assert!(!mgr.stop("nonexistent"));

        // info 不存在 session
        assert!(mgr.info("nonexistent").is_none());

        // list 空
        assert!(mgr.list().is_empty());

        // stop_all 空
        assert_eq!(mgr.stop_all(), 0);

        std::fs::remove_dir_all(&dir).unwrap();
    }

    #[test]
    fn tail_manager_stop_all_counts() {
        let mgr = TailManager::new();
        assert_eq!(mgr.stop_all(), 0);

        // 手动插入一个会话验证 stop_all 路径
        {
            let mut guard = mgr.sessions.lock().unwrap();
            guard.insert("test-1".into(), TailSession {
                path: PathBuf::from("/tmp/test.log"),
                cancel: Arc::new(AtomicBool::new(false)),
                bytes_read: 0,
                lines_read: 0,
            });
        }
        assert_eq!(mgr.stop_all(), 1);
        // stop_all 不清除，仅设 cancel → 会话保留但 active=false
        assert_eq!(mgr.list().len(), 1);
        assert!(!mgr.list()[0].active);
    }

    #[test]
    fn tail_session_info_serialization() {
        let info = TailSessionInfo {
            session_id: "abc-123".into(),
            path: "/tmp/test.log".into(),
            active: true,
            bytes_read: 1024,
            lines_read: 42,
        };
        let json = serde_json::to_value(&info).unwrap();
        assert_eq!(json["session_id"], "abc-123");
        assert_eq!(json["active"], true);
        assert_eq!(json["bytes_read"], 1024);
        assert_eq!(json["lines_read"], 42);
    }

    #[test]
    fn tail_line_event_serialization() {
        let event = TailLineEvent {
            session_id: "s1".into(),
            lines: vec!["hello".into(), "world".into()],
            byte_offset: 2048,
        };
        let json = serde_json::to_value(&event).unwrap();
        assert_eq!(json["session_id"], "s1");
        assert_eq!(json["lines"].as_array().unwrap().len(), 2);
        assert_eq!(json["byte_offset"], 2048);
    }

    // 编译期断言：常量值在合理范围（不炸 IPC 也不丢行）
    const _: () = assert!(MAX_TAIL_LINES_PER_FLUSH >= 50);
    const _: () = assert!(MAX_TAIL_LINES_PER_FLUSH <= 2000);
}
