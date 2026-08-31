//! SSE 桥 —— 生产可用级。
//!
//! 每个 run 一个 EventSource 消费者，工作流程：
//!   1. POST 到 FastAPI `/chat/{run_id}/stream` 启动流
//!   2. 监听 EventSource 流的 `Event::Message` 事件
//!   3. 每条事件都重新发成一条类型化的 Tauri Event（`agent://*` 通道）
//!   4. 支持取消（关闭 EventSource）
//!   5. 错误以 `agent://error` 事件上报
//!
//! Webview 跨不过 Tauri 的 CSP 直接连 127.0.0.1:8765，所以所有流都从这个
//! Rust 任务代理出去。

use std::collections::HashMap;
use std::sync::Arc;

use futures_util::StreamExt;
use reqwest_eventsource::Event;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri::{AppHandle, Emitter};
use tokio::sync::{mpsc, Mutex};

use crate::error::{AppError, AppResult};

/// 会话上下文单条消息（2026-08-06）：前端把当前 tab 最近几轮对话
/// 随 agent_chat 传进来，Rust 侧不解析语义，原样拼进 chat 请求体的
/// `history` 字段，后端注入 graph 初始 messages（解决跨轮次上下文丢失）。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HistoryMsg {
    pub role: String,
    pub content: String,
}


/// Channel name mapping. Must match `apps/desktop/src/ipc/events.ts::EVT`.
pub mod channel {
    pub const MESSAGE:  &str = "agent://message";
    pub const TOOL_CALL: &str = "agent://tool_call";
    pub const TOOL_RESULT: &str = "agent://tool_result";
    pub const TRACE:    &str = "agent://trace";
    pub const APPROVAL: &str = "agent://approval";
    pub const LOG:      &str = "agent://log";
    pub const DONE:     &str = "agent://done";
    pub const ERROR:    &str = "agent://error";
    // 流保活心跳（BUGFIX #161）：后端每 15s 无图块时下发，转发给前端看门狗感知流存活
    pub const HEARTBEAT: &str = "agent://heartbeat";
    pub const SKILL_MATCHED: &str = "agent://skill_matched";   // Phase 2D V0
    // Phase 2C V0 LLM 路由
    pub const LLM_ROUTE_DECIDED: &str = "agent://llm_route_decided";
    pub const LLM_DEGRADED: &str = "agent://llm_degraded";
    pub const LLM_BUDGET_ALERT: &str = "agent://llm_budget_alert";
    // Phase 17 缓存命中统计
    pub const LLM_CACHE_STATS: &str = "agent://llm_cache_stats";

    // Phase 2G V1.3 业务功能点导航 —— SSE 三处同步（CLAUDE.md §4）
    pub const BIZNAV_YAML_RELOADED: &str = "agent://biznav_yaml_reloaded";
    pub const BIZNAV_FEATURE_AFFECTED: &str = "agent://biznav_feature_affected";
    pub const BIZNAV_EXTRACTION_DONE: &str = "agent://biznav_extraction_done";

    // Phase 4 V0 本地端侧模型 —— SSE 三处同步（CLAUDE.md §4）
    pub const LOCALAI_READY: &str = "agent://localai_ready";
    pub const LOCALAI_ERROR: &str = "agent://localai_error";

    // Phase 2F+ V1 日志分析 —— SSE 三处同步（CLAUDE.md §4）
    pub const LOG_ANALYSIS_STARTED: &str = "agent://log_analysis_started";
    pub const LOG_ANALYSIS_DONE: &str = "agent://log_analysis_done";
    pub const LOG_ANALYSIS_ERROR: &str = "agent://log_analysis_error";

    // Phase 12 V0/V1 多智能体调度 —— SSE 三处同步（CLAUDE.md §4）
    pub const SUB_AGENT_SPAWN: &str = "agent://sub_agent_spawn";
    pub const SUB_AGENT_DONE: &str = "agent://sub_agent_done";
    pub const SUB_AGENT_PROGRESS: &str = "agent://sub_agent_progress";

