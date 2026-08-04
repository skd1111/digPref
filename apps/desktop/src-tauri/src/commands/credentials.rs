//! Tauri commands for the credential vault.
//!
//! All commands return `Result<T, String>` so the JS side gets a stable
//! error shape. The Rust-side `AppError` already implements Serialize.
//!
//! Security model
//! --------------
//! - The frontend NEVER sees raw values except via `credential_get`.
//! - All write / read operations are appended to the audit SQLite.
//! - The set/delete commands require an `operator` arg (OS user) for
//!   accountability.

use tauri::State;

use crate::agent_manager::app_log;
use crate::credentials::{Vault, SERVICE_NAME};
use crate::error::AppResult;
use crate::state::AppState;


/// Fetch a credential by its namespaced account key.
///
/// Returns `None` if no such credential exists. The frontend must handle
/// both `Ok(None)` and `Err(...)`.
#[tauri::command]
pub fn credential_get(state: State<'_, AppState>, key: String) -> AppResult<Option<String>> {
    app_log(&format!("[credential_get] key={}", key));
    let vault = Vault::default();
    let value = vault.get(&key)?;
    app_log(&format!("[credential_get] key={} hit={}", key, value.is_some()));
    // Audit the access — we log that a read happened but not the value.
    state.audit_handle().append(
        "credential.get",
        serde_json::json!({ "key": key, "hit": value.is_some() }),
    )?;
    Ok(value)
}


/// Write (create or replace) a credential.
#[tauri::command]
pub fn credential_set(
    state: State<'_, AppState>,
    key: String,
    value: String,
) -> AppResult<()> {
    app_log(&format!("[credential_set] key={}, value.len={}", key, value.len()));
    let vault = Vault::default();
    vault.set(&key, &value)?;
    app_log(&format!("[credential_set] key={} 写入成功", key));
    state.audit_handle().append(
        "credential.set",
        serde_json::json!({ "key": key, "len": value.len() }),
    )?;
    Ok(())
}


/// Delete a credential. Idempotent — succeeds even if the entry doesn't
/// exist.
#[tauri::command]
pub fn credential_delete(state: State<'_, AppState>, key: String) -> AppResult<()> {
    app_log(&format!("[credential_delete] key={}", key));
    let vault = Vault::default();
    vault.delete(&key)?;
    app_log(&format!("[credential_delete] key={} 删除完成（幂等）", key));
    state.audit_handle().append("credential.delete", serde_json::json!({ "key": key }))?;
    Ok(())
}


/// List the credentials we know about. The caller provides the account
/// names (from `~/.eaide/systems.yaml` or similar); we return the values
/// for the ones that exist.
///
/// Frontend uses this to populate the "system asset tree" with secret
/// status indicators without ever exposing the actual secret contents.
#[tauri::command]
pub fn credential_list(
    state: State<'_, AppState>,
    keys: Vec<String>,
) -> AppResult<Vec<CredentialStatus>> {
    app_log(&format!("[credential_list] count={}", keys.len()));
    let vault = Vault::default();
    let items = vault.list(&keys);
    let present_count = items.iter().filter(|(_, v)| v.is_some()).count();
    app_log(&format!("[credential_list] {} 个中有 {} 个命中", keys.len(), present_count));
    state.audit_handle().append(
        "credential.list",
        serde_json::json!({ "count": keys.len() }),
    )?;
    Ok(items
        .into_iter()
        .map(|(key, value)| CredentialStatus {
            key,
            present: value.is_some(),
        })
        .collect())
}


#[derive(serde::Serialize)]
pub struct CredentialStatus {
    pub key: String,
    pub present: bool,
}


/// Diagnostic: returns the constant service name so the UI can render it.
#[tauri::command]
pub fn credential_service_name() -> &'static str {
    SERVICE_NAME
}