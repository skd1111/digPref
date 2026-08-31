//! 共享应用状态 —— 通过 `State<AppState>` 注入到每个 Tauri 命令。
//!
//! 生命周期：
//!   1. `try_init()` 尝试加载配置、打开审计数据库、启动 Agent 子进程
//!   2. 任一子系统失败 → 调用方应改用 `fallback()`，window 仍打开但功能降级
//!   3. Drop 时自动终止 Agent 子进程（AgentManager::drop）
//!
//! Phase 2F+ Task 5：追加 `logviewer: Arc<LogViewerState>` 字段。
//! **LogViewerState 永不失败** —— 它的构造器只分配内存 + Mutex，没有任何 I/O，
//! 因此 `try_init` 和 `fallback` 都直接构造一个新实例而不传 Err。
//! 这样保证 logviewer 子系统永远不会阻止主窗口打开（即使 audit / mcp 都炸了）。

use std::path::PathBuf;
use std::sync::Arc;
use tauri::AppHandle;

use crate::agent_manager::{app_log, AgentManager};
use crate::audit::store::AuditStore;
use crate::config::AppConfig;
use crate::error::{AppError, AppResult};
use crate::logviewer::{LogViewerState, TailManager};
use crate::mcp_client::McpRegistry;
use crate::stream::SseBridge;


pub struct AppState {
    pub config: Arc<AppConfig>,
    pub audit: Option<Arc<AuditStore>>,
    pub mcp: Option<Arc<McpRegistry>>,
    pub sse: Arc<SseBridge>,
    /// Phase 2F+ Task 5 — 大文件查看器任务注册表。
    /// 始终存在（不会因为初始化失败而消失），命令侧可以无脑调
    /// `state.logviewer.submit_index(...)` 等方法。
    pub logviewer: Arc<LogViewerState>,
    /// Phase 2F+ V1.5 — Tail -f 会话管理器。
    pub tailer: Arc<TailManager>,
    /// Agent 进程管理器 —— 持有子进程 handle，Drop 时自动终止
    #[allow(dead_code)]
    agent: AgentManager,
}

impl AppState {
    /// 尝试完整初始化。任何子系统失败都 log 后返回 Err，
    /// 调用方（setup hook）应改用 `fallback()` 让窗口继续打开。
    pub fn try_init(app: &AppHandle) -> AppResult<Self> {
        app_log("[state] try_init 开始");
        let config = AppConfig::load(app).map(Arc::new)
            .map_err(|e| { app_log(&format!("[state] AppConfig::load 失败: {}", e)); e })?;
        app_log(&format!(
            "[state] AppConfig 加载成功: agent_base_url={}, audit_db={}, mcp_config={}, devtools_enabled={}",
            config.agent_base_url,
            config.audit_db_path.display(),
            config.mcp_config_path.display(),
            config.devtools_enabled
        ));

        // 启动 Agent 子进程（永不失败 —— 失败只记日志，不阻止应用打开）
        let agent = AgentManager::start(&config);

        let audit = AuditStore::open(&config.audit_db_path)
            .map(Arc::new)
            .map_err(|e| {
                app_log(&format!("[state] AuditStore::open 失败: {}", e));
                e
            })?;
        app_log(&format!("[state] AuditStore 已打开（{}）", config.audit_db_path.display()));

        let mcp = McpRegistry::load(&config.mcp_config_path)
            .map(Arc::new)
            .map_err(|e| {
                app_log(&format!("[state] McpRegistry::load 失败: {}", e));
                e
            })?;
        app_log(&format!("[state] McpRegistry 已加载（{}）", config.mcp_config_path.display()));

        let sse = Arc::new(SseBridge::new(app.clone(), config.agent_base_url.clone())?);
        app_log(&format!("[state] SseBridge 已构造（base={}）", config.agent_base_url));

        // Phase 2F+ Task 5 — logviewer 注册表。永远成功（无 I/O）。
        // Task 6: 绑定实际 storage path（<audit_db_parent>/log_index.db），
        // 否则后台 spawn_blocking worker 只能落到 default_storage_path()
        // （单元测试 / 没设 APPDATA 时是 CWD 下 log_index.db —— 不安全）。
        let logviewer_storage_path = config
            .audit_db_path
            .parent()
            .map(|p| p.join("log_index.db"))
            .unwrap_or_else(|| PathBuf::from("log_index.db"));
        let logviewer = Arc::new(LogViewerState::with_storage_path(logviewer_storage_path));
        app_log("[state] LogViewerState 已构造（注册表空）");

        app_log("[state] AppState::try_init 成功");
        let tailer = Arc::new(TailManager::new());
        Ok(Self { config, audit: Some(audit), mcp: Some(mcp), sse, logviewer, tailer, agent })
    }