    // Phase 13 DSpark 推测解码 —— SSE 三处同步（CLAUDE.md §4）
    pub const DSPARK_ACCELERATION_STATUS: &str = "agent://dspark_acceleration_status";

    // Phase 1B V1 原生工具 —— SSE 三处同步（CLAUDE.md §4）
    // started/done/denied 三事件：dispatcher 在执行前后 emit，sse_bridge 转发给 Webview
    pub const BUILTIN_TOOL_STARTED: &str = "agent://builtin_tool_started";
    pub const BUILTIN_TOOL_DONE: &str = "agent://builtin_tool_done";
    pub const BUILTIN_TOOL_DENIED: &str = "agent://builtin_tool_denied";

    // Phase 14 V0 图像处理 —— SSE 三处同步（CLAUDE.md §4）
    // started/done/error 三事件：api.py 在每个处理前后 emit，sse_bridge 转发给 Webview
    pub const IMAGE_PROCESSING_STARTED: &str = "agent://image_processing_started";
    pub const IMAGE_PROCESSING_DONE: &str = "agent://image_processing_done";
    pub const IMAGE_PROCESSING_ERROR: &str = "agent://image_processing_error";

    // Phase 2B V0 SSH 会话 —— SSE 三处同步（CLAUDE.md §4）
    // connected/disconnected/command_done/error 四事件
    pub const SSH_CONNECTED: &str = "agent://ssh_connected";
    pub const SSH_DISCONNECTED: &str = "agent://ssh_disconnected";
    pub const SSH_COMMAND_DONE: &str = "agent://ssh_command_done";
    pub const SSH_ERROR: &str = "agent://ssh_error";

    // Phase 5 V0 审核专家 —— SSE 三处同步（CLAUDE.md §4）
    // task_pending/task_decided/evidence_added/compliance_done 四事件
    pub const AUDIT_TASK_PENDING: &str = "agent://audit_task_pending";
    pub const AUDIT_TASK_DECIDED: &str = "agent://audit_task_decided";
    pub const AUDIT_EVIDENCE_ADDED: &str = "agent://audit_evidence_added";
    pub const AUDIT_COMPLIANCE_DONE: &str = "agent://audit_compliance_done";

    // 文档风险合规审核 —— SSE 三处同步（CLAUDE.md §4）
    pub const DOC_REVIEW_STARTED: &str = "agent://doc_review_started";
    pub const DOC_REVIEW_CLASSIFIED: &str = "agent://doc_review_classified";
    pub const DOC_REVIEW_FINDINGS_READY: &str = "agent://doc_review_findings_ready";
    pub const DOC_REVIEW_FAILED: &str = "agent://doc_review_failed";

    // Phase 7 V0 数据专家 —— SSE 三处同步（CLAUDE.md §4）
    // data_query_result/data_python_result/data_chart_ready/data_export_done 四事件
    pub const DATA_QUERY_RESULT: &str = "agent://data_query_result";
    pub const DATA_PYTHON_RESULT: &str = "agent://data_python_result";
    pub const DATA_CHART_READY: &str = "agent://data_chart_ready";
    pub const DATA_EXPORT_DONE: &str = "agent://data_export_done";
    // Phase 7 补齐：大结果集 WS + Arrow 中继事件（非 SSE，Rust 主动 emit）
    // data_stream_chunk：Arrow 批/元数据帧；data_stream_done：流结束
    pub const DATA_STREAM_CHUNK: &str = "agent://data_stream_chunk";
    pub const DATA_STREAM_DONE: &str = "agent://data_stream_done";

    // Phase 6 V1.5 会话管理 —— SSE 三处同步（CLAUDE.md §4）
    // compression_applied：CompressionRouter 选策略后实际执行压缩 → 前端可显示压缩提示
    // memory_consolidated：L3 情景记忆 → 语义记忆蒸馏完成（后台任务）
    pub const SESSION_COMPRESSION_APPLIED: &str = "agent://session_compression_applied";
    pub const SESSION_MEMORY_CONSOLIDATED: &str = "agent://session_memory_consolidated";

