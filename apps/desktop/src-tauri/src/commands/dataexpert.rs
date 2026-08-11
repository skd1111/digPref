//! Phase 7 V0 · 数据专家 Tauri commands —— Rust 桥接层。
//!
//! 包装 HTTP 请求到 Python Agent `/data/*` 路由。
//! 与 commands/audit.rs、commands/codenav.rs 同模式。

use base64::Engine;
use futures_util::StreamExt;
use serde_json::Value;
use tauri::{AppHandle, Emitter, State};
use tokio_tungstenite::tungstenite::Message;

use crate::error::{AppError, AppResult};
use crate::state::AppState;

/// GET /data/sources —— 数据源列表
#[tauri::command]
pub async fn data_list_sources(state: State<'_, AppState>) -> AppResult<Value> {
    let resp = state.agent_get("/data/sources").await?;
    Ok(resp)
}

/// POST /data/sources/{id}/sync —— 同步 Schema 元数据（注入 connection，缺口 2）
#[tauri::command]
pub async fn data_sync_schema(
    state: State<'_, AppState>,
    source_id: String,
) -> AppResult<Value> {
    let path = format!("/data/sources/{}/sync", source_id);
    let connection = crate::commands::asset::resolve_connection_config(&state, &source_id)
        .unwrap_or(serde_json::json!({}));
    let resp = state
        .agent_post(&path, serde_json::json!({ "connection": connection }))
        .await?;
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

/// POST /data/sql/run —— 执行只读 SQL（注入 connection，缺口 1）
#[tauri::command]
pub async fn data_run_sql(
    state: State<'_, AppState>,
    sql: String,
    source_id: Option<String>,
    confirmed: Option<bool>,
) -> AppResult<Value> {
    // 从 systems.yaml 解析数据源连接（keyring 密码已解），凭证只在内存传递
    let connection = match source_id.as_deref() {
        Some(sid) if !sid.is_empty() => {
            crate::commands::asset::resolve_connection_config(&state, sid)
                .unwrap_or(serde_json::json!({}))
        }
        _ => serde_json::json!({}),
    };
    let body = serde_json::json!({
        "sql": sql,
        "source_id": source_id.unwrap_or_default(),
        "confirmed": confirmed.unwrap_or(false),
        "connection": connection,
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

/// POST /data/export/{fmt} —— 导出（task_id 优先，服务端取数，缺口 5）
#[tauri::command]
pub async fn data_export(
    state: State<'_, AppState>,
    fmt: String,
    columns: Vec<String>,
    rows: Vec<Vec<Value>>,
    title: Option<String>,
    task_id: Option<String>,
) -> AppResult<Value> {
    let path = format!("/data/export/{}", fmt);
    let body = serde_json::json!({
        "task_id": task_id.unwrap_or_default(),
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

/// 数据库类型默认端口（与后端 pool.py DB_TYPE_REGISTRY 对齐）。
pub fn default_port_for(db_type: &str) -> u32 {
    match db_type {
        "mysql" => 3306,
        "postgresql" | "gaussdb" | "opengauss" => 5432,
        "oracle" => 1521,
        "sqlserver" => 1433,
        "clickhouse" => 8123,
        "dm" => 5236,
        "kingbase" => 54321,
        "gbase" => 5258,
        "oceanbase" => 2881,
        "tidb" => 4000,
        "highgo" => 5866,
        _ => 0,
    }
}

/// GET /data/tasks —— 历史分析任务列表（供 HistoryAnalysisList，缺口 9）
#[tauri::command]
pub async fn data_list_tasks(state: State<'_, AppState>, limit: Option<u32>) -> AppResult<Value> {
    let path = format!("/data/tasks?limit={}", limit.unwrap_or(50));
    let resp = state.agent_get(&path).await?;
    Ok(resp)
}

/// WS 中继：连 Python `/data/stream/{task_id}`，逐帧转发 Arrow 批到 Tauri channel（缺口 5）。
///
/// 协议（与后端 api.py stream_result 对齐）：
///   text 帧（kind=meta/done）→ DATA_STREAM_CHUNK {kind:"meta", text}
///   binary 帧（Arrow IPC 批）→ DATA_STREAM_CHUNK {kind:"batch", data_base64}
///   流结束 → DATA_STREAM_DONE {task_id, total_bytes}
#[tauri::command]
pub async fn data_stream_result(
    app: AppHandle,
    state: State<'_, AppState>,
    task_id: String,
) -> AppResult<u64> {
    // agent_base_url 如 http://127.0.0.1:8765 → ws://127.0.0.1:8765
    let ws_url = state
        .config
        .agent_base_url
        .replacen("http://", "ws://", 1)
        .replacen("https://", "wss://", 1);
    let url = format!("{}/data/stream/{}", ws_url.trim_end_matches('/'), task_id);

    let (mut ws, _resp) = tokio_tungstenite::connect_async(&url)
        .await
        .map_err(|e| AppError::Config(format!("WS 连接失败 {}: {}", url, e)))?;

    let b64 = base64::engine::general_purpose::STANDARD;
    let chunk_ch = crate::stream::channel::DATA_STREAM_CHUNK;
    let mut seq: u64 = 0;
    let mut total_bytes: u64 = 0;

    while let Some(msg) = ws.next().await {
        match msg {
            Ok(Message::Binary(data)) => {
                total_bytes += data.len() as u64;
                let _ = app.emit(
                    chunk_ch,
                    serde_json::json!({
                        "task_id": task_id,
                        "seq": seq,
                        "kind": "batch",
                        "data_base64": b64.encode(&data),
                    }),
                );
                seq += 1;
            }
            Ok(Message::Text(text)) => {
                let _ = app.emit(
                    chunk_ch,
                    serde_json::json!({
                        "task_id": task_id,
                        "seq": seq,
                        "kind": "meta",
                        "text": text,
                    }),
                );
                seq += 1;
            }
            Ok(Message::Close(_)) | Err(_) => break,
            Ok(_) => {}
        }
    }

    let _ = app.emit(
        crate::stream::channel::DATA_STREAM_DONE,
        serde_json::json!({
            "task_id": task_id,
            "total_bytes": total_bytes,
            "chunks": seq,
        }),
    );
    Ok(total_bytes)
}

/// POST /data/test_connection —— 测试数据库连接（支持主流+国产/信创）
// Tauri command 参数与前端调用一一对应，不拆结构体
#[allow(clippy::too_many_arguments)]
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
