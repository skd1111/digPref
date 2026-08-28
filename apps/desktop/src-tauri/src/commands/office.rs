//! Office 预览 Tauri commands —— OfficeCLI 渲染 docx/xlsx/pptx → HTML/PNG（V9）。
//!
//! WebView 受 CSP 限制不能直连 127.0.0.1:8765，统一经 Rust 代理：
//!
//! - `office_preview_render`: POST /office/preview；html 模式追加拉取渲染页全文（资源已内联）
//!   返回给前端 srcDoc 展示，避免二次跨域。
//! - `office_preview_stop`: POST /office/preview/stop。
//!
//! 模板照抄 reqflow.rs：json_or_err 含 HTTP 状态检查，避免错误体穿透。

use tauri::State;

use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("office preview command failed: {}", e)
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

fn agent_url(_state: &AppState, path: &str) -> String {
    let port = std::env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
    format!("http://127.0.0.1:{}{}", port, path)
}

/// POST /office/preview —— 渲染 Office 文件为预览产物。
///
/// html 模式返回 {session_id, html_url, html}（html = 渲染页全文）；
/// screenshot 模式返回 {session_id, image_base64, page}。
#[tauri::command]
pub async fn office_preview_render(
    path: String,
    mode: Option<String>,
    page: Option<u32>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/office/preview");
    let mut body = serde_json::json!({
        "path": path,
        "mode": mode.unwrap_or_else(|| "html".into()),
    });
    if let Some(p) = page {
        body["page"] = serde_json::Value::from(p);
    }
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    let json = json_or_err(resp).await?;

    // html 模式：追加拉取渲染页全文（资源内联单文件），前端 srcDoc 直接展示
    if json.get("mode").and_then(|m| m.as_str()) == Some("html") {
        if let Some(html_url) = json.get("html_url").and_then(|u| u.as_str()) {
            let full = agent_url(&state, html_url);
            let html_resp = reqwest::get(&full).await.map_err(err)?;
            let status = html_resp.status();
            let html = html_resp.text().await.map_err(err)?;
            if !status.is_success() {
                return Err(format!(
                    "agent returned {}: {}",
                    status.as_u16(),
                    &html[..html.len().min(200)]
                ));
            }
            let mut out = json;
            out["html"] = serde_json::Value::String(html);
            return Ok(out);
        }
    }
    Ok(json)
}

/// POST /office/preview/stop —— 停止预览会话并清理后端临时目录。
#[tauri::command]
pub async fn office_preview_stop(
    session_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/office/preview/stop");
    let client = reqwest::Client::new();
    let resp = client
        .post(&url)
        .json(&serde_json::json!({ "session_id": session_id }))
        .send()
        .await
        .map_err(err)?;
    json_or_err(resp).await
}
