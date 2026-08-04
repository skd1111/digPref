//! Phase 7 V0 · 数据专家 Tauri commands —— Rust 桥接层。
//!
//! 包装 HTTP 请求到 Python Agent `/data/*` 路由。
//! 与 commands/audit.rs、commands/codenav.rs 同模式。

use serde_json::Value;
use tauri::State;

use crate::error::AppResult;
use crate::state::AppState;

/// GET /data/sources —— 数据源列表
#[tauri::command]
pub async fn data_list_sources(state: State<'_, AppState>) -> AppResult<Value> {
    let resp = state.agent_get("/data/sources").await?;
    Ok(resp)
}

/// POST /data/sources/{id}/sync —— 同步 Schema 元数据
#[tauri::command]
pub async fn data_sync_schema(
    state: State<'_, AppState>,
    source_id: String,
) -> AppResult<Value> {
    let path = format!("/data/sources/{}/sync", source_id);
    let resp = state.agent_post(&path, serde_json::json!({})).await?;
    Ok(resp)
}

/// POST /data/nl2sql —— 自然语言 → SQL
#[tauri::command]
pub async fn data_nl2sql(
    state: State<'_, AppState>,
    question: String,
    source_id: Option<String>,
) -> AppResult<Value> {
    let body = serde_json::json!({
        "question": question,
        "source_id": source_id.unwrap_or_default(),
    });
    let resp = state.agent_post("/data/nl2sql", body).await?;
    Ok(resp)
}

/// POST /data/sql/run —— 执行只读 SQL
#[tauri::command]
pub async fn data_run_sql(
    state: State<'_, AppState>,
    sql: String,
    source_id: Option<String>,
    confirmed: Option<bool>,
) -> AppResult<Value> {
    let body = serde_json::json!({
        "sql": sql,
        "source_id": source_id.unwrap_or_default(),
        "confirmed": confirmed.unwrap_or(false),
    });
    let resp = state.agent_post("/data/sql/run", body).await?;
    Ok(resp)
}

/// POST /data/python/run —— 沙箱执行 Python
#[tauri::command]
pub async fn data_run_python(
    state: State<'_, AppState>,
    script: String,
    task_id: Option<String>,
) -> AppResult<Value> {
    let body = serde_json::json!({
        "script": script,
        "task_id": task_id.unwrap_or_default(),
    });
    let resp = state.agent_post("/data/python/run", body).await?;
    Ok(resp)
}

/// POST /data/chart/recommend —— 图表推荐
#[tauri::command]
pub async fn data_chart_recommend(
    state: State<'_, AppState>,
    columns: Vec<String>,
    dtypes: Vec<String>,
    row_count: u64,
) -> AppResult<Value> {
    let body = serde_json::json!({
        "columns": columns,
        "dtypes": dtypes,
        "row_count": row_count,
    });
    let resp = state.agent_post("/data/chart/recommend", body).await?;
    Ok(resp)
}

/// POST /data/export/{fmt} —— 导出
#[tauri::command]
pub async fn data_export(
    state: State<'_, AppState>,
    fmt: String,
    columns: Vec<String>,
    rows: Vec<Vec<Value>>,
    title: Option<String>,
) -> AppResult<Value> {
    let path = format!("/data/export/{}", fmt);
    let body = serde_json::json!({
        "columns": columns,
        "rows": rows,
        "title": title.unwrap_or_else(|| "数据报表".to_string()),
    });
    let resp = state.agent_post(&path, body).await?;
    Ok(resp)
}

/// POST /data/templates —— 保存报表模板
#[tauri::command]
pub async fn data_save_template(
    state: State<'_, AppState>,
    name: String,
    description: Option<String>,
    task_id: Option<String>,
    export_format: Option<String>,
) -> AppResult<Value> {
    let body = serde_json::json!({
        "name": name,
        "description": description.unwrap_or_default(),
        "task_id": task_id.unwrap_or_default(),
        "export_format": export_format.unwrap_or_else(|| "excel".to_string()),
    });
    let resp = state.agent_post("/data/templates", body).await?;
    Ok(resp)
}

/// POST /data/test_connection —— 测试数据库连接（支持主流+国产/信创）
#[tauri::command]
pub async fn data_test_connection(
    state: State<'_, AppState>,
    db_type: String,
    host: Option<String>,
    port: Option<u16>,
    database: Option<String>,
    username: Option<String>,
    password: Option<String>,
    path: Option<String>,
) -> AppResult<Value> {
    let body = serde_json::json!({
        "db_type": db_type,
        "host": host.unwrap_or_else(|| "127.0.0.1".to_string()),
        "port": port,
        "database": database.unwrap_or_default(),
        "username": username.unwrap_or_default(),
        "password": password.unwrap_or_default(),
        "path": path.unwrap_or_default(),
    });
    let resp = state.agent_post("/data/test_connection", body).await?;
    Ok(resp)
}