    // Phase 15 V0 前端实时预览引擎 —— SSE 三处同步（CLAUDE.md §4）
    // hmr_connected：HMR WebSocket 连接成功（含 session_id + status）
    // hmr_disconnected：HMR 断开 / 重连中（含 session_id + status）
    // build_error：Vite 编译错误（含 session_id + error + file/line/column）
    pub const PREVIEW_HMR_CONNECTED: &str = "agent://preview_hmr_connected";
    pub const PREVIEW_HMR_DISCONNECTED: &str = "agent://preview_hmr_disconnected";
    pub const PREVIEW_BUILD_ERROR: &str = "agent://preview_build_error";

    // Phase 18 双框架 —— SSE 三处同步（CLAUDE.md §4）
    // mode_routed：ModeRouter 路由结果；repair_attempt：Auto-Repair 进度；
    // auto_decision：自动模式决策（只转发，Rust 侧不解析业务语义）
    pub const MODE_ROUTED: &str = "agent://mode_routed";
    pub const REPAIR_ATTEMPT: &str = "agent://repair_attempt";
    pub const AUTO_DECISION: &str = "agent://auto_decision";

    // 执行过程可视化（Claude Code 式） —— SSE 三处同步（CLAUDE.md §4）
    // run_started：流建立第一帧；tool_progress：工具阶段文案；
    // shell_chunk：shell 流式输出；file_write_preview：写前 unified diff 预览。
    // （只转发，Rust 侧不解析业务语义）
    pub const RUN_STARTED: &str = "agent://run_started";
    pub const TOOL_PROGRESS: &str = "agent://tool_progress";
    pub const SHELL_CHUNK: &str = "agent://shell_chunk";
    pub const FILE_WRITE_PREVIEW: &str = "agent://file_write_preview";

    // Phase 19 V0 自进化闭环 —— SSE 三处同步（CLAUDE.md §4）
    // evolution_insight_created：失败反思产出新经验（前端经验库页刷新）
    // skill_draft_ready（V1）：技能蒸馏草稿待审（前端技能页草稿区刷新）
    pub const EVOLUTION_INSIGHT_CREATED: &str = "agent://evolution_insight_created";
    pub const SKILL_DRAFT_READY: &str = "agent://skill_draft_ready";
    // evolution_experiment_done（V1.5）：Prompt 影子优化实验完成（前端实验面板刷新）
    pub const EVOLUTION_EXPERIMENT_DONE: &str = "agent://evolution_experiment_done";
}


/// Per-run handle — used to cancel a stream from the UI.
#[derive(Clone)]
pub struct RunHandle {
    /// Sender that signals the SSE task to stop.
    cancel_tx: mpsc::Sender<()>,
}

impl RunHandle {
    pub async fn cancel(&self) {
        let _ = self.cancel_tx.send(()).await;
    }
}


pub struct SseBridge {
    /// Active runs, keyed by run_id.
    runs: Arc<Mutex<HashMap<String, RunHandle>>>,
    /// Base URL of the FastAPI agent.
    base_url: String,
    /// HTTP client with reasonable defaults. None 在降级模式下表示不可用。
    client: Option<reqwest::Client>,
    /// Tauri app handle for emitting events.
    app: AppHandle,
}

