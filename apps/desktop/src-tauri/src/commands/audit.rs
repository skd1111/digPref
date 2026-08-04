//! Audit query commands.

use tauri::State;
use crate::error::AppResult;
use crate::state::AppState;

#[tauri::command]
pub fn audit_search(
    state: State<'_, AppState>,
    query: String,
    limit: Option<u32>,
) -> AppResult<Vec<serde_json::Value>> {
    state.audit_handle().search(&query, limit.unwrap_or(200))
}