//! 前端 dist 热替换 loader（spec §7；runbook §6 已接线）。
//!
//! 布局（按优先级）：
//! 1. `versions/web/<active>/<rel>` —— 增量更新/手工热替换，改前端无需重编 Rust；
//! 2. `exe_dir/web/<rel>` —— 出厂 web 资源（`bundle.resources` 由 `../dist` 映射而来）；
//! 3. 内嵌 `frontendDist` —— 最后兜底，老安装包没有 `web/` 时不白屏。
//!
//! 生效链路：`pnpm build` → 把 `apps/desktop/dist/*` 复制到
//! `<安装根>/versions/web/<ver>/` → `<安装根>/web-active.txt` 写一行 `<ver>` → 重启。
//!
//! ⚠️ 窗口 url 是 `eaide://localhost/`（tauri.conf.json；BUGFIX #194 后不再带
//! `index.html` 后缀，避免 react-router 的 pathname 落在入口文件上 404）；
//! dev 模式由 lib.rs 改回 `WebviewUrl::App("index.html")` 以保留 devUrl + HMR。

use std::path::{Path, PathBuf};

use tauri::http::{header::CONTENT_TYPE, Response, StatusCode};
use tauri::{AppHandle, Runtime};

/// 归一化请求路径：去首尾 `/`、空路径视为 `index.html`。
/// 拒绝含 `..` 分段 / 反斜杠 / 盘符戒号 / NUL 的路径（Windows 下 `\` 也是分隔符、
/// `:` 可拼出 `C:/…`戒 UNC 经 `join` 整串替换 base → 任意本地文件读取）。
/// 不做百分号解码（`uri().path()` 为未解码原文）：若将来补解码，必须在解码后
/// 重新跑本函数的 `..`/`\`/`:` 校验，否则编码型穿越（`%2e%2e%2f`）会回来。
fn normalize_rel(path: &str) -> Option<String> {
    let rel = path.trim_start_matches('/').trim_end_matches('/');
    let rel = if rel.is_empty() { "index.html" } else { rel };
    if rel.contains('\0') || rel.contains('\\') || rel.contains(':') {
        return None;
    }
    if rel.split('/').any(|seg| seg == "..") {
        return None;
    }
    Some(rel.to_string())
}

/// 末段无扩展名 → 视为 SPA history 路由（深链需回落 index.html）。
fn is_spa_route(rel: &str) -> bool {
    !rel.rsplit('/').next().unwrap_or("").contains('.')
}