impl SseBridge {
    pub fn new(app: AppHandle, base_url: String) -> AppResult<Self> {
        let client = reqwest::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(5))
            .read_timeout(std::time::Duration::from_secs(60))
            .build()
            .map_err(|e| {
                AppError::Config(format!("reqwest client builder failed: {e}"))
            })?;
        Ok(Self {
            runs: Arc::new(Mutex::new(HashMap::new())),
            base_url,
            client: Some(client),
            app,
        })
    }

    /// 空 SSE 桥 —— reqwest 客户端构建失败时兜底用。
    /// 所有需要 client 的方法都会返回 Err，窗口仍能打开。
    pub fn empty(app: AppHandle, base_url: String) -> Self {
        Self {
            runs: Arc::new(Mutex::new(HashMap::new())),
            base_url,
            client: None,
            app,
        }
    }

    /// Start a new chat run. Returns a handle for later cancellation.
    ///
    /// Phase 18：work_mode / autonomy 可选透传进 chat 请求体（Rust 不解析语义）。
    /// history：会话上下文（当前 tab 最近几轮对话），透传进请求体。
    /// last_skill_id / task_id / task_title（2026-08-26）：skill 粘性与任务级工作目录，同样只透传。
    /// pinned_skill_id（2026-08-28）：`/` 指令强钉的 skill，同样只透传。
    // 本函数是透传缝（参数原样进 chat 请求体，不解析语义），聚成结构体反而
    // 增加上下游改动面 → 豁免 too_many_arguments
    #[allow(clippy::too_many_arguments)]
    pub async fn start_run(
        self: Arc<Self>,
        run_id: String,
        prompt: String,
        work_mode: Option<String>,
        autonomy: Option<String>,
        inference_mode: Option<String>,
        history: Option<Vec<HistoryMsg>>,
        page_context: Option<Value>,
        model_override: Option<String>,
        history_summary: Option<String>,
        last_skill_id: Option<String>,
        pinned_skill_id: Option<String>,
        task_id: Option<String>,
        task_title: Option<String>,
    ) -> AppResult<RunHandle> {
        crate::agent_manager::app_log(&format!("[sse_bridge] start_run 进入 run_id={}, prompt.len={}", run_id, prompt.len()));
        // Cancel any pre-existing run with the same id
        self.cancel_run(&run_id).await.ok();

        let (cancel_tx, mut cancel_rx) = mpsc::channel::<()>(1);
        let handle = RunHandle { cancel_tx: cancel_tx.clone() };
        self.runs.lock().await.insert(run_id.clone(), handle.clone());

        // Build the streaming request
        let url = format!("{}/chat/{}/stream", self.base_url, run_id);
        let client = match self.client.as_ref() {
            Some(c) => c,
            None => {
                let _ = self.app.emit(channel::ERROR, serde_json::json!({
                    "runId": run_id,
                    "message": "SSE bridge running in degraded mode (no HTTP client)",
                }));
                return Err(AppError::Config("SSE bridge has no HTTP client".into()));
            }
        };
        let req = client
            .post(&url)
            .header("Accept", "text/event-stream")
            .header("Cache-Control", "no-cache")
            .json(&{
                let mut body = serde_json::json!({ "prompt": prompt });
                if let Some(wm) = work_mode {
                    body["workMode"] = Value::String(wm);
                }
                if let Some(am) = autonomy {
                    body["autonomy"] = Value::String(am);
                }
                if let Some(im) = inference_mode {
                    body["inferenceMode"] = Value::String(im);
                }
                if let Some(h) = history {
                    if !h.is_empty() {
                        body["history"] = serde_json::to_value(&h)
                            .unwrap_or(Value::Array(vec![]));
                    }
                }
                // 页面上下文（2026-08-14）：前端当前页签/场景，透传进 chat 请求体
                // 的 `context` 字段（后端注入 intent / decompose prompt）
                if let Some(ctx) = page_context {
                    if !ctx.is_null() {
                        body["context"] = ctx;
                    }
                }
                // 会话模型选择（2026-08-17）：模型管理 backend 名，后端置顶回答链
                if let Some(mo) = model_override {
                    if !mo.trim().is_empty() {
                        body["modelOverride"] = Value::String(mo);
                    }
                }
                // 历史压缩摘要（2026-08-17）：断点之前旧对话的摘要，后端注入
                // graph 初始 messages 的 system 消息（置于 history 之前）
                if let Some(hs) = history_summary {
                    if !hs.trim().is_empty() {
                        body["historySummary"] = Value::String(hs);
                    }
                }
                // Skill 粘性（2026-08-26）：上一轮命中的 skill，本轮追问/修改时继承；
                // Skill 强钉（2026-08-28）：`/` 手动指定时后端短路路由器固定用它
                if let Some(sid) = last_skill_id {
                    if !sid.trim().is_empty() {
                        body["lastSkillId"] = Value::String(sid);
                    }
                }
                if let Some(pid) = pinned_skill_id {
                    if !pid.trim().is_empty() {
                        body["pinnedSkillId"] = Value::String(pid);
                    }
                }
                // 任务级工作目录（2026-08-26）：一个聊天页签 = 一个任务文件夹，
                // 后端据此解析产出文件落盘目录并在 done 事件里回传 taskId/taskDir
                if let Some(tid) = task_id {
                    if !tid.trim().is_empty() {
                        body["taskId"] = Value::String(tid);
                        if let Some(tt) = task_title {
                            if !tt.trim().is_empty() {
                                body["taskTitle"] = Value::String(tt);
                            }
                        }
                    }
                }
                body
            });

        let mut es = reqwest_eventsource::EventSource::new(req)
            .map_err(|e| AppError::Config(format!("EventSource build failed: {e}")))?;

        let app = self.app.clone();
        let runs = self.runs.clone();
        let run_id_for_task = run_id.clone();

        // Spawn a task that drives the stream until done/cancelled/error.
        tokio::spawn(async move {
            tokio::select! {
                _ = async {
                    while let Some(event) = es.next().await {
                        match event {
                            Ok(Event::Open) => {
                                // Connection established — emit a log line.
                                let _ = app.emit(channel::LOG, serde_json::json!({
                                    "line": format!("[run {}] SSE connection open", run_id_for_task),
                                }));
                            }
                            Ok(Event::Message(msg)) => {
                                let channel = map_event_to_channel(&msg.event);
                                let payload: Value = serde_json::from_str(&msg.data)
                                    .unwrap_or_else(|_| Value::String(msg.data.clone()));
                                let _ = app.emit(channel, payload);
                            }
                            Err(reqwest_eventsource::Error::InvalidStatusCode(status, _)) => {
                                let _ = app.emit(channel::ERROR, serde_json::json!({
                                    "runId": run_id_for_task,
                                    "status": status.as_u16(),
                                    "message": "agent returned non-2xx",
                                }));
                                break;
                            }
                            Err(reqwest_eventsource::Error::InvalidContentType(_, _)) => {
                                let _ = app.emit(channel::ERROR, serde_json::json!({
                                    "runId": run_id_for_task,
                                    "message": "agent did not return text/event-stream",
                                }));
                                break;
                            }
                            Err(e) => {
                                let _ = app.emit(channel::ERROR, serde_json::json!({
                                    "runId": run_id_for_task,
                                    "message": e.to_string(),
                                }));
                                break;
                            }
                        }
                    }
                } => {
                    let _ = app.emit(channel::DONE, serde_json::json!({
                        "runId": run_id_for_task,
                        "reason": "stream_closed",
                    }));
                }
                _ = cancel_rx.recv() => {
                    es.close();
                    let _ = app.emit(channel::LOG, serde_json::json!({
                        "line": format!("[run {}] cancelled by user", run_id_for_task),
                    }));
                    // 2026-08-07：取消后补发 done 事件，让前端解除 busy 状态
                    // （否则 ThinkingIndicator / 停止按钮会卡在运行态）
                    let _ = app.emit(channel::DONE, serde_json::json!({
                        "runId": run_id_for_task,
                        "reason": "cancelled",
                    }));
                }
            }
            // Cleanup registry
            runs.lock().await.remove(&run_id_for_task);
        });

        Ok(handle)
    }

    /// Cancel an active run.
    pub async fn cancel_run(&self, run_id: &str) -> AppResult<()> {
        let handle = self.runs.lock().await.remove(run_id);
        if let Some(h) = handle {
            crate::agent_manager::app_log(&format!("[sse_bridge] cancel_run run_id={}", run_id));
            h.cancel().await;
        }
        Ok(())
    }

    /// Phase 18：通用 JSON POST（如 /autonomy/confirm）。返回响应体 JSON。
    pub async fn post_json(&self, path: &str, body: Value) -> AppResult<Value> {
        let url = format!("{}{}", self.base_url, path);
        let client = self.client.as_ref().ok_or_else(|| {
            AppError::Config("SSE bridge has no HTTP client (degraded mode)".into())
        })?;
        let resp = client.post(&url).json(&body).send().await?;
        let status = resp.status();
        if !status.is_success() {
            return Err(AppError::Config(format!("POST {} returned {}", path, status)));
        }
        let text = resp.text().await.unwrap_or_default();
        Ok(serde_json::from_str(&text).unwrap_or(Value::Null))
    }

    /// POST a HITL approval decision to the FastAPI agent.
    pub async fn post_approval(
        &self,
        approval_id: &str,
        decision: &str,
        operator: Option<String>,
    ) -> AppResult<()> {
        crate::agent_manager::app_log(&format!("[sse_bridge] post_approval {} -> {} ({})", approval_id, decision, operator.as_deref().unwrap_or("?")));
        let url = format!("{}/approval/{}", self.base_url, approval_id);
        let mut body = serde_json::json!({ "decision": decision });
        if let Some(op) = operator {
            body["operator"] = Value::String(op);
        }
        let client = self.client.as_ref().ok_or_else(|| {
            AppError::Config("SSE bridge has no HTTP client (degraded mode)".into())
        })?;
        let resp = client
            .post(&url)
            .json(&body)
            .send()
            .await?;
        let status = resp.status();
        if !status.is_success() {
            crate::agent_manager::app_log(&format!("[sse_bridge] post_approval {} -> HTTP {}", url, status));
            return Err(AppError::Config(format!(
                "approval POST returned {}",
                status
            )));
        }
        crate::agent_manager::app_log(&format!("[sse_bridge] post_approval {} -> 2xx，OK", url));
        Ok(())
    }

    /// Active run count — surfaced for diagnostics / tests.
    pub async fn active_runs(&self) -> usize {
        self.runs.lock().await.len()
    }
}


