//! 杂项 shell / 开发辅助命令。

use std::process::Command;

use crate::state::AppState;

/// 目录条目（供 File → Open Folder 文件树渲染）。
#[derive(serde::Serialize)]
pub struct DirEntry {
    pub name: String,
    pub path: String,
    pub is_dir: bool,
}

/// 列出指定目录的直接子条目（文件 + 子目录）。
///
/// 返回按 目录优先 → 名称字母序 排列的列表。
/// 隐藏 node_modules / .git / target 等常见噪音目录。
#[tauri::command]
pub async fn list_dir_entries(path: String) -> Result<Vec<DirEntry>, String> {
    let p = std::path::Path::new(&path);
    if !p.exists() {
        return Err(format!("路径不存在：{path}"));
    }
    if !p.is_dir() {
        return Err(format!("不是目录：{path}"));
    }

    const IGNORED: &[&str] = &[
        "node_modules", ".git", "target", "dist", "__pycache__", ".venv", ".idea", ".vs",
    ];

    let mut entries: Vec<DirEntry> = std::fs::read_dir(p)
        .map_err(|e| format!("读取目录失败：{e}"))?
        .filter_map(|item| {
            let item = item.ok()?;
            let name = item.file_name().to_string_lossy().to_string();
            if IGNORED.contains(&name.as_str()) {
                return None;
            }
            let ft = item.file_type().ok()?;
            Some(DirEntry {
                name: name.clone(),
                path: item.path().to_string_lossy().to_string(),
                is_dir: ft.is_dir(),
            })
        })
        .collect();

    // 目录优先，再按名称排序
    entries.sort_by(|a, b| b.is_dir.cmp(&a.is_dir).then(a.name.to_lowercase().cmp(&b.name.to_lowercase())));

    // 限制最多 500 条，避免巨型目录卡死前端
    entries.truncate(500);
    Ok(entries)
}

/// 读取文本文件（供 File → Open File 用）。
///
/// 不暴露给任何生产逻辑（Agent 不应走前端读文件）；仅供 UI 单文件预览。
/// path 不存在 / 非文本 / 过大（>5MB）时返回错误。
#[tauri::command]
pub async fn read_text_file(path: String) -> Result<String, String> {
    let p = std::path::Path::new(&path);
    if !p.exists() {
        return Err(format!("路径不存在：{path}"));
    }
    let metadata = std::fs::metadata(p).map_err(|e| format!("stat 失败：{e}"))?;
    if !metadata.is_file() {
        return Err(format!("不是文件：{path}"));
    }
    if metadata.len() > 5 * 1024 * 1024 {
        return Err(format!("文件过大（>5MB）：{path}"));
    }
    std::fs::read_to_string(p).map_err(|e| format!("读取失败（可能不是 UTF-8）：{e}"))
}

/// 打开 / 关闭开发者工具（F12 / Ctrl+Shift+I）。
///
/// 受配置开关控制：release 构建默认关闭，可通过
/// `%APPDATA%/eaide/config.yaml` 的 `devtools: true` 或
/// `EAIDE_DEVTOOLS=true` 环境变量启用（debug 构建默认开启）。
#[tauri::command]
pub fn open_devtools(
    window: tauri::WebviewWindow,
    state: tauri::State<AppState>,
) -> Result<String, String> {
    if !state.config.devtools_enabled {
        return Err(
            "开发者工具已被配置禁用：请在 config.yaml 中设置 devtools: true（或设置 \
             EAIDE_DEVTOOLS=true）后重启应用"
                .into(),
        );
    }
    if window.is_devtools_open() {
        window.close_devtools();
        Ok("开发者工具已关闭".into())
    } else {
        window.open_devtools();
        Ok("开发者工具已打开".into())
    }
}

/// 在系统资源管理器中定位到指定文件 / 目录（选中该条目）。
///
/// - Windows：`explorer.exe /select,"<path>"`
/// - macOS：`open -R <path>`
/// - Linux：`xdg-open <父目录>`（无统一选中语义，退化为打开父目录）
///
/// path 不存在时返回错误，避免资源管理器弹出「路径不存在」。
#[tauri::command]
pub fn reveal_in_explorer(path: String) -> Result<String, String> {
    let p = std::path::Path::new(&path);
    if !p.exists() {
        return Err(format!("路径不存在：{path}"));
    }

    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        // explorer.exe 的 /select, 参数对带空格 / 逗号的路径需要整体加引号，
        // 用 raw_arg 原样透传，避免 std 的 Windows 命令行转义破坏引号。
        let mut cmd = Command::new("explorer.exe");
        cmd.raw_arg(format!("/select,\"{path}\""));
        cmd.spawn().map_err(|e| format!("启动 explorer 失败：{e}"))?;
    }
    #[cfg(target_os = "macos")]
    {
        Command::new("open")
            .arg("-R")
            .arg(&path)
            .spawn()
            .map_err(|e| format!("启动 Finder 失败：{e}"))?;
    }
    #[cfg(all(unix, not(target_os = "macos")))]
    {
        let parent = p.parent().unwrap_or(std::path::Path::new("."));
        Command::new("xdg-open")
            .arg(parent)
            .spawn()
            .map_err(|e| format!("启动文件管理器失败：{e}"))?;
    }

    Ok(path)
}
