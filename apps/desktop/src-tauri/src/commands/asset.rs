//! System asset CRUD — 系统资产持久化到 systems.yaml。
//!
//! 资产类型：database / rest / ssh / rpa
//! 存储格式：YAML 数组，每个资产含 id/type/label/icon/meta 字段。
//! 安全红线：密码字段存 Keyring 引用（__KEYRING_REF:xxx），禁明文。

use std::fs;
use std::path::PathBuf;

use serde_json::Value;
use tauri::State;

use crate::error::{AppError, AppResult};
use crate::state::AppState;

/// 列出所有系统资产（从 systems.yaml 读取）。
#[tauri::command]
pub fn asset_list(state: State<'_, AppState>) -> AppResult<Vec<Value>> {
    let path = systems_path(&state);
    if !path.exists() {
        return Ok(vec![]);
    }
    let content = fs::read_to_string(&path)
        .map_err(|e| AppError::Config(format!("读取 systems.yaml 失败: {}", e)))?;
    let parsed: Value = serde_yaml::from_str(&content)
        .map_err(|e| AppError::Config(format!("解析 systems.yaml 失败: {}", e)))?;

    // 支持两种格式：顶层数组 或 { assets: [...] }
    let assets = match &parsed {
        Value::Array(arr) => arr.clone(),
        Value::Object(obj) => obj
            .get("assets")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default(),
        _ => vec![],
    };
    Ok(assets)
}

/// 新增资产（追加到 systems.yaml）。
#[tauri::command]
pub fn asset_add(state: State<'_, AppState>, asset: Value) -> AppResult<Value> {
    let path = systems_path(&state);
    let mut assets = load_assets(&path)?;

    // 确保有 id
    let id = asset
        .get("id")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| format!("asset_{}", chrono_now()));

    let mut new_asset = asset.clone();
    if let Value::Object(ref mut obj) = new_asset {
        obj.insert("id".into(), Value::String(id.clone()));
    }

    assets.push(new_asset.clone());
    save_assets(&path, &assets)?;
    Ok(new_asset)
}

/// 更新资产（按 id 匹配）。
#[tauri::command]
pub fn asset_update(state: State<'_, AppState>, id: String, patch: Value) -> AppResult<Value> {
    let path = systems_path(&state);
    let mut assets = load_assets(&path)?;

    let idx = assets
        .iter()
        .position(|a| a.get("id").and_then(|v| v.as_str()) == Some(&id))
        .ok_or_else(|| AppError::Config(format!("资产不存在: {}", id)))?;

    // 合并 patch 到现有资产
    if let (Value::Object(existing), Value::Object(p)) = (&mut assets[idx], &patch) {
        for (k, v) in p {
            existing.insert(k.clone(), v.clone());
        }
    }

    let updated = assets[idx].clone();
    save_assets(&path, &assets)?;
    Ok(updated)
}

/// 删除资产（按 id）。
#[tauri::command]
pub fn asset_remove(state: State<'_, AppState>, id: String) -> AppResult<()> {
    let path = systems_path(&state);
    let mut assets = load_assets(&path)?;
    assets.retain(|a| a.get("id").and_then(|v| v.as_str()) != Some(&id));
    save_assets(&path, &assets)?;
    Ok(())
}

// ---- 内部工具 ---------------------------------------------------------------

fn systems_path(state: &State<'_, AppState>) -> PathBuf {
    state.config.systems_path.clone()
}

fn load_assets(path: &PathBuf) -> AppResult<Vec<Value>> {
    if !path.exists() {
        return Ok(vec![]);
    }
    let content = fs::read_to_string(path)
        .map_err(|e| AppError::Config(format!("读取 systems.yaml 失败: {}", e)))?;
    let parsed: Value = serde_yaml::from_str(&content)
        .map_err(|e| AppError::Config(format!("解析 systems.yaml 失败: {}", e)))?;
    match parsed {
        Value::Array(arr) => Ok(arr),
        Value::Object(obj) => Ok(obj.get("assets").and_then(|v| v.as_array()).cloned().unwrap_or_default()),
        _ => Ok(vec![]),
    }
}

/// Phase 7 补齐：按资产 id 解析数据库连接配置（含 keyring 密码解析）。
///
/// 供 data_run_sql / data_sync_schema 注入 Python `/data/*` 请求体。
/// 安全红线：凭证只在内存传递，不落盘不打日志（CLAUDE.md §5）。
pub fn resolve_connection_config(state: &State<'_, AppState>, asset_id: &str) -> AppResult<Value> {
    let assets = load_assets(&systems_path(state))?;
    let asset = assets
        .iter()
        .find(|a| a.get("id").and_then(|v| v.as_str()) == Some(asset_id))
        .ok_or_else(|| AppError::Config(format!("数据源不存在: {}", asset_id)))?;
    let meta = asset.get("meta").cloned().unwrap_or(Value::Object(Default::default()));

    let get_str = |key: &str| -> String {
        meta.get(key).and_then(|v| v.as_str()).unwrap_or("").to_string()
    };

    // password 可能是 __KEYRING_REF:xxx 引用 → 从系统 keyring 解析
    let mut password = get_str("password");
    if let Some(ref_id) = password.strip_prefix("__KEYRING_REF:") {
        password = crate::credentials::Vault::default()
            .get(ref_id)?
            .unwrap_or_default();
    }

    let db_type = get_str("db_type");
    let default_port = crate::commands::dataexpert::default_port_for(&db_type);
    let port: u32 = get_str("port").parse().unwrap_or(default_port);

    Ok(serde_json::json!({
        "type": db_type,
        "host": get_str("host"),
        "port": port,
        "database": get_str("database"),
        "user": get_str("username"),
        "username": get_str("username"),
        "password": password,
        "path": get_str("path"),
    }))
}

fn save_assets(path: &PathBuf, assets: &[Value]) -> AppResult<()> {
    // 确保目录存在
    if let Some(parent) = path.parent() {
        let _ = fs::create_dir_all(parent);
    }
    let yaml = serde_yaml::to_string(assets)
        .map_err(|e| AppError::Config(format!("序列化 systems.yaml 失败: {}", e)))?;
    fs::write(path, yaml)
        .map_err(|e| AppError::Config(format!("写入 systems.yaml 失败: {}", e)))?;
    Ok(())
}

fn chrono_now() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}