/// Map an SSE event name to the Tauri channel. Falls back to `agent://log`.
fn map_event_to_channel(event_name: &str) -> &'static str {
    match event_name {
        "message"     => channel::MESSAGE,
        "tool_call"   => channel::TOOL_CALL,
        "tool_result" => channel::TOOL_RESULT,
        "trace"       => channel::TRACE,
        "approval"    => channel::APPROVAL,
        "done"        => channel::DONE,
        "error"       => channel::ERROR,
        "heartbeat"   => channel::HEARTBEAT,   // BUGFIX #161 看门狗心跳
        "skill_matched" => channel::SKILL_MATCHED,   // Phase 2D V0
        "llm_route_decided" => channel::LLM_ROUTE_DECIDED,   // Phase 2C V0
        "llm_degraded"      => channel::LLM_DEGRADED,
        "llm_budget_alert"  => channel::LLM_BUDGET_ALERT,
        "llm_cache_stats"   => channel::LLM_CACHE_STATS,     // Phase 17
        // Phase 2G V1.3：业务功能点 SSE 三处同步（CLAUDE.md §4）
        "biznav_yaml_reloaded"   => channel::BIZNAV_YAML_RELOADED,
        "biznav_feature_affected" => channel::BIZNAV_FEATURE_AFFECTED,
        "biznav_extraction_done"  => channel::BIZNAV_EXTRACTION_DONE,
        // Phase 4 V0：本地端侧模型 SSE 三处同步（CLAUDE.md §4）
        "localai_ready" => channel::LOCALAI_READY,
        "localai_error" => channel::LOCALAI_ERROR,
        // Phase 2F+ V1：日志分析 SSE 三处同步（CLAUDE.md §4）
        "log_analysis_started" => channel::LOG_ANALYSIS_STARTED,
        "log_analysis_done"    => channel::LOG_ANALYSIS_DONE,
        "log_analysis_error"   => channel::LOG_ANALYSIS_ERROR,
        // Phase 12 V0/V1：多智能体调度 SSE 三处同步（CLAUDE.md §4）
        "sub_agent_spawn"    => channel::SUB_AGENT_SPAWN,
        "sub_agent_done"     => channel::SUB_AGENT_DONE,
        "sub_agent_progress" => channel::SUB_AGENT_PROGRESS,
        // Phase 13 DSpark：推测解码 SSE 三处同步（CLAUDE.md §4）
        "dspark_acceleration_status" => channel::DSPARK_ACCELERATION_STATUS,
        // Phase 1B V1：原生工具 SSE 三处同步（CLAUDE.md §4）
        "builtin_tool_started" => channel::BUILTIN_TOOL_STARTED,
        "builtin_tool_done"    => channel::BUILTIN_TOOL_DONE,
        "builtin_tool_denied"  => channel::BUILTIN_TOOL_DENIED,
        // Phase 14 V0：图像处理 SSE 三处同步（CLAUDE.md §4）
        "image_processing_started" => channel::IMAGE_PROCESSING_STARTED,
        "image_processing_done"    => channel::IMAGE_PROCESSING_DONE,
        "image_processing_error"   => channel::IMAGE_PROCESSING_ERROR,
        // Phase 2B V0：SSH 会话 SSE 三处同步（CLAUDE.md §4）
        "ssh_connected"     => channel::SSH_CONNECTED,
        "ssh_disconnected"  => channel::SSH_DISCONNECTED,
        "ssh_command_done"  => channel::SSH_COMMAND_DONE,
        "ssh_error"         => channel::SSH_ERROR,
        // Phase 5 V0：审核专家 SSE 三处同步（CLAUDE.md §4）
        "audit_task_pending"    => channel::AUDIT_TASK_PENDING,
        "audit_task_decided"    => channel::AUDIT_TASK_DECIDED,
        "audit_evidence_added"  => channel::AUDIT_EVIDENCE_ADDED,
        "audit_compliance_done"  => channel::AUDIT_COMPLIANCE_DONE,
        // 文档风险合规审核 SSE 三处同步（CLAUDE.md §4）
        "doc_review_started"        => channel::DOC_REVIEW_STARTED,
        "doc_review_classified"     => channel::DOC_REVIEW_CLASSIFIED,
        "doc_review_findings_ready" => channel::DOC_REVIEW_FINDINGS_READY,
        "doc_review_failed"         => channel::DOC_REVIEW_FAILED,
        // Phase 7 V0：数据专家 SSE 三处同步（CLAUDE.md §4）
        "data_query_result"    => channel::DATA_QUERY_RESULT,
        "data_python_result"   => channel::DATA_PYTHON_RESULT,
        "data_chart_ready"     => channel::DATA_CHART_READY,
        "data_export_done"     => channel::DATA_EXPORT_DONE,
        // Phase 15 V0：前端实时预览引擎 SSE 三处同步（CLAUDE.md §4）
        "preview_hmr_connected"    => channel::PREVIEW_HMR_CONNECTED,
        "preview_hmr_disconnected" => channel::PREVIEW_HMR_DISCONNECTED,
        "preview_build_error"      => channel::PREVIEW_BUILD_ERROR,
        // Phase 18：双框架 SSE 三处同步（CLAUDE.md §4）
        "mode_routed"    => channel::MODE_ROUTED,
        "repair_attempt" => channel::REPAIR_ATTEMPT,
        "auto_decision"  => channel::AUTO_DECISION,
        // 执行过程可视化（Claude Code 式） SSE 三处同步（CLAUDE.md §4）
        "run_started"        => channel::RUN_STARTED,
        "tool_progress"      => channel::TOOL_PROGRESS,
        "shell_chunk"        => channel::SHELL_CHUNK,
        "file_write_preview" => channel::FILE_WRITE_PREVIEW,
        // Phase 19 V0：自进化闭环 SSE 三处同步（CLAUDE.md §4）
        "evolution_insight_created" => channel::EVOLUTION_INSIGHT_CREATED,
        "skill_draft_ready"         => channel::SKILL_DRAFT_READY,   // Phase 19 V1
        "evolution_experiment_done" => channel::EVOLUTION_EXPERIMENT_DONE,   // Phase 19 V1.5
        _             => channel::LOG,
    }
}
