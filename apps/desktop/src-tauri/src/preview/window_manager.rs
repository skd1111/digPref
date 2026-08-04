//! Phase 15 V0 · 预览窗口生命周期管理。
//!
//! 每个预览会话一个独立 `WebviewWindow`（label = `preview-{session_id}`）。
//! 主路径：独立窗口（可拖第二屏 / 独立 resize）；iframe 嵌入仅作前端兜底。
//!
//! 生命周期：
//!   - open_preview_window：创建窗口（初始尺寸按设备模式）
//!   - close_preview_window：关闭窗口（同时通知后端停止 session）
//!   - reload_preview_window：刷新页面（调用 Python /preview/reload）
//!   - list_preview_windows：列出全部预览窗口

use tauri::{AppHandle, WebviewUrl, WebviewWindowBuilder};
use tauri::Manager;

use crate::error::{AppError, AppResult};

/// 预览窗口 label 前缀（与前端 invoke wrapper 约定一致）。
pub const PREVIEW_WINDOW_PREFIX: &str = "preview-";

/// 设备模式 → (width, height) 初始尺寸（设计文档 §3.3）。
pub fn device_size_for(device_mode: &str) -> (f64, f64) {
    match device_mode {
        "mobile" => (375.0, 667.0),
        "tablet" => (768.0, 1024.0),
        "custom" => (480.0, 720.0),
        _ => (1280.0, 800.0), // desktop 默认
    }
}

/// session_id → 窗口 label（`preview-{session_id}`）。
pub fn window_label_for(session_id: &str) -> String {
    format!("{PREVIEW_WINDOW_PREFIX}{session_id}")
}

/// 从窗口 label 反解 session_id（非预览窗口返回 None）。
#[allow(dead_code)]
pub fn session_id_from_label(label: &str) -> Option<String> {
    label.strip_prefix(PREVIEW_WINDOW_PREFIX).map(|s| s.to_string())
}

/// URL 合法性校验：仅允许 http(s) 且 host 为 127.0.0.1 / localhost
/// （预览服务只在本机 5173-5300 端口运行，防止任意 URL 注入窗口）。
pub fn validate_preview_url(url: &str) -> AppResult<()> {
    let parsed = url
        .parse::<url::Url>()
        .map_err(|e| AppError::Config(format!("invalid preview url: {e}")))?;
    let scheme_ok = matches!(parsed.scheme(), "http" | "https");
    let host_ok = matches!(
        parsed.host_str(),
        Some("127.0.0.1" | "localhost" | "[::1]" | "::1")
    );
    if !scheme_ok || !host_ok {
        return Err(AppError::Config(format!(
            "preview url must be http(s)://127.0.0.1:<port>, got: {url}"
        )));
    }
    Ok(())
}

/// 打开（或聚焦已存在的）预览窗口。
pub fn open_preview_window(
    app: &AppHandle,
    session_id: &str,
    url: &str,
    device_mode: &str,
) -> AppResult<String> {
    validate_preview_url(url)?;
    let label = window_label_for(session_id);

    // 已存在 → 聚焦 + 返回
    if let Some(win) = app.get_webview_window(&label) {
        let _ = win.set_focus();
        return Ok(label);
    }

    let (w, h) = device_size_for(device_mode);
    let win = WebviewWindowBuilder::new(
        app,
        &label,
        WebviewUrl::External(url.parse().map_err(|e| AppError::Config(format!(
            "invalid preview url: {e}"
        )))?),
    )
    .title(format!("EAIDE Preview · {session_id}"))
    .inner_size(w, h)
    .build()
    .map_err(|e| AppError::Internal(format!("open preview window failed: {e}")))?;

    let _ = win.set_focus();
    Ok(label)
}

/// 关闭预览窗口（幂等）。
pub fn close_preview_window(app: &AppHandle, session_id: &str) -> AppResult<bool> {
    let label = window_label_for(session_id);
    match app.get_webview_window(&label) {
        Some(win) => {
            win.close().map_err(|e| {
                AppError::Internal(format!("close preview window failed: {e}"))
            })?;
            Ok(true)
        }
        None => Ok(false),
    }
}

/// 刷新预览窗口（reload 页面；Vite HMR 连接由后端保持）。
pub fn reload_preview_window(app: &AppHandle, session_id: &str) -> AppResult<bool> {
    let label = window_label_for(session_id);
    match app.get_webview_window(&label) {
        Some(win) => {
            let _ = win.eval("window.location.reload();");
            Ok(true)
        }
        None => Ok(false),
    }
}

/// 列出全部预览窗口 label + session_id。
pub fn list_preview_windows(app: &AppHandle) -> Vec<String> {
    app.webview_windows()
        .keys()
        .filter(|label| label.starts_with(PREVIEW_WINDOW_PREFIX))
        .cloned()
        .collect()
}

/// 调整预览窗口尺寸（设备模式切换）。
pub fn resize_preview_window(app: &AppHandle, session_id: &str, device_mode: &str) -> AppResult<bool> {
    let label = window_label_for(session_id);
    match app.get_webview_window(&label) {
        Some(win) => {
            let (w, h) = device_size_for(device_mode);
            win.set_size(tauri::LogicalSize::new(w, h)).map_err(|e| {
                AppError::Internal(format!("resize preview window failed: {e}"))
            })?;
            Ok(true)
        }
        None => Ok(false),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn window_label_roundtrip() {
        let sid = "abcd1234";
        let label = window_label_for(sid);
        assert_eq!(label, "preview-abcd1234");
        assert_eq!(session_id_from_label(&label).as_deref(), Some(sid));
        assert_eq!(session_id_from_label("main"), None);
        assert_eq!(session_id_from_label("preview-").as_deref(), Some(""));
    }

    #[test]
    fn device_sizes() {
        assert_eq!(device_size_for("mobile"), (375.0, 667.0));
        assert_eq!(device_size_for("tablet"), (768.0, 1024.0));
        assert_eq!(device_size_for("desktop"), (1280.0, 800.0));
        assert_eq!(device_size_for("unknown"), (1280.0, 800.0));
        assert_eq!(device_size_for("custom"), (480.0, 720.0));
    }

    #[test]
    fn url_validation() {
        assert!(validate_preview_url("http://127.0.0.1:5173").is_ok());
        assert!(validate_preview_url("http://localhost:5174/").is_ok());
        assert!(validate_preview_url("https://evil.com").is_err());
        assert!(validate_preview_url("file:///C:/x").is_err());
        assert!(validate_preview_url("not-a-url").is_err());
        assert!(validate_preview_url("http://127.0.0.1:99999").is_err());
    }

    #[test]
    fn prefix_is_consistent() {
        assert_eq!(PREVIEW_WINDOW_PREFIX, "preview-");
        assert!(list_preview_windows_helper().is_empty());
    }

    fn list_preview_windows_helper() -> Vec<String> {
        vec!["main".to_string()]
            .into_iter()
            .filter(|l| l.starts_with(PREVIEW_WINDOW_PREFIX))
            .collect()
    }
}
