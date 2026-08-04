//! Phase 2G 业务功能点导航 Tauri commands —— 包装 HTTP 调用 FastAPI。
//!
//! V1.2 (2026-07-28): 8 个核心 command + 1 个 status 探测。
//! 模板照抄 codenav.rs，避免引入 urlencoding crate。

use tauri::State;

use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("biznav command failed: {}", e)
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
// 8 个核心 command + status
// ---------------------------------------------------------------------------

/// POST /biznav/extract —— 后台任务立即返回 job_id。
#[tauri::command]
pub async fn biznav_extract(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/biznav/extract");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /biznav/status?project_name= —— 探测 biznav 模块是否就绪（含 biznav.db 是否存在）。
#[tauri::command]
pub async fn biznav_status(
    project_name: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/biznav/status?project_name={}", urlencoding(&project_name)),
    );
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /biznav/features?project_name=&category=&include_deleted=
#[tauri::command]
pub async fn biznav_list_features(
    project_name: Option<String>,
    category: Option<String>,
    include_deleted: Option<bool>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(&state, "/biznav/features");
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
    if let Some(p) = project_name.as_deref() {
        push("project_name", p);
    }
    if let Some(c) = category.as_deref() {
        push("category", c);
    }
    if let Some(d) = include_deleted {
        push("include_deleted", if d { "true" } else { "false" });
    }
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /biznav/features/{feature_id}
#[tauri::command]
pub async fn biznav_get_feature(
    feature_id: String,
    project_name: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!(
            "/biznav/features/{}?project_name={}",
            urlencoding(&feature_id),
            urlencoding(&project_name)
        ),
    );
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// PUT /biznav/features/{feature_id} —— 写业务功能点（涉及乐观锁 expected_version）。
#[tauri::command]
pub async fn biznav_upsert_feature(
    feature_id: String,
    project_name: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/biznav/features/{}", urlencoding(&feature_id)),
    );
    let client = reqwest::Client::new();
    // body 里 project_name 由前端独立传入；这里把 project_name 注入到 body
    // 若 body 已经有 project_name，前端责任保证一致
    let mut payload = body.clone();
    if let Some(obj) = payload.as_object_mut() {
        obj.insert("project_name".into(), serde_json::Value::String(project_name));
    } else {
        // 非 object 的 body 无法正确传入 UpdateFeatureRequest 字段；
        // 只传 project_name，让后端返回 422 提示调用方修 body 格式。
        payload = serde_json::json!({
            "project_name": project_name,
        });
    }
    let resp = client.put(&url).json(&payload).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// DELETE /biznav/features/{feature_id}?project_name=&hard=
#[tauri::command]
pub async fn biznav_delete_feature(
    feature_id: String,
    project_name: String,
    hard: Option<bool>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(
        &state,
        &format!(
            "/biznav/features/{}?project_name={}",
            urlencoding(&feature_id),
            urlencoding(&project_name),
        ),
    );
    if hard.unwrap_or(false) {
        url.push_str("&hard=true");
    }
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /biznav/import —— YAML 导入（含 merge 策略）。
#[tauri::command]
pub async fn biznav_import_yaml(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/biznav/import");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /biznav/export?project_name=&project_root=
#[tauri::command]
pub async fn biznav_export_yaml(
    project_name: String,
    project_root: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(
        &state,
        &format!("/biznav/export?project_name={}", urlencoding(&project_name)),
    );
    if let Some(root) = project_root.as_deref() {
        url.push_str(&format!("&project_root={}", urlencoding(root)));
    }
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /biznav/affected?file_path=&project_name= —— 查询受某文件变更影响的功能点。
#[tauri::command]
pub async fn biznav_affected(
    file_path: String,
    project_name: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!(
            "/biznav/affected?file_path={}&project_name={}",
            urlencoding(&file_path),
            urlencoding(&project_name)
        ),
    );
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}