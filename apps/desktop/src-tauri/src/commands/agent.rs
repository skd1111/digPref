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
#[tauri::command]
pub async fn agent_chat(
    state: State<'_, AppState>,
    prompt: String,
    work_mode: Option<String>,
    autonomy: Option<String>,
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
        .start_run(run_id.clone(), trimmed.to_string(), work_mode, autonomy)
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
