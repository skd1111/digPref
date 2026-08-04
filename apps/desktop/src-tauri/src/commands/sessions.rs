//! Phase 6 会话管理 Tauri commands —— 包装 HTTP 调用 FastAPI。
//!
//! V1.5 (2026-07-31): 全套会话 commands（共 17 个）。
//! 模板照抄 codenav.rs / biznav.rs 风格，避免引入 urlencoding crate。
//!
//! 覆盖端点：
//!   V0：sessions_create / sessions_list / sessions_get / sessions_delete / sessions_kb_search
//!   V1.5：sessions_append_message / sessions_record_checkpoint / sessions_stats
//!        / sessions_search / sessions_branch_create / sessions_branches_list
//!        / sessions_share_create / sessions_share_revoke / sessions_share_grant / sessions_share_list
//!        / sessions_export / sessions_import / sessions_recovery
//!        / sessions_event_chain / sessions_event_chain_verify

use tauri::State;

use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("sessions command failed: {}", e)
}

/// 将 HTTP 响应转为 JSON；非 2xx 时提取 detail 返回 Err。
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

fn agent_url(path: &str) -> String {
    let port = std::env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
    format!("http://127.0.0.1:{}{}", port, path)
}

/// 简易 URL 编码（避免拉 urlencoding crate）。
fn urlencoding(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char);
            }
            _ => {
                out.push_str(&format!("%{:02X}", b));
            }
        }
    }
    out
}

// ============================================================================
// V0 端点
// ============================================================================

#[tauri::command]
pub async fn sessions_create(
    body: serde_json::Value,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url("/sessions");
    let resp = reqwest::Client::new()
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_list(
    opts: Option<serde_json::Value>,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url("/sessions");
    let mut req = reqwest::Client::new().get(&url);
    if let Some(o) = opts.as_ref().and_then(|v| v.as_object()) {
        for (k, v) in o {
            if let Some(s) = v.as_str() {
                req = req.query(&[(k.as_str(), s)]);
            } else if let Some(n) = v.as_i64() {
                req = req.query(&[(k.as_str(), n)]);
            }
        }
    }
    let resp = req.send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_get(
    session_id: String,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!("/sessions/{}", urlencoding(&session_id)));
    let resp = reqwest::Client::new().get(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_delete(
    session_id: String,
    _state: State<'_, AppState>,
) -> CmdResult<()> {
    let url = agent_url(&format!("/sessions/{}", urlencoding(&session_id)));
    let resp = reqwest::Client::new()
        .delete(&url)
        .send()
        .await
        .map_err(err)?;
    let status = resp.status();
    if !status.is_success() {
        let txt = resp.text().await.unwrap_or_default();
        return Err(format!("agent returned {}: {}", status.as_u16(), txt));
    }
    Ok(())
}

#[tauri::command]
pub async fn sessions_kb_search(
    body: serde_json::Value,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url("/sessions/kb/search");
    let resp = reqwest::Client::new()
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

// ============================================================================
// V1.5 端点：messages / checkpoints / stats / search / branch
// ============================================================================

#[tauri::command]
pub async fn sessions_append_message(
    session_id: String,
    body: serde_json::Value,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!(
        "/sessions/{}/messages",
        urlencoding(&session_id)
    ));
    let resp = reqwest::Client::new()
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_record_checkpoint(
    session_id: String,
    body: serde_json::Value,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!(
        "/sessions/{}/checkpoints",
        urlencoding(&session_id)
    ));
    let resp = reqwest::Client::new()
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_stats(
    session_id: String,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!("/sessions/{}/stats", urlencoding(&session_id)));
    let resp = reqwest::Client::new().get(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_search(
    body: serde_json::Value,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url("/sessions/search");
    let resp = reqwest::Client::new()
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_branch_create(
    session_id: String,
    body: serde_json::Value,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!("/sessions/{}/branch", urlencoding(&session_id)));
    let resp = reqwest::Client::new()
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_branches_list(
    session_id: String,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!(
        "/sessions/{}/branches",
        urlencoding(&session_id)
    ));
    let resp = reqwest::Client::new().get(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

// ============================================================================
// V1.5 端点：share / export / import / recovery / event_chain
// ============================================================================

#[tauri::command]
pub async fn sessions_share_create(
    session_id: String,
    body: serde_json::Value,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!("/sessions/{}/share", urlencoding(&session_id)));
    let resp = reqwest::Client::new()
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_share_revoke(
    session_id: String,
    token: String,
    actor: Option<String>,
    _state: State<'_, AppState>,
) -> CmdResult<()> {
    let url = agent_url(&format!(
        "/sessions/{}/share/{}",
        urlencoding(&session_id),
        urlencoding(&token)
    ));
    let mut req = reqwest::Client::new().delete(&url);
    if let Some(a) = actor {
        req = req.query(&[("actor", a)]);
    }
    let resp = req.send().await.map_err(err)?;
    let status = resp.status();
    if !status.is_success() {
        let txt = resp.text().await.unwrap_or_default();
        return Err(format!("agent returned {}: {}", status.as_u16(), txt));
    }
    Ok(())
}

#[tauri::command]
pub async fn sessions_share_grant(
    session_id: String,
    body: serde_json::Value,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!(
        "/sessions/{}/share/grant",
        urlencoding(&session_id)
    ));
    let resp = reqwest::Client::new()
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_share_list(
    session_id: String,
    actor: Option<String>,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!("/sessions/{}/share", urlencoding(&session_id)));
    let mut req = reqwest::Client::new().get(&url);
    if let Some(a) = actor {
        req = req.query(&[("actor", a)]);
    }
    let resp = req.send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_export(
    session_id: String,
    body: serde_json::Value,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!("/sessions/{}/export", urlencoding(&session_id)));
    let resp = reqwest::Client::new()
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_import(
    body: serde_json::Value,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url("/sessions/import");
    let resp = reqwest::Client::new()
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_recovery(
    idle_threshold_ms: Option<u64>,
    limit: Option<u32>,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url("/sessions/recovery");
    let resp = reqwest::Client::new()
        .get(&url)
        .query(&[
            (
                "idle_threshold_ms",
                idle_threshold_ms
                    .map(|v| v.to_string())
                    .unwrap_or_else(|| "300000".into()),
            ),
            (
                "limit",
                limit
                    .map(|v| v.to_string())
                    .unwrap_or_else(|| "50".into()),
            ),
        ])
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_event_chain(
    session_id: String,
    limit: Option<u32>,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!(
        "/sessions/{}/event-chain",
        urlencoding(&session_id)
    ));
    let mut req = reqwest::Client::new().get(&url);
    if let Some(l) = limit {
        req = req.query(&[("limit", l.to_string())]);
    }
    let resp = req.send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn sessions_event_chain_verify(
    session_id: String,
    _state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&format!(
        "/sessions/{}/event-chain/verify",
        urlencoding(&session_id)
    ));
    let resp = reqwest::Client::new()
        .post(&url)
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}