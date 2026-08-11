//! ops Tauri commands —— 运营工作台业务记录（Phase 2H）。
//!
//! 包装 HTTP 调用 FastAPI `/ops/*`（记录卡片 CRUD + AI 总结）。
//! 模板照抄 reqflow.rs。

use tauri::State;

use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("ops command failed: {}", e)
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

/// POST /ops/records —— 创建业务记录卡片。
#[tauri::command]
pub async fn ops_create_record(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/records");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /ops/records?feature_id=&project_name=&limit=
#[tauri::command]
pub async fn ops_list_records(
    feature_id: Option<String>,
    project_name: Option<String>,
    limit: Option<u32>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(&state, "/ops/records");
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
    if let Some(f) = feature_id.as_deref() {
        push("feature_id", f);
    }
    if let Some(p) = project_name.as_deref() {
        push("project_name", p);
    }
    if let Some(l) = limit {
        push("limit", &l.to_string());
    }
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /ops/records/{id} —— 详情。
#[tauri::command]
pub async fn ops_get_record(
    record_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/records/{}", urlencoding(&record_id)),
    );
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// DELETE /ops/records/{id} —— 删除记录卡片。
#[tauri::command]
pub async fn ops_delete_record(
    record_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/records/{}", urlencoding(&record_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/records/summarize —— AI 根据会话 + 功能点 + Skill 生成总结草稿。
#[tauri::command]
pub async fn ops_summarize_record(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/records/summarize");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

// ---------------------------------------------------------------------------
// 专家验收工作流 Case（2026-08-10）
// ---------------------------------------------------------------------------

/// GET /ops/case?project_name=&feature_id= —— 获取 Case（材料文件 + 问答）。
#[tauri::command]
pub async fn ops_case_get(
    project_name: Option<String>,
    feature_id: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = agent_url(&state, "/ops/case");
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
    if let Some(f) = feature_id.as_deref() {
        push("feature_id", f);
    }
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/files —— 上传材料给对应专家（base64 内容）。
#[tauri::command]
pub async fn ops_case_file_add(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/case/files");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// DELETE /ops/case —— 清空 Case 重新开始办理（BUGFIX #85）。
#[tauri::command]
pub async fn ops_case_clear(
    project_name: Option<String>,
    feature_id: Option<String>,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let mut url = String::from(agent_url(&state, "/ops/case"));
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
    if let Some(f) = feature_id.as_deref() {
        push("feature_id", f);
    }
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/files/{id}/review —— AI 专家审核验收。
#[tauri::command]
pub async fn ops_case_file_review(
    file_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/files/{}/review", urlencoding(&file_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.post(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /ops/case/files/{id}/content —— 交付物柜预览（BUGFIX #79，base64 内容）。
#[tauri::command]
pub async fn ops_case_file_content(
    file_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/files/{}/content", urlencoding(&file_id)),
    );
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/files/{id}/save-as —— 交付物柜另存（后端直接复制文件）。
#[tauri::command]
pub async fn ops_case_file_save_as(
    file_id: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/files/{}/save-as", urlencoding(&file_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/files/{id}/override —— 人工改判。
#[tauri::command]
pub async fn ops_case_file_override(
    file_id: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/files/{}/override", urlencoding(&file_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// DELETE /ops/case/files/{id} —— 删除材料。
#[tauri::command]
pub async fn ops_case_file_delete(
    file_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/files/{}", urlencoding(&file_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/ask —— 向专家提问。
#[tauri::command]
pub async fn ops_case_ask(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/case/ask");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// PUT /ops/case/drafts/{id} —— 保存交付草稿填写值（BUGFIX #78：界面直填）。
#[tauri::command]
pub async fn ops_case_draft_save(
    draft_id: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/drafts/{}", urlencoding(&draft_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.put(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/drafts/{id}/submit —— 提交草稿：自动入材料走专家审核。
#[tauri::command]
pub async fn ops_case_draft_submit(
    draft_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/drafts/{}/submit", urlencoding(&draft_id)),
    );
    let client = reqwest::Client::new();
    let resp = client.post(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// POST /ops/case/export —— 全部验收后打包导出交付物 zip 到 target_path。
#[tauri::command]
pub async fn ops_case_export(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/ops/case/export");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// GET /ops/case/crosscheck?case_id= —— 多文档要素交叉比对（不一致标红，防退件）。
#[tauri::command]
pub async fn ops_case_crosscheck(
    case_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(
        &state,
        &format!("/ops/case/crosscheck?case_id={}", urlencoding(&case_id)),
    );
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}
