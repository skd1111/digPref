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