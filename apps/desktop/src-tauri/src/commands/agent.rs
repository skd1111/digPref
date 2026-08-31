//! Agent 相关 Tauri 命令：chat、approval、cancel。
//!
//! 每个命令都是 SseBridge 的薄包装。实际流处理在
//! `crate::stream::SseBridge` 中完成；这里只负责启动/停止信号
//! 并转发 HITL 决策。

use std::sync::Arc;

use tauri::State;
use uuid::Uuid;

use crate::error::AppResult;
use crate::state::AppState;

/// 获取当前操作系统用户名（用于审计日志）。
#[cfg(target_os = "windows")]
fn os_username() -> String {
    std::env::var("USERNAME").unwrap_or_else(|_| "unknown".into())
}

#[cfg(not(target_os = "windows"))]
fn os_username() -> String {
    std::env::var("USER").unwrap_or_else(|_| "unknown".into())
}

/// 启动新的聊天运行。返回 run_id（用于后续取消操作）。
///
/// Phase 18：work_mode / autonomy 可选透传（前端 WorkMode 与会话级自主性），
/// Rust 侧不解析业务语义，只随 chat 请求体进后端。
/// inference_mode：推理性能模式开关（performance 时后端注入完整版双模式提示词）。
/// history：会话上下文（当前 tab 最近几轮对话），Rust 侧不解析，透传后端。
/// page_context：页面上下文（2026-08-14，当前页签/场景），透传后端注入 intent/decompose。
/// model_override：会话模型选择（2026-08-17，模型管理 backend 名），透传后端置顶回答链。
/// last_skill_id：上一轮命中的 skill（2026-08-26，追问/修改时继承，防裸生成）。
/// pinned_skill_id：`/` 指令强钉的 skill（2026-08-28，后端短路路由器固定用它）。
/// task_id / task_title：任务级工作目录（2026-08-26，一个聊天页签 = 一个任务文件夹）。
// 本函数与 start_run 同为透传缝（参数原样进 chat 请求体，不解析语义），
// 聚成结构体反而增加上下游改动面 → 豁免 too_many_arguments
#[allow(clippy::too_many_arguments)]
#[tauri::command]
pub async fn agent_chat(
    state: State<'_, AppState>,
    prompt: String,
    work_mode: Option<String>,
    autonomy: Option<String>,
    inference_mode: Option<String>,
    history: Option<Vec<crate::stream::HistoryMsg>>,
    page_context: Option<serde_json::Value>,
    model_override: Option<String>,
    history_summary: Option<String>,
    last_skill_id: Option<String>,
    pinned_skill_id: Option<String>,
    task_id: Option<String>,
    task_title: Option<String>,
) -> AppResult<String> {
    crate::agent_manager::app_log(&format!("[agent_chat] 收到 prompt，长度={}", prompt.len()));
    // 输入校验：拒绝空 prompt
    let trimmed = prompt.trim();
    if trimmed.is_empty() {
        crate::agent_manager::app_log("[agent_chat] 拒绝：prompt 为空");
        return Err(crate::error::AppError::Validation("prompt 不能为空".into()));
    }
    if trimmed.len() > 100_000 {
        crate::agent_manager::app_log(&format!("[agent_chat] 拒绝：prompt 过长 ({}>100000)", trimmed.len()));
        return Err(crate::error::AppError::Validation(
            "prompt 过长（最大 100KB）".into(),
        ));
    }

    let run_id = Uuid::new_v4().to_string();
    let bridge = Arc::clone(&state.sse);
    crate::agent_manager::app_log(&format!("[agent_chat] run_id={}，start_run 即将开始", run_id));
    bridge
        .start_run(run_id.clone(), trimmed.to_string(), work_mode, autonomy, inference_mode, history, page_context, model_override, history_summary, last_skill_id, pinned_skill_id, task_id, task_title)
        .await?;
    crate::agent_manager::app_log(&format!("[agent_chat] run_id={}，SSE 流已建立", run_id));
    state.audit_handle().append(
        "agent.start",
        serde_json::json!({ "run_id": run_id, "operator": os_username() }),
    )?;
    Ok(run_id)
}

