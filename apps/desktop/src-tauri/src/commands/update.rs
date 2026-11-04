//! 增量更新状态只读命令（spec §8）。
//!
//! 仅读 `versions/` 目录与 `<pkg>-active.txt` 指针，无任何副作用；本期无 UI，
//! 供排错（`logs/eaide.log` 对照）与前端护栏测试断言用。

use serde_json::{json, Value};
use std::path::Path;

use crate::agent_manager::{read_active_version, resolve_versions_root};

/// 汇总单个 package 的激活/可用版本（不依赖外部 manifest，纯本地探测）。
fn pkg_status(vroot: &Path, name: &str) -> Value {
    let active = read_active_version(vroot, name).unwrap_or_else(|| "factory".into());
    let pkgdir = vroot.join(name);
    let mut available: Vec<String> = pkgdir
        .read_dir()
        .map(|rd| {
            rd.filter_map(|e| e.ok())
                .filter(|e| e.path().is_dir())
                .map(|e| e.file_name().to_string_lossy().into_owned())
                .collect()
        })
        .unwrap_or_default();
    if !available.iter().any(|v| v.as_str() == "factory") {
        available.push("factory".into());
    }
    available.sort();
    let resolved = if active == "factory" {
        "factory(安装根 one-dir)".to_string()
    } else {
        pkgdir.join(&active).display().to_string()
    };
    json!({ "active": active, "available": available, "resolved": resolved })
}

/// 基于 exe_dir 计算更新状态（纯函数，便于单测注入临时目录）。
pub fn update_status_at(exe_dir: &Path) -> Value {
    let vroot = resolve_versions_root(exe_dir);
    json!({
        "shellVersion": env!("CARGO_PKG_VERSION"),
        "versionsRoot": vroot.display().to_string(),
        "agent": pkg_status(&vroot, "agent"),
        "web": pkg_status(&vroot, "web"),
    })
}

#[tauri::command]
pub fn app_update_status() -> Value {
    let exe_dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        .unwrap_or_default();
    update_status_at(&exe_dir)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    fn fresh_tmp(tag: &str) -> std::path::PathBuf {
        static SEQ: AtomicU64 = AtomicU64::new(0);
        let n = SEQ.fetch_add(1, Ordering::Relaxed);
        let p = std::env::temp_dir().join(format!("eaide-update-{}-{}-{}", std::process::id(), n, tag));
        let _ = std::fs::remove_dir_all(&p);
        std::fs::create_dir_all(&p).unwrap();
        p
    }

    #[test]
    fn reports_active_and_available() {
        let root = fresh_tmp("active");
        std::fs::create_dir_all(root.join("versions/agent/0.1.8")).unwrap();
        std::fs::write(root.join("versions/agent-active.txt"), "0.1.8\n").unwrap();
        let st = update_status_at(&root);
        assert_eq!(st["agent"]["active"].as_str(), Some("0.1.8"));
        assert!(st["agent"]["available"]
            .as_array()
            .unwrap()
            .iter()
            .any(|v| v.as_str() == Some("0.1.8")));
        assert_eq!(st["web"]["active"].as_str(), Some("factory"));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn missing_versions_defaults_factory() {
        let root = fresh_tmp("empty");
        let st = update_status_at(&root);
        assert_eq!(st["agent"]["active"].as_str(), Some("factory"));
        assert_eq!(st["web"]["active"].as_str(), Some("factory"));
        let _ = std::fs::remove_dir_all(&root);
    }
}
