//! datadict Tauri commands —— 数据字典（Phase 2H）。
//!
//! 包装 HTTP 调用 FastAPI `/dict/*`（公共参数独立维护，Skill 按 key 引用）。

use tauri::State;

use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("dict command failed: {}", e)
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

/// GET /dict/items?category= —— 列表。
#[tauri::command]
pub async fn dict_list_items(
    category: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(&state, "/dict/items");
    if let Some(c) = category.as_deref() {
        url.push_str(&format!("?category={}", urlencoding(c)));
    }
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /dict/search?q=&limit= —— 模糊搜索。
#[tauri::command]
pub async fn dict_search_items(
    q: String,
    limit: Option<u32>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(
        &state,
        &format!("/dict/search?q={}", urlencoding(&q)),
    );
    if let Some(l) = limit {
        url.push_str(&format!("&limit={}", l));
    }
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /dict/categories —— 分类列表。
#[tauri::command]
pub async fn dict_list_categories(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/dict/categories");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /dict/items —— 新建条目。
#[tauri::command]
pub async fn dict_create_item(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/dict/items");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// PUT /dict/items/{key} —— 更新条目。
#[tauri::command]
pub async fn dict_update_item(
    key: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, &format!("/dict/items/{}", urlencoding(&key)));
    let client = reqwest::Client::new();
    let resp = client.put(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// DELETE /dict/items/{key} —— 删除条目。
#[tauri::command]
pub async fn dict_delete_item(
    key: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, &format!("/dict/items/{}", urlencoding(&key)));
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}
