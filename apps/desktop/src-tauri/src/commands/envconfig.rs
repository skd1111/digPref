//! 多环境治理 —— Tauri command 桥。
//!
//! 设计：
//!   - 命令直接 HTTP 调本地 Agent（`http://127.0.0.1:8765/envconfig/...`）
//!   - 真正的"明文密钥"永远留在 Rust 这边的 keyring，由 Agent 返回的占位符
//!     经 Rust 端在 keyring 里 lookup → 还原成明文 → 写回 EnvConfig 后再存盘 /
//!     推到 Agent 内存。
//!
//! 这一阶段先实现对 Agent HTTP 接口的"透明代理"（不掺入 keyring lookup，因为
//! Rust 这边目前还没有绑定 envconfig 的 secrets 到 keychain）。后续模块接入
//! 后再加 resolve_secrets 中间层。

use serde::{Deserialize, Serialize};
use tauri::State;

use crate::error::{AppError, AppResult};
use crate::state::AppState;

const AGENT_HTTP_TIMEOUT_SEC: u64 = 10;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct EnvListItemDto {
    pub environment: String,
    pub label: String,
    pub description: String,
    pub active: bool,
    pub updated_at: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct EnvListResponseDto {
    pub active: Option<String>,
    pub environments: Vec<EnvListItemDto>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct EnvConfigDto {
    pub environment: String,
    pub label: String,
    pub description: String,
    pub databases: Vec<serde_json::Value>,
    pub api_gateways: Vec<serde_json::Value>,
    pub mcp_servers: Vec<serde_json::Value>,
    pub target_servers: Vec<serde_json::Value>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ExportResponseDto {
    pub ciphertext_base64: String,
    pub env_count: u32,
    pub placeholder_count: u32,
    pub plaintext_bytes: u32,
    pub ciphertext_bytes: u32,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ImportResponseDto {
    pub env_count: u32,
    pub placeholders: Vec<String>,
    pub environments: Vec<EnvConfigDto>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ExportRequestDto {
    pub passphrase: String,
    pub environments: Vec<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ImportRequestDto {
    pub passphrase: String,
    pub ciphertext_base64: String,
    #[serde(default)]
    pub plaintext_ok: bool,
}

fn base_url(state: &AppState) -> String {
    state.config.agent_base_url.clone()
}

async fn http_get<T: for<'de> Deserialize<'de>>(url: &str) -> AppResult<T> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(AGENT_HTTP_TIMEOUT_SEC))
        .build()
        .map_err(|e| AppError::Config(format!("reqwest build: {e}")))?;
    let resp = client.get(url).send().await?;
    if !resp.status().is_success() {
        return Err(AppError::Config(format!(
            "GET {url} -> {}",
            resp.status()
        )));
    }
    let body: T = resp.json().await?;
    Ok(body)
}

async fn http_post<T: for<'de> Deserialize<'de>>(
    url: &str,
    body: serde_json::Value,
) -> AppResult<T> {
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(AGENT_HTTP_TIMEOUT_SEC))
        .build()
        .map_err(|e| AppError::Config(format!("reqwest build: {e}")))?;
    let resp = client.post(url).json(&body).send().await?;
    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(AppError::Config(format!("POST {url} -> {status}: {text}")));
    }
    let body: T = resp.json().await?;
    Ok(body)
}

// ---- Tauri commands -------------------------------------------------------

#[tauri::command]
pub async fn envconfig_list(state: State<'_, AppState>) -> AppResult<EnvListResponseDto> {
    crate::agent_manager::app_log("[envconfig_list] 转发到 Agent /envconfig/");
    let url = format!("{}/envconfig/", base_url(&state));
    http_get(&url).await
}

#[tauri::command]
pub async fn envconfig_get(
    state: State<'_, AppState>,
    env: String,
) -> AppResult<EnvConfigDto> {
    crate::agent_manager::app_log(&format!("[envconfig_get] env={env}"));
    let url = format!("{}/envconfig/{env}", base_url(&state));
    http_get(&url).await
}

#[tauri::command]
pub async fn envconfig_save(
    state: State<'_, AppState>,
    env: String,
    config: EnvConfigDto,
) -> AppResult<()> {
    crate::agent_manager::app_log(&format!("[envconfig_save] env={env} 转发到 Agent"));
    let url = format!("{}/envconfig/{env}", base_url(&state));
    let body = serde_json::to_value(&config).map_err(AppError::from)?;
    let _: serde_json::Value = http_post(&url, body).await?;
    Ok(())
}

#[tauri::command]
pub async fn envconfig_activate(
    state: State<'_, AppState>,
    env: String,
) -> AppResult<()> {
    crate::agent_manager::app_log(&format!("[envconfig_activate] env={env}"));
    let url = format!("{}/envconfig/{env}/activate", base_url(&state));
    let _: serde_json::Value = http_post(&url, serde_json::json!({})).await?;
    Ok(())
}

#[tauri::command]
pub async fn envconfig_delete(
    state: State<'_, AppState>,
    env: String,
) -> AppResult<()> {
    crate::agent_manager::app_log(&format!("[envconfig_delete] env={env}"));
    let url = format!("{}/envconfig/{env}", base_url(&state));
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(AGENT_HTTP_TIMEOUT_SEC))
        .build()
        .map_err(|e| AppError::Config(format!("reqwest build: {e}")))?;
    let resp = client.delete(&url).send().await?;
    if !resp.status().is_success() {
        return Err(AppError::Config(format!(
            "DELETE {url} -> {}",
            resp.status()
        )));
    }
    Ok(())
}

#[tauri::command]
pub async fn envconfig_export(
    state: State<'_, AppState>,
    req: ExportRequestDto,
) -> AppResult<ExportResponseDto> {
    crate::agent_manager::app_log(&format!(
        "[envconfig_export] envs={:?}",
        req.environments
    ));
    let url = format!("{}/envconfig/export", base_url(&state));
    let body = serde_json::to_value(&req).map_err(AppError::from)?;
    http_post(&url, body).await
}

#[tauri::command]
pub async fn envconfig_import(
    state: State<'_, AppState>,
    req: ImportRequestDto,
) -> AppResult<ImportResponseDto> {
    crate::agent_manager::app_log(&format!(
        "[envconfig_import] ciphertext_bytes={}",
        req.ciphertext_base64.len()
    ));
    let url = format!("{}/envconfig/import", base_url(&state));
    let body = serde_json::to_value(&req).map_err(AppError::from)?;
    http_post(&url, body).await
}
