//! Phase 15 V0 · 预览引擎 Tauri commands —— Rust 桥接层。
//!
//! 窗口操作直接操作 WebviewWindow（主路径）；会话 CRUD 包装 HTTP 请求到
//! Python Agent `/preview/*` 路由（与 commands/dataexpert.rs 同模式）。

use serde_json::{json, Value};
use tauri::{AppHandle, State};

use crate::error::AppResult;
use crate::preview::window_manager;
use crate::state::AppState;

/// 打开预览窗口（主路径：独立 WebviewWindow）。
#[tauri::command]
pub async fn preview_open_window(
    app: AppHandle,
    session_id: String,
    url: String,
    device_mode: Option<String>,
) -> AppResult<String> {
    let mode = device_mode.unwrap_or_else(|| "desktop".to_string());
    window_manager::open_preview_window(&app, &session_id, &url, &mode)
}

/// 关闭预览窗口（幂等；窗口不存在返回 false）。
#[tauri::command]
pub async fn preview_close_window(app: AppHandle, session_id: String) -> AppResult<bool> {
    window_manager::close_preview_window(&app, &session_id)
}

/// 刷新预览窗口页面。
#[tauri::command]
pub async fn preview_reload_window(app: AppHandle, session_id: String) -> AppResult<bool> {
    window_manager::reload_preview_window(&app, &session_id)
}

/// 调整预览窗口尺寸（设备模式切换）。
#[tauri::command]
pub async fn preview_resize_window(
    app: AppHandle,
    session_id: String,
    device_mode: String,
) -> AppResult<bool> {
    window_manager::resize_preview_window(&app, &session_id, &device_mode)
}

/// 列出全部预览窗口（label 列表）。
#[tauri::command]
pub async fn preview_list_windows(app: AppHandle) -> Vec<String> {
    window_manager::list_preview_windows(&app)
}

/// POST /preview/start —— 启动预览会话（Python 后端起 Vite 子进程）。
#[tauri::command]
pub async fn preview_start(
    state: State<'_, AppState>,
    project_path: String,
    entry_file: Option<String>,
    framework: Option<String>,
    port: Option<u16>,
    // BUGFIX #175：项目不在预览白名单时，前端用户确认后带 true 重试
    allow_path: Option<bool>,
) -> AppResult<Value> {
    let body = json!({
        "project_path": project_path,
        "entry_file": entry_file.unwrap_or_default(),
        "framework": framework,
        "port": port,
        "allow_path": allow_path.unwrap_or(false),
    });
    state.agent_post("/preview/start", body).await
}

/// POST /preview/stop/{session_id} —— 停止预览会话。
#[tauri::command]
pub async fn preview_stop(state: State<'_, AppState>, session_id: String) -> AppResult<Value> {
    let path = format!("/preview/stop/{}", session_id);
    state.agent_post(&path, json!({})).await
}

/// GET /preview/sessions —— 活跃会话列表。
#[tauri::command]
pub async fn preview_sessions(state: State<'_, AppState>) -> AppResult<Value> {
    state.agent_get("/preview/sessions").await
}

/// GET /preview/info/{session_id} —— 会话详情。
#[tauri::command]
pub async fn preview_info(
    state: State<'_, AppState>,
    session_id: String,
) -> AppResult<Value> {
    let path = format!("/preview/info/{}", session_id);
    state.agent_get(&path).await
}

/// POST /preview/reload/{session_id} —— 强制刷新。
#[tauri::command]
pub async fn preview_reload(state: State<'_, AppState>, session_id: String) -> AppResult<Value> {
    let path = format!("/preview/reload/{}", session_id);
    state.agent_post(&path, json!({})).await
}

/// POST /preview/install/{session_id} —— 手动触发依赖安装。
#[tauri::command]
pub async fn preview_install(state: State<'_, AppState>, session_id: String) -> AppResult<Value> {
    let path = format!("/preview/install/{}", session_id);
    state.agent_post(&path, json!({})).await
}
