//! Phase 2F 代码导航 Tauri commands —— 包装 HTTP 调用 FastAPI。

use tauri::{Emitter, State};

use futures_util::StreamExt;
use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("codenav command failed: {}", e)
}

/// 将 HTTP 响应转为 JSON；非 2xx 时提取 detail 返回 Err，
/// 避免 FastAPI 错误体（如 {"detail":"..."}）被当作正常数据传给前端。
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
pub async fn code_nav_jump(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/jump");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// 语法错误检查（2026-08-19）：编辑器内容 → 后端 tree-sitter 解析 → 诊断列表，
/// 前端据此画 Monaco 红色波浪线。纯透传（同 code_nav_jump 模式）。
#[tauri::command]
pub async fn code_nav_check(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/check");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_index(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/index");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_status(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/status");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_list_symbols(
    name: String,
    kind: Option<String>,
    limit: Option<u32>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let limit = limit.unwrap_or(10);
    let mut url = agent_url(&state, &format!("/codenav/symbols?name={}", urlencoding(&name)));
    if let Some(k) = kind {
        url.push_str(&format!("&kind={}", urlencoding(&k)));
    }
    url.push_str(&format!("&limit={}", limit));
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_explain(
    body: serde_json::Value,
    state: State<'_, AppState>,
    app: tauri::AppHandle,
) -> CmdResult<serde_json::Value> {
    let symbol = body.get("symbol").and_then(|v| v.as_str()).unwrap_or("?");
    let t0 = std::time::Instant::now();
    // 发「开始解释」log
    let _ = app.emit(
        "agent://log",
        serde_json::json!({
            "kind": "log",
            "line": format!("[codenav.explain] ▶ symbol={symbol} status=running"),
        }),
    );
    let url = agent_url(&state, "/codenav/explain");
    let client = reqwest::Client::new();
    let result = async {
        let resp = client.post(&url).json(&body).send().await.map_err(err)?;
        json_or_err(resp).await
    }
    .await;
    let latency_ms = t0.elapsed().as_millis() as u64;
    match &result {
        Ok(body) => {
            let source = body.get("source").and_then(|v| v.as_str()).unwrap_or("?");
            let backend = body.get("backend").and_then(|v| v.as_str()).unwrap_or("-");
            let text_preview = body
                .get("text")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .chars()
                .take(80)
                .collect::<String>()
                .replace('\n', " ");
            let _ = app.emit(
                "agent://log",
                serde_json::json!({
                    "kind": "log",
                    "line": format!(
                        "[codenav.explain] ✓ symbol={symbol} status=ok source={source} backend={backend} latency_ms={latency_ms} preview=\"{text_preview}…\""
                    ),
                }),
            );
        }
        Err(e) => {
            let _ = app.emit(
                "agent://log",
                serde_json::json!({
                    "kind": "log",
                    "line": format!(
                        "[codenav.explain] ✗ symbol={symbol} status=err latency_ms={latency_ms} err={e}"
                    ),
                }),
            );
        }
    }
    result
}

#[tauri::command]
pub async fn code_nav_explain_stream(
    body: serde_json::Value,
    state: State<'_, AppState>,
    app: tauri::AppHandle,
) -> CmdResult<serde_json::Value> {
    let symbol = body.get("symbol").and_then(|v| v.as_str()).unwrap_or("?");
    let t0 = std::time::Instant::now();
    // 发「开始解释」log（与旧接口一致，主对话 ExecutionBlock 仍能收到）
    let _ = app.emit(
        "agent://log",
        serde_json::json!({
            "kind": "log",
            "line": format!("[codenav.explain] ▶ symbol={symbol} status=running"),
        }),
    );
    let url = agent_url(&state, "/codenav/explain/stream");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .json(&body)
        .send()
        .await
        .map_err(err)?;
    if !resp.status().is_success() {
        let status = resp.status();
        let detail = resp
            .json::<serde_json::Value>()
            .await
            .ok()
            .and_then(|j| j.get("detail").and_then(|d| d.as_str()).map(String::from))
            .unwrap_or_else(|| "unknown error".into());
        return Err(format!("agent returned {}: {}", status.as_u16(), detail));
    }

    let mut stream = resp.bytes_stream();
    let mut buffer = String::new();
    let mut full_text = String::new();
    let mut source = "llm".to_string();
    let mut confidence = 0.85_f64;
    let mut backend: Option<String> = None;

    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(err)?;
        buffer.push_str(&String::from_utf8_lossy(&chunk));
        while let Some(pos) = buffer.find('\n') {
            let line = buffer[..pos].trim().to_string();
            buffer = buffer[pos + 1..].to_string();
            if line.is_empty() {
                continue;
            }
            let value: serde_json::Value = match serde_json::from_str(&line) {
                Ok(v) => v,
                Err(_) => continue,
            };
            if let Some(delta) = value.get("delta").and_then(|d| d.as_str()) {
                if !delta.is_empty() {
                    full_text.push_str(delta);
                    // 逐块推给 WebView，前端控制台实时渲染
                    let _ = app.emit(
                        "agent://codenav_explain_delta",
                        serde_json::json!({ "symbol": symbol, "delta": delta }),
                    );
                }
            }
            if value.get("done").and_then(|d| d.as_bool()).unwrap_or(false) {
                if let Some(t) = value.get("text").and_then(|t| t.as_str()) {
                    full_text = t.to_string();
                }
                source = value
                    .get("source")
                    .and_then(|s| s.as_str())
                    .unwrap_or("llm")
                    .to_string();
                confidence = value
                    .get("confidence")
                    .and_then(|c| c.as_f64())
                    .unwrap_or(0.85);
                backend = value
                    .get("backend")
                    .and_then(|b| b.as_str())
                    .map(String::from);
            }
            if let Some(msg) = value.get("error").and_then(|e| e.as_str()) {
                let _ = app.emit(
                    "agent://log",
                    serde_json::json!({
                        "kind": "log",
                        "line": format!(
                            "[codenav.explain] ✗ symbol={symbol} status=err err={msg}"
                        ),
                    }),
                );
                return Err(format!("codenav explain stream failed: {}", msg));
            }
        }
    }

    let latency_ms = t0.elapsed().as_millis() as u64;
    let text_preview = full_text
        .chars()
        .take(80)
        .collect::<String>()
        .replace('\n', " ");
    let _ = app.emit(
        "agent://log",
        serde_json::json!({
            "kind": "log",
            "line": format!(
                "[codenav.explain] ✓ symbol={symbol} status=ok source={source} backend={backend:?} latency_ms={latency_ms} preview=\"{text_preview}…\""
            ),
        }),
    );
    Ok(serde_json::json!({
        "symbol": symbol,
        "text": full_text,
        "source": source,
        "confidence": confidence,
        "backend": backend,
    }))
}

#[tauri::command]
pub async fn code_nav_llm_config(
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/llm-config");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_llm_config_reload(
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/llm-config/reload");
    let client = reqwest::Client::new();
    let resp = client.post(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_allowed_roots(
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/allowed-roots");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_llm_backend(
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/llm-backend");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_llm_backend_bind(
    backend_name: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/llm-backend/bind");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .json(&serde_json::json!({"backend_name": backend_name}))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_opened_projects(
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/opened-projects");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_sync_opened_projects(
    folders: Vec<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/opened-projects/sync");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .json(&serde_json::json!({"folders": folders}))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_add_opened_project(
    folder: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/opened-projects/add");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .json(&serde_json::json!({"folder": folder}))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn code_nav_remove_opened_project(
    folder: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/codenav/opened-projects/remove");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .json(&serde_json::json!({"folder": folder}))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
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
