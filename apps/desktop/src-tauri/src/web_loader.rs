//! 前端 dist 热替换 loader 的**纯逻辑**（spec §7）。
//!
//! ⚠️ 本模块只提供「把请求路径解析到磁盘文件 + 推断 content-type」的可测函数。
//! 真正的 `window.url` 改写 + `register_asynchronous_uri_scheme_protocol` 接线是
//! **spike 门控项**（见 docs/ops/incremental-update-runbook.md §5 与文末 SPIKE 注），
//! 未验证前不并入主线，以免在无法本机编译的情况下影响全体用户 app 启动。
//!
//! 布局：`versions/web/<active>/<rel>`（更新覆盖，优先）→ `exe_dir/web/<rel>`
//! （出厂 web 资源兜底）。注意前端**不**走内嵌 `frontendDist`，而走磁盘 `web/`
//! 资源目录——这样热替换与内嵌 origin 覆盖这两个难点都规避了。

use std::path::{Path, PathBuf};

/// 归一化请求路径：去前导 `/`、空路径视为 `index.html`。含 `..` 一律拒绝（None）。
fn normalize_rel(path: &str) -> Option<String> {
    let rel = path.trim_start_matches('/');
    let rel = if rel.is_empty() { "index.html" } else { rel };
    // query/fragment 不会出现在 uri().path()，但防御性去尾斜杠
    if rel.split('/').any(|seg| seg == "..") || rel.contains('\0') {
        return None;
    }
    Some(rel.to_string())
}

/// 把请求 URI path 解析为磁盘文件；找不到具体文件且无扩展名 → SPA 回落 index.html。
///
/// 优先顺序：`versions/web/<active>` > 出厂 `<exe_dir>/web`。
pub fn resolve_asset(exe_dir: &Path, vroot: &Path, uri_path: &str) -> Option<PathBuf> {
    let rel = normalize_rel(uri_path)?;
    let looks_spa = !rel.rsplit('/').next().unwrap_or("").contains('.');

    let pick_in = |base: &Path| -> Option<PathBuf> {
        let cand = base.join(&rel);
        if cand.is_file() {
            return Some(cand);
        }
        // SPA history 路由深链：无扩展名且未命中 → index.html
        if looks_spa {
            let idx = base.join("index.html");
            if idx.is_file() {
                return Some(idx);
            }
        }
        None
    };

    if let Some(active) = crate::agent_manager::read_active_version(vroot, "web") {
        if let Some(hit) = pick_in(&vroot.join("web").join(&active)) {
            return Some(hit);
        }
    }
    pick_in(&exe_dir.join("web"))
}

/// 扩展名 → content-type（前端静态资源够用集）。
pub fn mime_of(rel: &str) -> &'static str {
    match rel.rsplit('.').next().unwrap_or("").to_ascii_lowercase().as_str() {
        "html" | "htm" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript",
        "css" => "text/css",
        "json" | "map" => "application/json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "ico" => "image/x-icon",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "ttf" => "font/ttf",
        "otf" => "font/otf",
        "wasm" => "application/wasm",
        "txt" => "text/plain; charset=utf-8",
        _ => "application/octet-stream",
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    struct Tmp(PathBuf);
    impl Tmp {
        fn new(tag: &str) -> Self {
            static SEQ: AtomicU64 = AtomicU64::new(0);
            let n = SEQ.fetch_add(1, Ordering::Relaxed);
            let p = std::env::temp_dir()
                .join(format!("eaide-web-{}-{}-{}", std::process::id(), n, tag));
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

    fn write(dir: &Path, rel: &str) {
        let f = dir.join(rel);
        std::fs::create_dir_all(f.parent().unwrap()).unwrap();
        std::fs::write(&f, b"x").unwrap();
    }

    #[test]
    fn factory_web_used_when_no_active() {
        let t = Tmp::new("factory");
        let exe_dir = t.path();
        let vroot = exe_dir.join("versions");
        write(&exe_dir.join("web"), "index.html");
        write(&exe_dir.join("web"), "assets/app.js");
        assert_eq!(
            resolve_asset(exe_dir, &vroot, "/").unwrap(),
            exe_dir.join("web").join("index.html")
        );
        assert_eq!(
            resolve_asset(exe_dir, &vroot, "/assets/app.js").unwrap(),
            exe_dir.join("web").join("assets/app.js")
        );
    }

    #[test]
    fn active_version_overrides_and_falls_through() {
        let t = Tmp::new("active");
        let exe_dir = t.path();
        let vroot = exe_dir.join("versions");
        write(&exe_dir.join("web"), "index.html");
        write(&exe_dir.join("web"), "assets/app.js");
        // 激活 0.2.0，仅提供 index.html（其余应回落出厂 web）
        write(&vroot.join("web/0.2.0"), "index.html");
        std::fs::write(vroot.join("web-active.txt"), "0.2.0\n").unwrap();
        assert_eq!(
            resolve_asset(exe_dir, &vroot, "/").unwrap(),
            vroot.join("web/0.2.0/index.html")
        );
        // app.js 不在激活版 → 回落出厂 web
        assert_eq!(
            resolve_asset(exe_dir, &vroot, "/assets/app.js").unwrap(),
            exe_dir.join("web").join("assets/app.js")
        );
    }

    #[test]
    fn spa_deep_link_falls_back_to_index() {
        let t = Tmp::new("spa");
        let exe_dir = t.path();
        let vroot = exe_dir.join("versions");
        write(&exe_dir.join("web"), "index.html");
        // /settings/terminal 无扩展名且不存在 → index.html
        assert_eq!(
            resolve_asset(exe_dir, &vroot, "/settings/terminal").unwrap(),
            exe_dir.join("web/index.html")
        );
        // 有扩展名且不存在 → None（应 404，不应吞成 index.html）
        assert!(resolve_asset(exe_dir, &vroot, "/missing.png").is_none());
    }

    #[test]
    fn traversal_rejected() {
        let t = Tmp::new("trav");
        let exe_dir = t.path();
        let vroot = exe_dir.join("versions");
        assert!(resolve_asset(exe_dir, &vroot, "/../../windows/x").is_none());
        assert!(resolve_asset(exe_dir, &vroot, "/a/./../b").is_none());
    }

    #[test]
    fn mime_mapping() {
        assert_eq!(mime_of("index.html"), "text/html; charset=utf-8");
        assert_eq!(mime_of("assets/app.JS"), "text/javascript");
        assert_eq!(mime_of("a.wasm"), "application/wasm");
        assert_eq!(mime_of("noext"), "application/octet-stream");
    }
}
