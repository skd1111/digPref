//! 专家团 Tauri command —— 包装 HTTP 调用 FastAPI（仿 commands/skills.rs）。

use tauri::State;

use crate::state::AppState;

type CmdResult<T> = Result<T, String>;

fn err(e: impl std::fmt::Display) -> String {
    format!("expert_teams command failed: {}", e)
}

/// 将 HTTP 响应转为 JSON；非 2xx 时提取 detail 返回 Err。
async fn json_or_err(resp: reqwest::Response) -> CmdResult<serde_json::Value> {
    let status = resp.status();
    let json: serde_json::Value = resp.json().await.map_err(err)?;
    if !status.is_success() {
        let detail = json
            .get("detail")
            .map(|d| match d {
                serde_json::Value::String(s) => s.clone(),
                other => other.to_string(),
            })
            .unwrap_or_else(|| "unknown error".into());
        return Err(format!("agent returned {}: {}", status.as_u16(), detail));
    }
    Ok(json)
}

fn agent_url(_state: &AppState, path: &str) -> String {
    let port = std::env::var("EAIDE_AGENT_PORT").unwrap_or_else(|_| "8765".into());
    format!("http://127.0.0.1:{}{}", port, path)
}

#[tauri::command]
pub async fn expert_teams_list(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/expert-teams/list");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn expert_teams_get(
    team_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, &format!("/expert-teams/{}", team_id));
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn expert_teams_save(
    team_id: String,
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, &format!("/expert-teams/{}", team_id));
    let client = reqwest::Client::new();
    let resp = client.put(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn expert_teams_delete(
    team_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, &format!("/expert-teams/{}", team_id));
    let client = reqwest::Client::new();
    let resp = client.delete(&url).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn expert_teams_import(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/expert-teams/import");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn expert_teams_export_all(state: State<'_, AppState>) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/expert-teams/export/all");
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}

#[tauri::command]
pub async fn expert_teams_recommend(
    body: serde_json::Value,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/expert-teams/recommend");
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// 专家团资产包导入：zip（team.yaml 提示词 + templates/ 交付物模板，base64）。
#[tauri::command]
pub async fn expert_teams_import_package(
    file_name: String,
    content_base64: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, "/expert-teams/import-package");
    let body = serde_json::json!({ "file_name": file_name, "content_base64": content_base64 });
    let client = reqwest::Client::new();
    let resp = client.post(&url).json(&body).send().await.map_err(err)?;
    json_or_err(resp).await
}

/// 专家团资产包导出：zip（base64，含 team.yaml + 当前生效模板）。
#[tauri::command]
pub async fn expert_teams_export_package(
    team_id: String,
    state: State<'_, AppState>,
) -> CmdResult<serde_json::Value> {
    let url = agent_url(&state, &format!("/expert-teams/{}/package", team_id));
    let resp = reqwest::get(&url).await.map_err(err)?;
    json_or_err(resp).await
}
