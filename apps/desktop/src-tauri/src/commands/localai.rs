//! Tauri commands for Phase 4 V0 local AI models and external KB.
//!
//! Each command proxies to the FastAPI backend via HTTP (same pattern as biznav.rs).

use reqwest;
use serde_json::Value;
use tauri::State;

use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("localai command failed: {}", e)
}

async fn json_or_err(resp: reqwest::Response) -> CmdResult<Value> {
    let status = resp.status();
    let body: Value = resp.json().await.map_err(err)?;
    if status.is_success() {
        Ok(body)
    } else {
        let detail = body
            .get("detail")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown error");
        Err(format!("agent returned {}: {}", status.as_u16(), detail))
    }
}

fn agent_url(_state: &State<'_, AppState>, path: &str) -> String {
    let port = std::env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
    format!("http://127.0.0.1:{}{}", port, path)
}

// ---- Local AI Status ----------------------------------------------------

#[tauri::command]
pub async fn localai_status(state: State<'_, AppState>) -> CmdResult<Value> {
    let url = agent_url(&state, "/localai/status");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn localai_health(state: State<'_, AppState>) -> CmdResult<Value> {
    let url = agent_url(&state, "/localai/health");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

// ---- Knowledge Base ----------------------------------------------------

#[tauri::command]
pub async fn knowledge_search(
    body: Value,
    state: State<'_, AppState>,
) -> CmdResult<Value> {
    let url = agent_url(&state, "/knowledge/search");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn knowledge_status(state: State<'_, AppState>) -> CmdResult<Value> {
    let url = agent_url(&state, "/knowledge/status");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}