/// 将 HITL 审批决策转发给 FastAPI Agent。
#[tauri::command]
pub async fn agent_approval(
    state: State<'_, AppState>,
    approval_id: String,
    decision: String,
    operator: Option<String>,
) -> AppResult<()> {
    let op = operator.unwrap_or_else(os_username);
    state
        .sse
        .post_approval(&approval_id, &decision, Some(op.clone()))
        .await?;
    state.audit_handle().append(
        "agent.approval",
        serde_json::json!({
            "approval_id": approval_id,
            "decision": decision,
            "operator": op,
        }),
    )?;
    Ok(())
}

/// 取消正在运行的聊天。
#[tauri::command]
pub async fn agent_cancel(
    state: State<'_, AppState>,
    run_id: String,
) -> AppResult<()> {
    state.sse.cancel_run(&run_id).await?;
    state.audit_handle().append(
        "agent.cancel",
        serde_json::json!({ "run_id": run_id, "operator": os_username() }),
    )?;
    Ok(())
}

/// 会话标题摘要（2026-08-07）：非流式 HTTP，后端返回 {"title": "..."}。
/// 失败时后端也返空 title（前端保留截断标题），此处只透传。
#[tauri::command]
pub async fn chat_summarize_title(
    state: State<'_, AppState>,
    user_prompt: String,
    assistant_reply: Option<String>,
) -> AppResult<serde_json::Value> {
    let mut body = serde_json::json!({ "userPrompt": user_prompt });
    if let Some(reply) = assistant_reply {
        body["assistantReply"] = serde_json::Value::String(reply);
    }
    state.sse.post_json("/chat/summarize-title", body).await
}