    /// 降级初始化的 State —— 即使 try_init 失败也保证 window 能打开。
    /// audit / mcp 在降级模式下为 None，命令侧需处理 None。
    pub fn fallback(app: &AppHandle, reason: &AppError) -> Self {
        app_log(&format!(
            "[state] 降级模式启动，原因: {} —— audit / mcp / sse 中可能部分不可用",
            reason
        ));

        // 优先用 load 结果；若 load 也失败，则用纯默认值（不去拿 AppHandle 避免循环）
        let config = match AppConfig::load(app) {
            Ok(c) => { app_log("[state] fallback: AppConfig::load 仍能用，继续使用"); Arc::new(c) }
            Err(e) => {
                app_log(&format!("[state] fallback: AppConfig::load 也失败: {} —— 用 safe_defaults", e));
                Arc::new(AppConfig::safe_defaults())
            }
        };

        let agent = AgentManager::start(&config);

        // 即使降级也尝试建 audit / mcp —— 可能其中之一其实可用
        let audit = match AuditStore::open(&config.audit_db_path) {
            Ok(a) => { app_log("[state] fallback: AuditStore 已打开"); Some(Arc::new(a)) }
            Err(e) => { app_log(&format!("[state] fallback: AuditStore 仍失败: {}", e)); None }
        };
        let mcp = match McpRegistry::load(&config.mcp_config_path) {
            Ok(m) => { app_log("[state] fallback: McpRegistry 已加载"); Some(Arc::new(m)) }
            Err(e) => { app_log(&format!("[state] fallback: McpRegistry 仍失败: {}", e)); None }
        };

        let sse = match SseBridge::new(app.clone(), config.agent_base_url.clone()) {
            Ok(b) => Arc::new(b),
            Err(e) => {
                app_log(&format!("[state] SseBridge::new 失败: {} —— 用空实例，SSE 不可用", e));
                // SSE 不可用时构造一个最简实例 —— start_run 等调用会失败但窗口已开
                Arc::new(SseBridge::empty(app.clone(), config.agent_base_url.clone()))
            }
        };

        // Phase 2F+ Task 5 — logviewer 注册表在 fallback 模式下也必须可用。
        // 它无 I/O、永不失败，所以这里直接构造，不走 match / Option。
        // Task 6: 同样绑定到 <audit_db_parent>/log_index.db。
        let logviewer_storage_path = config
            .audit_db_path
            .parent()
            .map(|p| p.join("log_index.db"))
            .unwrap_or_else(|| PathBuf::from("log_index.db"));
        let logviewer = Arc::new(LogViewerState::with_storage_path(logviewer_storage_path));
        app_log("[state] fallback: LogViewerState 已构造（注册表空）");

        app_log("[state] fallback: 降级 State 已构造 —— 主窗口可以打开");
        let tailer = Arc::new(TailManager::new());
        Self { config, audit, mcp, sse, logviewer, tailer, agent }
    }

