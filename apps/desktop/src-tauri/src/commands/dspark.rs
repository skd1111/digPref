//! Phase 13 V0 — DSpark 推测解码 Tauri commands。
//!
//! Wraps HTTP calls to FastAPI `/dspark/*` endpoints.
//! V0：6 个命令 —— get_config / get_policies / get_recent / reload_policies /
//!     set_draft_model_path / update_config。
//!
//! 入参严格用 typed struct（问题 6 修复）；返回保留 serde_json::Value 兼容
//! V1 接 SSE 时的 protocol 增量。
//!
//! V0 不做实时 SSE 推送（V1：dspark_acceleration_status 通道）。
//!
//! 文档：[docs/design/phase-13-dspark.md](../../../docs/design/phase-13-dspark.md)

use serde::Deserialize;
use tauri::State;

use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("dspark command failed: {}", e)
}

/// 将 HTTP 响应转为 JSON；非 2xx 时提取 detail 返回 Err
async fn json_or_err(resp: reqwest::Response) -> CmdResult<serde_json::Value> {
    let status = resp.status();
    let json: serde_json::Value = resp.json().await.map_err(err)?;
    if !status.is_success() {
        let detail = json
            .get("detail")
            .and_then(|d| d.as_str())
            .unwrap_or("unknown error");
        return Err(format!("agent returned {}: {}", status.as_u16(), detail));
    }
    Ok(json)
}

fn agent_url(_state: &AppState, path: &str) -> String {
    let port = std::env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
    format!("http://127.0.0.1:{}{}", port, path)
}

// === Typed 入参 ===========================================================
//
// 与 packages/shared-protocol/src/protocol/dspark.py + ts/dspark.ts 严格对齐。
// 字段名用 snake_case（DSpark 全模块不走 shared-protocol 默认 camelCase）。
//
// 校验上下界常量：context_size ∈ [512, 262144]、gpu_layers ∈ [-1, 999]、
// short_output_threshold ≥ 1。FastAPI 端 Pydantic 会二次校验，Rust 早拒
// 能让上层错误更清晰。

/// POST /dspark/config body —— 所有字段可选
#[derive(Debug, Deserialize)]
#[serde(rename_all = "snake_case")]
pub struct DSparkConfigUpdateBody {
    #[serde(default)]
    pub draft_model_path: Option<String>,
    #[serde(default)]
    pub context_size: Option<i64>,
    #[serde(default)]
    pub gpu_layers: Option<i64>,
    #[serde(default)]
    pub enable_global: Option<bool>,
    #[serde(default)]
    pub short_output_threshold: Option<i64>,
}

impl DSparkConfigUpdateBody {
    /// 前置校验（FastAPI 仍会 Pydantic 二次校验；Rust 早拒给上层清晰错误）
    fn validate(&self) -> Result<(), String> {
        if let Some(ctx) = self.context_size {
            if !(512..=262144).contains(&ctx) {
                return Err(format!(
                    "context_size out of range: {} (must be 512..=262144)",
                    ctx
                ));
            }
        }
        if let Some(gpu) = self.gpu_layers {
            if !(-1..=999).contains(&gpu) {
                return Err(format!(
                    "gpu_layers out of range: {} (must be -1..=999)",
                    gpu
                ));
            }
        }
        if let Some(short) = self.short_output_threshold {
            if short < 1 {
                return Err(format!(
                    "short_output_threshold must be >= 1, got {}",
                    short
                ));
            }
        }
        Ok(())
    }

    fn is_empty(&self) -> bool {
        self.draft_model_path.is_none()
            && self.context_size.is_none()
            && self.gpu_layers.is_none()
            && self.enable_global.is_none()
            && self.short_output_threshold.is_none()
    }
}

/// POST /dspark/draft-model-path body
#[derive(Debug, Deserialize)]
pub struct DraftModelPathBody {
    #[serde(default)]
    pub path: Option<String>,
}

// === Commands ==============================================================

#[tauri::command]
pub async fn dspark_get_config(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/dspark/config");
    let client = reqwest::Client::new();
    let resp = client
        .get(&url)
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn dspark_get_policies(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/dspark/policies");
    let client = reqwest::Client::new();
    let resp = client
        .get(&url)
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn dspark_get_recent(
    limit: Option<u32>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let lim = limit.unwrap_or(20).clamp(1, 100);
    // agent_url(&state, "/dspark") 已含 base path "/dspark"，这里只拼子路径
    let url = format!("{}/recent?limit={}", agent_url(&state, "/dspark"), lim);
    let client = reqwest::Client::new();
    let resp = client
        .get(&url)
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn dspark_reload_policies(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/dspark/reload");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn dspark_set_draft_model_path(
    body: DraftModelPathBody,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/dspark/draft-model-path");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(5))
        .json(&serde_json::json!({ "path": body.path }))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

/// POST /dspark/config —— 全量配置更新（上下文 / GPU / 全局开关等）
///
/// 入参严格用 `DSparkConfigUpdateBody`（问题 6 修复）：
///   - 字段名拼错 / 类型错会在 Rust 编译期或 serde 反序列化期报错
///   - 数值上下界在前置 validate() 拦截
///   - FastAPI 端 Pydantic 仍会二次校验（防御纵深）
#[tauri::command]
pub async fn dspark_update_config(
    body: DSparkConfigUpdateBody,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    if body.is_empty() {
        return Err("no fields to update".into());
    }
    body.validate()?;

    let url = agent_url(&state, "/dspark/config");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(5))
        .json(&serde_json::json!({
            // 过滤 None：FastAPI exclude_none=True，避免显式 null 污染字段
            "draft_model_path": body.draft_model_path,
            "context_size": body.context_size,
            "gpu_layers": body.gpu_layers,
            "enable_global": body.enable_global,
            "short_output_threshold": body.short_output_threshold,
        }))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}