//! Phase 16 · 思维链可视化 Tauri commands —— Rust 桥接层。
//!
//! 包装 HTTP 请求到 Python Agent `/trace/*` 路由（GET 只读查询）。
//! 与 commands/dataexpert.rs 同模式（state.agent_get）。

use serde_json::Value;
use tauri::State;

use crate::error::AppResult;
use crate::state::AppState;

/// GET /trace/sessions —— 最近会话列表（前端启动时自动加载最近会话思维链）
#[tauri::command]
pub async fn trace_recent_sessions(state: State<'_, AppState>) -> AppResult<Value> {
    let resp = state.agent_get("/trace/sessions").await?;
    Ok(resp)
}

/// GET /trace/session/{session_id} —— 会话思维链时间线
#[tauri::command]
pub async fn trace_get_session(
    state: State<'_, AppState>,
    session_id: String,
) -> AppResult<Value> {
    let path = format!("/trace/session/{}", session_id);
    let resp = state.agent_get(&path).await?;
    Ok(resp)
}

/// GET /trace/step/{step_id} —— 单步详情
#[tauri::command]
pub async fn trace_get_step(state: State<'_, AppState>, step_id: String) -> AppResult<Value> {
    let path = format!("/trace/step/{}", step_id);
    let resp = state.agent_get(&path).await?;
    Ok(resp)
}

/// GET /trace/file-diff/{step_id}/{file_index} —— 文件操作完整 diff（hover 懒加载）
#[tauri::command]
pub async fn trace_get_file_diff(
    state: State<'_, AppState>,
    step_id: String,
    file_index: u32,
) -> AppResult<Value> {
    let path = format!("/trace/file-diff/{}/{}", step_id, file_index);
    let resp = state.agent_get(&path).await?;
    Ok(resp)
}