    /// 取 audit 句柄；None 时返回内存空实例（写入会丢，但 API 不空指针）。
    /// 命令侧可以无脑调 `state.audit_handle().append(...)`，避免重复 unwrap。
    ///
    /// ⚠️ 警告：如果真实审计库打开失败，降级为内存 SQLite 后所有审计条目
    /// 在进程退出时永久丢失。此路径仅在启动失败时的 fallback 中使用，
    /// 生产环境不应走到这里。
    pub fn audit_handle(&self) -> Arc<AuditStore> {
        self.audit.clone().unwrap_or_else(|| {
            app_log("[state] ⚠️ audit_handle 降级为内存 SQLite —— 审计数据在进程退出时永久丢失！");
            Arc::new(AuditStore::empty().unwrap_or_else(|e| {
                // 连内存 SQLite 都打不开 —— 极端情况，panic 走 hook 文件记录
                panic!("AuditStore::empty 失败: {}", e);
            }))
        })
    }

    /// 取 mcp 句柄；None 或 load 失败时返回空注册表。
    pub fn mcp_handle(&self) -> Arc<McpRegistry> {
        self.mcp.clone().unwrap_or_else(|| Arc::new(McpRegistry::empty()))
    }

    /// 取 logviewer 注册表。永远返回 Some —— logviewer 永远不会降级。
    /// 命令侧可以无脑调 `state.logviewer_handle().submit_index(...)`。
    pub fn logviewer_handle(&self) -> Arc<LogViewerState> {
        Arc::clone(&self.logviewer)
    }

    /// 取 tailer 管理器。永远返回 Some。
    pub fn tailer_handle(&self) -> Arc<TailManager> {
        Arc::clone(&self.tailer)
    }

    /// GET 到 Python Agent（Phase 7 dataexpert 命令用）。
    /// 与 commands/biznav.rs 的 agent_url + json_or_err 同模式。
    pub async fn agent_get(&self, path: &str) -> AppResult<serde_json::Value> {
        let url = format!("{}{}", self.config.agent_base_url, path);
        let resp = reqwest::get(&url)
            .await
            .map_err(|e| AppError::Internal(format!("agent_get {}: {}", path, e)))?;
        agent_json_or_err(resp, "agent_get").await
    }

    /// POST 到 Python Agent（Phase 7 dataexpert 命令用）。
    pub async fn agent_post(
        &self,
        path: &str,
        body: serde_json::Value,
    ) -> AppResult<serde_json::Value> {
        let url = format!("{}{}", self.config.agent_base_url, path);
        let client = reqwest::Client::new();
        let resp = client
            .post(&url)
            .json(&body)
            .send()
            .await
            .map_err(|e| AppError::Internal(format!("agent_post {}: {}", path, e)))?;
        agent_json_or_err(resp, "agent_post").await
    }

    /// DELETE 到 Python Agent（Phase 19 经验库删除用；与 agent_post 同模式）。
    pub async fn agent_delete(&self, path: &str) -> AppResult<serde_json::Value> {
        let url = format!("{}{}", self.config.agent_base_url, path);
        let client = reqwest::Client::new();
        let resp = client
            .delete(&url)
            .send()
            .await
            .map_err(|e| AppError::Internal(format!("agent_delete {}: {}", path, e)))?;
        agent_json_or_err(resp, "agent_delete").await
    }
}


/// HTTP 响应 → JSON；非 2xx 时提取 detail 返回 Err（dataexpert 共用辅助）。
async fn agent_json_or_err(
    resp: reqwest::Response,
    op: &str,
) -> AppResult<serde_json::Value> {
    let status = resp.status();
    let json: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| AppError::Internal(format!("{} response parse: {}", op, e)))?;
    if !status.is_success() {
        let detail = json
            .get("detail")
            .and_then(|d| d.as_str())
            .unwrap_or("unknown error");
        return Err(AppError::Internal(format!(
            "agent returned {}: {}",
            status.as_u16(),
            detail
        )));
    }
    Ok(json)
}
