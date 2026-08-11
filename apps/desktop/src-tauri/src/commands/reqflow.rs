//! reqflow Tauri commands —— 运营专家需求改造工作流（需求卡片 V1）。
//!
//! 包装 HTTP 调用 FastAPI `/reqflow/*`（11 端点 → 10 command，
//! export 按 format 分流：md 走 JSON，docx 走 bytes → base64）。
//! 模板照抄 biznav.rs：json_or_err 含 HTTP 状态检查，避免错误体穿透。

use base64::Engine; // STANDARD.encode() 需要 trait 在作用域
use tauri::State;

use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("reqflow command failed: {}", e)
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

// ---------------------------------------------------------------------------
// 批次
// ---------------------------------------------------------------------------

/// POST /reqflow/batches —— 创建批次。
#[tauri::command]
pub async fn reqflow_create_batch(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/reqflow/batches");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /reqflow/batches?project_name= —— 批次列表 + 统计。
#[tauri::command]
pub async fn reqflow_list_batches(
    project_name: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(&state, "/reqflow/batches");
    if let Some(p) = project_name.as_deref() {
        url.push_str(&format!("?project_name={}", urlencoding(p)));
    }
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

// ---------------------------------------------------------------------------
// AI 生成
// ---------------------------------------------------------------------------

/// POST /reqflow/cards/generate —— AI 生成卡片草稿（后端三级降级链）。
#[tauri::command]
pub async fn reqflow_generate_card(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/reqflow/cards/generate");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

// ---------------------------------------------------------------------------
// 卡片 CRUD + 版本
// ---------------------------------------------------------------------------

/// GET /reqflow/cards?batch_id=&status=&feature_id=&project_name=
#[tauri::command]
pub async fn reqflow_list_cards(
    batch_id: Option<String>,
    status: Option<String>,
    feature_id: Option<String>,
    project_name: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(&state, "/reqflow/cards");
    let mut first = true;
    let mut push = |k: &str, v: &str| {
        if first {
            url.push('?');
            first = false;
        } else {
            url.push('&');
        }
        url.push_str(&format!("{}={}", k, urlencoding(v)));
    };
    if let Some(b) = batch_id.as_deref() {
        push("batch_id", b);
    }
    if let Some(s) = status.as_deref() {
        push("status", s);
    }
    if let Some(f) = feature_id.as_deref() {
        push("feature_id", f);
    }
    if let Some(p) = project_name.as_deref() {
        push("project_name", p);
    }
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /reqflow/cards —— 保存卡片（自动编号）。
#[tauri::command]
pub async fn reqflow_create_card(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/reqflow/cards");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// PUT /reqflow/cards/{card_id} —— 改字段 / 切状态（后端记版本）。
#[tauri::command]
pub async fn reqflow_update_card(
    card_id: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, &format!("/reqflow/cards/{}", urlencoding(&card_id)));
    let client = reqwest::Client::new();
    let resp = client.put(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// DELETE /reqflow/cards/{card_id} —— 仅 draft 可删。
#[tauri::command]
pub async fn reqflow_delete_card(
    card_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, &format!("/reqflow/cards/{}", urlencoding(&card_id)));
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /reqflow/cards/{card_id}/versions —— 历史版本列表（倒序）。
#[tauri::command]
pub async fn reqflow_list_card_versions(
    card_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/reqflow/cards/{}/versions", urlencoding(&card_id)),
    );
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /reqflow/cards/{card_id}/versions/{version} —— 指定版本快照（只读）。
#[tauri::command]
pub async fn reqflow_get_card_version(
    card_id: String,
    version: u32,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!(
            "/reqflow/cards/{}/versions/{}",
            urlencoding(&card_id),
            version
        ),
    );
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

// ---------------------------------------------------------------------------
// 导出
// ---------------------------------------------------------------------------

/// GET /reqflow/export?batch_id=&format= —— md 返回 JSON；docx 返回 base64。
#[tauri::command]
pub async fn reqflow_export(
    batch_id: String,
    format: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!(
            "/reqflow/export?batch_id={}&format={}",
            urlencoding(&batch_id),
            urlencoding(&format)
        ),
    );
    let resp = reqwest::get(&url).await.map_err(err)?;
    let status = resp.status();
    if !status.is_success() {
        // 错误体是 JSON {detail: ...}
        return json_or_err(resp).await;
    }
    if format == "docx" {
        // 二进制 docx → base64（JSON 不能直接传 bytes，前端 atob 还原）
        let bytes = resp.bytes().await.map_err(err)?;
        let b64 = base64::engine::general_purpose::STANDARD.encode(bytes);
        return Ok(serde_json::json!({
            "format": "docx",
            "base64": b64,
            "filename": format!("{}.docx", batch_id),
        }));
    }
    let json: serde_json::Value = resp.json().await.map_err(err)?;
    Ok(json)
}

/// 导出文件落盘（项目未装 plugin-fs，用自定义 command 写磁盘）。
/// content_base64（docx）与 content_text（md）二选一。
#[tauri::command]
pub async fn reqflow_write_export(
    path: String,
    content_base64: Option<String>,
    content_text: Option<String>,
) -> CmdResult<serde_json::Value> {
    use base64::Engine;
    use std::io::Write;
    let bytes: Vec<u8> = if let Some(b64) = content_base64 {
        base64::engine::general_purpose::STANDARD
            .decode(b64)
            .map_err(err)?
    } else if let Some(text) = content_text {
        text.into_bytes()
    } else {
        return Err("content_base64 or content_text required".into());
    };
    let mut f = std::fs::File::create(&path).map_err(err)?;
    f.write_all(&bytes).map_err(err)?;
    Ok(serde_json::json!({"ok": true, "path": path, "bytes": bytes.len()}))
}