/// 把请求 URI path 解析为磁盘文件；找不到具体文件且无扩展名 → SPA 回落 index.html。
///
/// 优先顺序：`versions/web/<active>` > 出厂 `<exe_dir>/web`。
pub fn resolve_asset(exe_dir: &Path, vroot: &Path, uri_path: &str) -> Option<PathBuf> {
    let rel = normalize_rel(uri_path)?;
    let looks_spa = is_spa_route(&rel);

    let pick_in = |base: &Path| -> Option<PathBuf> {
        let cand = base.join(&rel);
        // 纵深防御：join 碰到绝对路径会整串替换 base（.. 已由 normalize_rel 拦掉，
        // 此处兜住剩余“跳出 base”情形）
        if !cand.starts_with(base) {
            return None;
        }
        if cand.is_file() {
            return Some(cand);
        }
        // SPA history 路由深链：无扩展名且未命中 → index.html
        if looks_spa {
            let idx = base.join("index.html");
            if idx.starts_with(base) && idx.is_file() {
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

// ─────────────────────────── 协议 handler ───────────────────────────

/// 一次前端请求的完整应答：磁盘 `web/` 优先，内嵌 `frontendDist` 兜底。
///
/// 由 `lib.rs` 的 `register_asynchronous_uri_scheme_protocol("eaide", …)` 在 worker
/// 线程调用。**任何失败路径都必须返回 Response**：吞掉 responder 会让窗口请求永久
/// 悬挂（无超时）。
pub fn serve<R: Runtime>(app: &AppHandle<R>, uri_path: &str) -> Response<Vec<u8>> {
    let Some(rel) = normalize_rel(uri_path) else {
        return plain(StatusCode::NOT_FOUND, "not found");
    };
    let Some(exe_dir) = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.to_path_buf()))
    else {
        return plain(StatusCode::INTERNAL_SERVER_ERROR, "no exe_dir");
    };
    let vroot = crate::agent_manager::resolve_versions_root(&exe_dir);

    // 1) 磁盘 web：versions/web/<active> → 出厂 web
    //    出厂根：Windows/Linux 是 exe_dir/web；macOS .app 是 Contents/Resources/web
    //    （exe 在 Contents/MacOS/，与 resources 目录不同层）。
    for root in factory_roots(&exe_dir) {
        if let Some(file) = resolve_asset(&root, &vroot, uri_path) {
            if let Ok(bytes) = std::fs::read(&file) {
                let mime = mime_of(&file.to_string_lossy());
                // 磁盘 index.html 复用内嵌 assets 预计算的 CSP，origin 换成 eaide 后
                // 策略与出厂一致（否则页面裸奔无 CSP）。
                let csp = mime.starts_with("text/html").then(|| embedded_csp(app)).flatten();
                return respond(mime, csp, bytes);
            }
            // 读失败（权限/占用）→ 继续试下一个根 / 内嵌兜底
        }
    }

    // 2) 内嵌 frontendDist 兜底（老安装包没有 web/ 时不白屏）
    embedded_response(app, &rel)
}

/// 出厂 web 的候选根（顺序即优先级）。
/// 非 macOS 只有一个根：避免 `exe_dir/../Resources` 在 Windows 上误命中邻近目录。
fn factory_roots(exe_dir: &Path) -> impl Iterator<Item = PathBuf> {
    let mac_resources = cfg!(target_os = "macos").then(|| exe_dir.join("..").join("Resources"));
    std::iter::once(exe_dir.to_path_buf()).chain(mac_resources)
}

fn embedded_response<R: Runtime>(app: &AppHandle<R>, rel: &str) -> Response<Vec<u8>> {
    let key = if is_spa_route(rel) { "index.html" } else { rel };
    match app.asset_resolver().get(key.to_string()) {
        Some(asset) => {
            let mime = asset.mime_type;
            let csp = asset.csp_header;
            respond(&mime, csp, asset.bytes)
        }
        None => plain(StatusCode::NOT_FOUND, "not found"),
    }
}

/// 内嵌 index.html 上由 tauri-codegen 预计算的 CSP 头。
fn embedded_csp<R: Runtime>(app: &AppHandle<R>) -> Option<String> {
    app.asset_resolver()
        .get("index.html".to_string())
        .and_then(|a| a.csp_header)
}

fn respond(mime: &str, csp: Option<String>, bytes: Vec<u8>) -> Response<Vec<u8>> {
    let mut builder = Response::builder()
        .status(StatusCode::OK)
        .header(CONTENT_TYPE, mime)
        // 每次启动重新读盘；禁掉 WebView 缓存，否则换了 web/ 还加载旧 chunk。
        .header("cache-control", "no-cache");
    if let Some(csp) = csp {
        builder = builder.header("content-security-policy", csp);
    }
    match builder.body(bytes) {
        Ok(resp) => resp,
        Err(_) => plain(StatusCode::INTERNAL_SERVER_ERROR, "bad response"),
    }
}

fn plain(status: StatusCode, msg: &'static str) -> Response<Vec<u8>> {
    Response::builder()
        .status(status)
        .header(CONTENT_TYPE, "text/plain; charset=utf-8")
        .body(msg.as_bytes().to_vec())
        .unwrap_or_else(|_| Response::new(msg.as_bytes().to_vec()))
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
        // Windows 绕过面（Review 439e0a1 Critical#1）：反斜杠、盘符/UNC 绝对路径、NUL
        assert!(resolve_asset(exe_dir, &vroot, "\\..\\..\\windows\\win.ini").is_none());
        assert!(resolve_asset(exe_dir, &vroot, "/C:/windows/win.ini").is_none());
        assert!(resolve_asset(exe_dir, &vroot, "/a/b\0c").is_none());
    }

    #[test]
    fn spa_no_index_returns_none_and_active_spa_fallback() {
        let t = Tmp::new("spano");
        let exe_dir = t.path();
        let vroot = exe_dir.join("versions");
        // 出厂 web 完全不存在 → 无扩展名深链应 None
        assert!(resolve_asset(exe_dir, &vroot, "/some/route").is_none());
        // active 层自己的 SPA 回落
        write(&vroot.join("web/0.3.0"), "index.html");
        std::fs::write(vroot.join("web-active.txt"), "0.3.0\n").unwrap();
        assert_eq!(
            resolve_asset(exe_dir, &vroot, "/deep/link").unwrap(),
            vroot.join("web/0.3.0/index.html")
        );
    }

    #[test]
    fn mime_mapping() {
        assert_eq!(mime_of("index.html"), "text/html; charset=utf-8");
        assert_eq!(mime_of("assets/app.JS"), "text/javascript");
        assert_eq!(mime_of("a.wasm"), "application/wasm");
        assert_eq!(mime_of("noext"), "application/octet-stream");
    }

    /// 内嵌兜底的路由判定：只有“末段带扩展名”才当具体文件，其余回落 index.html。
    #[test]
    fn spa_route_detection() {
        assert!(!is_spa_route("index.html"));
        assert!(is_spa_route("settings/terminal"));
        assert!(!is_spa_route("assets/index-abc123.js"));
        assert!(!is_spa_route("fonts/x.woff2"));
    }
}
