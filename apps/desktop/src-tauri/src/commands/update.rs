//! 增量更新状态只读命令（spec §8）。
//!
//! 仅读 `versions/` 目录与 `<pkg>-active.txt` 指针，无任何副作用；本期无 UI，
//! 供排错（`logs/eaide.log` 对照）与前端护栏测试断言用。

use serde_json::{json, Value};
use std::path::Path;

use crate::agent_manager::{read_active_version, resolve_versions_root};

/// 汇总单个 package 的激活/可用版本（不依赖外部 manifest，纯本地探测）。
fn pkg_status(vroot: &Path, exe_dir: &Path, name: &str) -> Value {
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
    let exe_name = if cfg!(target_os = "windows") {
        "eaide-agent.exe"
    } else {
        "eaide-agent"
    };
    // 对齐 spec §8：非 factory 给到可执行/资源目录路径；web 出厂态为磁盘 `web/`
    // （`bundle.resources` 的 `../dist` 映射），老安装包没有 web/ 时才是内嵌 → "embedded"
    let resolved = match (active.as_str(), name) {
        ("factory", "web") => {
            let web = exe_dir.join("web");
            if web.is_dir() {
                web.display().to_string()
            } else {
                "embedded".to_string()
            }
        }
        ("factory", _) => "factory(安装根 one-dir)".to_string(),
        (_, "agent") => pkgdir.join(&active).join(exe_name).display().to_string(),
        _ => pkgdir.join(&active).display().to_string(),
    };
    json!({ "active": active, "available": available, "resolved": resolved })
}

/// 纯函数：给定 versions 根聚合状态（env 覆盖单列，便于单测注入、不受全局 env 干扰）。
fn status_from_vroot(vroot: &Path, exe_dir: &Path) -> Value {
    json!({
        "shellVersion": env!("CARGO_PKG_VERSION"),
        "versionsRoot": vroot.display().to_string(),
        "agentExeOverride": std::env::var("EAIDE_AGENT_EXE").unwrap_or_default(),
        "agent": pkg_status(vroot, exe_dir, "agent"),
        "web": pkg_status(vroot, exe_dir, "web"),
    })
}

/// 基于 exe_dir 计算更新状态（读 EAIDE_VERSIONS_DIR 决定 versions 根）。
pub fn update_status_at(exe_dir: &Path) -> Value {
    status_from_vroot(&resolve_versions_root(exe_dir), exe_dir)
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
    use std::path::PathBuf;
    use std::sync::atomic::{AtomicU64, Ordering};

    /// RAII 临时目录：Drop 即清理，断言 panic 也不残留（Review 9a9a2e9 #M-3）。
    struct Tmp(PathBuf);
    impl Tmp {
        fn new(tag: &str) -> Self {
            static SEQ: AtomicU64 = AtomicU64::new(0);
            let n = SEQ.fetch_add(1, Ordering::Relaxed);
            let p = std::env::temp_dir().join(format!(
                "eaide-update-{}-{}-{}",
                std::process::id(),
                n,
                tag
            ));
            let _ = std::fs::remove_dir_all(&p);
            std::fs::create_dir_all(&p).unwrap();
            Self(p)
        }
        fn path(&self) -> &Path {
            &self.0
        }
    }
    impl Drop for Tmp {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn reports_active_and_available() {
        let t = Tmp::new("active");
        let vroot = t.path().join("versions");
        std::fs::create_dir_all(vroot.join("agent/0.1.8")).unwrap();
        std::fs::write(vroot.join("agent-active.txt"), "0.1.8\n").unwrap();
        let st = status_from_vroot(&vroot, t.path());
        assert_eq!(st["agent"]["active"].as_str(), Some("0.1.8"));
        assert!(st["agent"]["available"]
            .as_array()
            .unwrap()
            .iter()
            .any(|v| v.as_str() == Some("0.1.8")));
        assert!(st["agent"]["resolved"]
            .as_str()
            .unwrap()
            .ends_with(if cfg!(target_os = "windows") { "eaide-agent.exe" } else { "eaide-agent" }));
        assert_eq!(st["web"]["active"].as_str(), Some("factory"));
        assert_eq!(st["web"]["resolved"].as_str(), Some("embedded"));
    }

    #[test]
    fn missing_versions_defaults_factory() {
        let t = Tmp::new("empty");
        let vroot = t.path().join("versions");
        let st = status_from_vroot(&vroot, t.path());
        assert_eq!(st["agent"]["active"].as_str(), Some("factory"));
        assert_eq!(st["web"]["active"].as_str(), Some("factory"));
    }

    /// 出厂 web 已改为磁盘目录（`bundle.resources` 的 `../dist` → `./web/`），
    /// 有目录时 resolved 必须报真实路径，否则排错会被 "embedded" 误导。
    #[test]
    fn factory_web_reports_disk_dir_when_present() {
        let t = Tmp::new("webfactory");
        let exe_dir = t.path();
        let vroot = exe_dir.join("versions");
        std::fs::create_dir_all(exe_dir.join("web/assets")).unwrap();
        let st = status_from_vroot(&vroot, exe_dir);
        assert!(st["web"]["resolved"].as_str().unwrap().ends_with("web"));
    }
}
