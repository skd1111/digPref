//! Phase 12 V0/V1/V1.5 — 多智能体 Orchestrator Tauri commands。
//!
//! V0：4 个命令 —— spawn / list / get / tree_stats。
//! V1：progress 事件由 Rust sse_bridge 转发。
//! V1.5 增量（9 个新命令）：
//!   - dispatch / run_until_drained / cancel_all
//!   - dlq / dlq_requeue / dlq_close
//!   - metrics / queue_stats / replay
//!
//! 文档：[docs/design/phase-12-multi-agent-scaling.md](../../../docs/design/phase-12-multi-agent-scaling.md)

use tauri::State;

use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("orchestrator command failed: {}", e)
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

#[tauri::command]
pub async fn orchestrator_list(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/orchestrator/list");
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
pub async fn orchestrator_get(
    sub_agent_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = format!(
        "{}/orchestrator/{}",
        agent_url(&state, "/orchestrator"),
        sub_agent_id
    );
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
pub async fn orchestrator_tree_stats(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/orchestrator/tree/stats");
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
pub async fn orchestrator_cancel(
    sub_agent_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = format!(
        "{}/orchestrator/{}/cancel",
        agent_url(&state, "/orchestrator"),
        sub_agent_id
    );
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

// ---- V1.5 增量 ------------------------------------------------------------

#[tauri::command]
pub async fn orchestrator_dispatch(
    spec: serde_json::Value,
    priority: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/orchestrator/dispatch");
    let client = reqwest::Client::new();
    let mut req = client.post(&url).timeout(std::time::Duration::from_secs(5));
    if let Some(p) = priority {
        req = req.query(&[("priority", p)]);
    }
    let resp = req.json(&spec).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn orchestrator_run_until_drained(
    timeout: Option<f64>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/orchestrator/run_until_drained");
    let client = reqwest::Client::new();
    let mut req = client.post(&url).timeout(std::time::Duration::from_secs(120));
    if let Some(t) = timeout {
        req = req.query(&[("timeout", t.to_string())]);
    }
    let resp = req.send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn orchestrator_cancel_all(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/orchestrator/cancel_all");
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
pub async fn orchestrator_dlq_list(
    state: Option<String>,
    limit: Option<usize>,
    app_state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(&app_state, "/orchestrator/dlq");
    let mut sep = "?";
    if let Some(s) = state {
        url.push_str(sep);
        url.push_str(&format!("state={s}"));
        sep = "&";
    }
    if let Some(l) = limit {
        url.push_str(sep);
        url.push_str(&format!("limit={l}"));
    }
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
pub async fn orchestrator_dlq_requeue(
    task_id: String,
    note: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = format!(
        "{}/orchestrator/dlq/{}/requeue",
        agent_url(&state, "/orchestrator"),
        task_id
    );
    let client = reqwest::Client::new();
    let mut req = client.post(&url).timeout(std::time::Duration::from_secs(5));
    if let Some(n) = note {
        req = req.json(&serde_json::json!({ "note": n }));
    } else {
        req = req.json(&serde_json::json!({}));
    }
    let resp = req.send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn orchestrator_dlq_close(
    task_id: String,
    note: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = format!(
        "{}/orchestrator/dlq/{}/close",
        agent_url(&state, "/orchestrator"),
        task_id
    );
    let client = reqwest::Client::new();
    let mut req = client.post(&url).timeout(std::time::Duration::from_secs(5));
    if let Some(n) = note {
        req = req.json(&serde_json::json!({ "note": n }));
    } else {
        req = req.json(&serde_json::json!({}));
    }
    let resp = req.send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn orchestrator_metrics(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/orchestrator/metrics");
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
pub async fn orchestrator_queue_stats(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/orchestrator/queue/stats");
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
pub async fn orchestrator_replay(
    correlation_id: String,
    limit: Option<usize>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = format!(
        "{}/orchestrator/replay/{}",
        agent_url(&state, "/orchestrator"),
        correlation_id
    );
    let client = reqwest::Client::new();
    let mut req = client.get(&url).timeout(std::time::Duration::from_secs(10));
    if let Some(l) = limit {
        req = req.query(&[("limit", l.to_string())]);
    }
    let resp = req.send().await.map_err(err)?;
    json_or_err(resp).await
}