/// 附加文件到对话（2026-08-14）：前端 📎 选中文件读成 base64 传过来，
/// 转发到后端 /chat/attach-file 转成文本（文本类直读 / docx、pdf 等走
/// file_to_markdown），返回 {ok, content, mode, truncated, error}。
#[tauri::command]
pub async fn chat_attach_file(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> AppResult<serde_json::Value> {
    state.agent_post("/chat/attach-file", body).await
}

/// 会话历史压缩（2026-08-17）：前端「压缩上下文」把旧对话传过来，
/// 转发到后端 /chat/compress-history 走本地优先 LLM 链生成摘要，
/// 返回 {ok, summary, beforeTokens, afterTokens, messageCount}。
#[tauri::command]
pub async fn chat_compress_history(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> AppResult<serde_json::Value> {
    state.agent_post("/chat/compress-history", body).await
}

/// Phase 19 V0 自进化闭环：用户 👍/👎 反馈透传（/evolution/feedback）。
/// body: {sessionId, messageId, rating: "up"|"down", correction?}。
/// Rust 侧不解析业务语义；👎 触发的后台反思由后端自管。
#[tauri::command]
pub async fn evolution_feedback(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> AppResult<serde_json::Value> {
    state.agent_post("/evolution/feedback", body).await
}

/// Phase 19 V0：经验库列表（设置页「经验库」面板）。
#[tauri::command]
pub async fn evolution_experiences(state: State<'_, AppState>) -> AppResult<serde_json::Value> {
    state.agent_get("/evolution/experiences").await
}

/// Phase 19 V0：经验启停切换（人工干预；后端按当前态翻转）。
#[tauri::command]
pub async fn evolution_experience_toggle(
    experience_id: i64,
    state: State<'_, AppState>,
) -> AppResult<serde_json::Value> {
    state
        .agent_post(
            &format!("/evolution/experiences/{}/toggle", experience_id),
            serde_json::json!({}),
        )
        .await
}

/// Phase 19 V0：删除经验（人工干预）。
#[tauri::command]
pub async fn evolution_experience_delete(
    experience_id: i64,
    state: State<'_, AppState>,
) -> AppResult<serde_json::Value> {
    state
        .agent_delete(&format!("/evolution/experiences/{}", experience_id))
        .await
}

/// 诊断用：当前有多少个活跃的 SSE 流？
#[tauri::command]
pub async fn agent_active_runs(state: State<'_, AppState>) -> AppResult<usize> {
    Ok(state.sse.active_runs().await)
}

/// 重启 Agent 子进程（用于切换 LLM 后端时）。
#[tauri::command]
pub fn agent_restart(_state: State<'_, AppState>) -> AppResult<()> {
    crate::agent_manager::app_log("[agent] agent_restart 触发 —— 杀旧进程 + 重启");
    crate::agent_manager::restart_agent_process();
    Ok(())
}

/// Phase 18：自动模式风险确认 —— 前端弹窗确认后转发到 FastAPI /autonomy/confirm，
/// 后端写 AUTO_MODE_ENABLED 审计（会话级授权的合规证据）。
#[tauri::command]
pub async fn agent_autonomy_confirm(
    state: State<'_, AppState>,
    session_id: String,
    work_mode: Option<String>,
    consent_version: Option<String>,
) -> AppResult<serde_json::Value> {
    let body = serde_json::json!({
        "sessionId": session_id,
        "workMode": work_mode.unwrap_or_else(|| "full".into()),
        "consentVersion": consent_version.unwrap_or_else(|| "v1".into()),
    });
    let resp = state.sse.post_json("/autonomy/confirm", body).await?;
    Ok(resp)
}

/// Phase 18：读取工具链路径配置（设置页面板用）。
#[tauri::command]
pub async fn agent_toolchain_get(state: State<'_, AppState>) -> AppResult<serde_json::Value> {
    state.agent_get("/toolchain").await
}

/// Token 用量（GET /llm/token-usage）—— 状态栏「Agent: 就绪」旁的实时速率
/// （区分上传/下载）+ 当日总量。前端每 2s 轮询一次。
#[tauri::command]
pub async fn token_usage_get(state: State<'_, AppState>) -> AppResult<serde_json::Value> {
    state.agent_get("/llm/token-usage").await
}

/// Phase 18：保存工具链路径配置。
#[tauri::command]
pub async fn agent_toolchain_save(
    state: State<'_, AppState>,
    paths: std::collections::HashMap<String, String>,
) -> AppResult<serde_json::Value> {
    state
        .agent_post("/toolchain", serde_json::json!({ "paths": paths }))
        .await
}

/// 工作空间路径配置读取（设置页面板用）。
#[tauri::command]
pub async fn agent_workspace_get(state: State<'_, AppState>) -> AppResult<serde_json::Value> {
    state.agent_get("/workspace").await
}

/// 工作空间路径配置保存（空串 = 恢复默认 安装目录/workspace）。
#[tauri::command]
pub async fn agent_workspace_save(
    state: State<'_, AppState>,
    path: String,
) -> AppResult<serde_json::Value> {
    state
        .agent_post("/workspace", serde_json::json!({ "path": path }))
        .await
}

/// 任务目录文件清单（2026-08-26）：产物/中间文件，验收清理卡用。
#[tauri::command]
pub async fn task_files_get(state: State<'_, AppState>, task_id: String) -> AppResult<serde_json::Value> {
    state
        .agent_get(&format!("/workspace/tasks/{task_id}"))
        .await
}

/// 验收后清理任务目录内除产物外的文件（2026-08-26）：
/// 转发后端 /workspace/tasks/{task_id}/cleanup，返回 {ok, deleted, kept, taskDirRemoved}。
#[tauri::command]
pub async fn task_cleanup(
    state: State<'_, AppState>,
    task_id: String,
    keep: Option<Vec<String>>,
) -> AppResult<serde_json::Value> {
    let resp = state
        .agent_post(
            &format!("/workspace/tasks/{task_id}/cleanup"),
            serde_json::json!({ "keep": keep.unwrap_or_default() }),
        )
        .await?;
    state.audit_handle().append(
        "task.cleanup",
        serde_json::json!({ "task_id": task_id, "operator": os_username() }),
    )?;
    Ok(resp)
}
