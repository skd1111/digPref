//! MCP 服务器配置管理 —— Tauri command 桥（设置页「MCP」面板）。
//!
//! 设计：透明代理到本地 Agent（`http://127.0.0.1:8765/mcp-config/...`）。
//! mcp.yaml 的读写 / 校验 / 连通性测试 / 热重载全部由 Agent 完成；
//! 本模块只做 HTTP 转发与错误归一化。

use std::collections::HashMap;
use std::time::Duration;

use serde::Deserialize;
use tauri::State;

use crate::error::{AppError, AppResult};
use crate::state::AppState;

/// 常规读写超时（秒）
const HTTP_TIMEOUT_SEC: u64 = 10;
/// 连通性测试超时（秒）—— Agent 侧握手硬超时 20s，留进程启动余量
const TEST_TIMEOUT_SEC: u64 = 40;

fn base_url(state: &AppState) -> String {
    state.config.agent_base_url.clone()
}

async fn http_json(
    method: reqwest::Method,
    url: &str,
    body: Option<serde_json::Value>,
    timeout_sec: u64,
) -> AppResult<serde_json::Value> {
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(timeout_sec))
        .build()
        .map_err(|e| AppError::Config(format!("reqwest build: {e}")))?;
    let mut req = client.request(method.clone(), url);
    if let Some(b) = body {
        req = req.json(&b);
    }
    let resp = req.send().await?;
    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(AppError::Config(format!("{method} {url} -> {status}: {text}")));
    }
    let body: serde_json::Value = resp.json().await?;
    Ok(body)
}

// ---- Tauri commands -------------------------------------------------------

/// 读取当前 mcp.yaml 注册表。
#[tauri::command]
pub async fn mcp_config_get(state: State<'_, AppState>) -> AppResult<serde_json::Value> {
    crate::agent_manager::app_log("[mcp_config_get] 转发到 Agent /mcp-config");
    let url = format!("{}/mcp-config", base_url(&state));
    http_json(reqwest::Method::GET, &url, None, HTTP_TIMEOUT_SEC).await
}

/// 整表覆盖保存（Agent 侧做校验 + 原子写盘）。
#[tauri::command]
pub async fn mcp_config_save(
    state: State<'_, AppState>,
    servers: HashMap<String, serde_json::Value>,
) -> AppResult<serde_json::Value> {
    crate::agent_manager::app_log(&format!("[mcp_config_save] servers={:?}", servers.keys()));
    let url = format!("{}/mcp-config", base_url(&state));
    http_json(
        reqwest::Method::PUT,
        &url,
        Some(serde_json::json!({ "servers": servers })),
        HTTP_TIMEOUT_SEC,
    )
    .await
}

/// 对单个 server 条目做真实 stdio 握手 + list_tools。
#[derive(Debug, Deserialize)]
pub struct McpTestEntry {
    #[serde(default = "default_name")]
    pub name: String,
    pub command: String,
    #[serde(default)]
    pub args: Vec<String>,
    #[serde(default)]
    pub env: HashMap<String, String>,
    #[serde(default)]
    pub working_dir: Option<String>,
}

fn default_name() -> String {
    "probe".to_string()
}

#[tauri::command]
pub async fn mcp_config_test(
    state: State<'_, AppState>,
    entry: McpTestEntry,
) -> AppResult<serde_json::Value> {
    crate::agent_manager::app_log(&format!("[mcp_config_test] command={}", entry.command));
    let url = format!("{}/mcp-config/test", base_url(&state));
    http_json(
        reqwest::Method::POST,
        &url,
        Some(serde_json::json!({
            "name": entry.name,
            "command": entry.command,
            "args": entry.args,
            "env": entry.env,
            "working_dir": entry.working_dir,
        })),
        TEST_TIMEOUT_SEC,
    )
    .await
}

/// 重读 mcp.yaml 并重建 Agent 运行中的 MCP 连接池。
#[tauri::command]
pub async fn mcp_config_reload(state: State<'_, AppState>) -> AppResult<serde_json::Value> {
    crate::agent_manager::app_log("[mcp_config_reload] 转发到 Agent /mcp-config/reload");
    let url = format!("{}/mcp-config/reload", base_url(&state));
    http_json(
        reqwest::Method::POST,
        &url,
        Some(serde_json::json!({})),
        TEST_TIMEOUT_SEC,
    )
    .await
}
