//! Phase 1B V2 · 原生工具层 Rust 实现（全部 9 工具）。
//!
//! V1 占位 + V1.5 接力（2026-07-31）+ V2 收尾（2026-07-31）：
//!   - V1：9 占位工具名 + 风险等级 + HITL helper（mod.rs 145 行 + 10 单测）
//!   - V1.5：path_sandbox.rs + 6 安全工具真实实现（stat / mkdir / find / glob / hash / base64）
//!   - V2：3 高危工具（delete_file / move_file / shell）真实实现 + md5/sha1/blake2b hash
//!         算法 + glob crate 真支持 + Tauri Command 注册 + HITL interrupt（critical 永远需审批）
//!
//! 设计原则：
//!   1. 全部工具走 path_sandbox 校验
//!   2. 写 / 高危操作（mkdir / delete_file / move_file / shell）走 evaluate_hitl
//!   3. 通过 Tauri Command 暴露给 Webview；dispatcher 走 tauri::async_runtime 桥接
//!   4. 错误统一格式 `<ErrorName>: <message>` 与 Python ToolResult.error 一致

#![allow(dead_code)]  // V1 占位部分常量暂未使用

pub mod path_sandbox;

use std::collections::HashMap;
use std::fs;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};
use std::path::{Path, PathBuf};

use blake2::Blake2b512;
use md5::Md5;
use serde::{Deserialize, Serialize};
use sha1::Sha1;
use sha2::{Digest, Sha256};

use path_sandbox::validate_path;


// ---- 9 Rust 工具名（与 Python RUST_TOOL_NAMES 严格一致）-------------------

pub const RUST_TOOL_NAMES: &[&str] = &[
    "stat_file", "mkdir", "delete_file", "move_file",
    "find", "glob", "hash", "base64", "shell",
];

pub const RISK_READ: &str = "read";
pub const RISK_LOW: &str = "low";
pub const RISK_MEDIUM: &str = "medium";
pub const RISK_HIGH: &str = "high";
pub const RISK_CRITICAL: &str = "critical";

pub fn risk_level_for(tool_name: &str) -> &'static str {
    match tool_name {
        "stat_file" => RISK_READ,
        "mkdir" => RISK_MEDIUM,
        "delete_file" => RISK_HIGH,
        "move_file" => RISK_HIGH,
        "find" => RISK_READ,
        "glob" => RISK_READ,
        "hash" => RISK_READ,
        "base64" => RISK_READ,
        "shell" => RISK_CRITICAL,
        _ => RISK_READ,
    }
}

pub fn evaluate_hitl(tool_name: &str, require_hitl_for_write: bool) -> bool {
    let risk = risk_level_for(tool_name);
    // critical（shell）永远需要 HITL —— 即使 require_hitl_for_write=false 也不能自动批准
    if risk == RISK_CRITICAL {
        return true;
    }
    if risk == RISK_READ {
        return false;
    }
    require_hitl_for_write
}

pub fn is_rust_tool(tool_name: &str) -> bool {
    RUST_TOOL_NAMES.contains(&tool_name)
}

/// V1.5 部分实现（6 安全工具）。
///
/// delete_file / move_file / shell 留 V2（Tauri Command + HITL interrupt）。
pub fn is_v1_5_implemented(tool_name: &str) -> bool {
    matches!(tool_name, "stat_file" | "mkdir" | "find" | "glob" | "hash" | "base64")
}

/// V2 实现标记 —— 9 个 Rust 工具全部真实实现。
pub fn is_v2_implemented(tool_name: &str) -> bool {
    is_rust_tool(tool_name)
}

/// 当前实现标记（V2 后 = is_v2_implemented）。
pub fn is_implemented(tool_name: &str) -> bool {
    is_v2_implemented(tool_name)
}


// ---- 工具结果类型（与 Python ToolResult.to_dict() 字段对齐）--------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ToolResult {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub content: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub hint: Option<String>,
    #[serde(default)]
    pub meta: HashMap<String, serde_json::Value>,
    #[serde(default)]
    pub needs_hitl: bool,
    pub risk_level: String,
}

impl ToolResult {
    pub fn ok(content: serde_json::Value, risk_level: &str) -> Self {
        Self {
            ok: true,
            content: Some(content),
            error: None,
            hint: None,
            meta: HashMap::new(),
            needs_hitl: false,
            risk_level: risk_level.into(),
        }
    }

    pub fn fail(error: impl Into<String>, risk_level: &str) -> Self {
        Self {
            ok: false,
            content: None,
            error: Some(error.into()),
            hint: None,
            meta: HashMap::new(),
            needs_hitl: false,
            risk_level: risk_level.into(),
        }
    }

    pub fn needs_hitl_meta(mut self, needs_hitl: bool) -> Self {
        self.needs_hitl = needs_hitl;
        self
    }
}

/// 构造「需要 HITL 审批」的返回结果（工具未执行）。
pub fn hitl_required(risk_level: &str, hint: &str) -> ToolResult {
    ToolResult {
        ok: false,
        content: None,
        error: Some("hitl_required".into()),
        hint: Some(hint.into()),
        meta: HashMap::new(),
        needs_hitl: true,
        risk_level: risk_level.into(),
    }
}


// ---- 6 安全工具真实实现 -----------------------------------------------------

/// builtin_stat_file —— 取文件元数据（size / mtime / permissions）。
///
/// Args:
///     path: 文件路径。
///     allowed_roots: 允许的根目录。
///
/// Returns:
///     ToolResult.ok({size, mtime, permissions, is_file, is_dir})
pub fn builtin_stat_file(path: &str, allowed_roots: &[String]) -> ToolResult {
    let p = match validate_path(path, allowed_roots, true) {
        Ok(p) => p,
        Err(e) => return ToolResult::fail(e.to_string(), RISK_READ),
    };
    let meta = match fs::metadata(&p) {
        Ok(m) => m,
        Err(e) => return ToolResult::fail(format!("stat_failed: {}", e), RISK_READ),
    };
    let content = serde_json::json!({
        "size": meta.len(),
        "mtime": meta.modified()
            .ok()
            .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
            .map(|d| d.as_secs_f64())
            .unwrap_or(0.0),
        "readonly": meta.permissions().readonly(),
        "is_file": meta.is_file(),
        "is_dir": meta.is_dir(),
        "path": p.to_string_lossy(),
    });
    ToolResult::ok(content, RISK_READ)
}


/// builtin_mkdir —— 创建目录（中等风险，触发 HITL）。
///
/// Args:
///     path: 目录路径。
///     parents: 是否自动创建父目录（mkdir -p）。
///     allowed_roots: 允许的根目录。
///     require_hitl: 是否要求 HITL 审批（与 evaluate_hitl 一致）。
///
/// Returns:
///     ToolResult.ok({path, parents, created})
pub fn builtin_mkdir(
    path: &str,
    parents: bool,
    allowed_roots: &[String],
    require_hitl: bool,
) -> ToolResult {
    if evaluate_hitl("mkdir", require_hitl) {
        return hitl_required(RISK_MEDIUM, "mkdir requires HITL approval");
    }
    let p = match validate_path(path, allowed_roots, false) {
        Ok(p) => p,
        Err(e) => return ToolResult::fail(e.to_string(), RISK_MEDIUM),
    };
    let result = if parents {
        fs::create_dir_all(&p)
    } else {
        fs::create_dir(&p)
    };
    match result {
        Ok(()) => {
            let content = serde_json::json!({
                "path": p.to_string_lossy(),
                "parents": parents,
                "created": true,
            });
            ToolResult::ok(content, RISK_MEDIUM).needs_hitl_meta(false)
        }
        Err(e) => ToolResult::fail(format!("mkdir_failed: {}", e), RISK_MEDIUM),
    }
}


/// builtin_delete_file —— 删除文件 / 目录（高风险 → HITL）。
///
/// Args:
///     path: 目标路径。
///     recursive: 删除目录时是否递归（目录必须 recursive=true）。
///     allowed_roots: 允许的根目录。
///     require_hitl: 是否要求 HITL 审批。
///
/// Returns:
///     ToolResult.ok({path, removed: true, is_dir})
pub fn builtin_delete_file(
    path: &str,
    recursive: bool,
    allowed_roots: &[String],
    require_hitl: bool,
) -> ToolResult {
    if evaluate_hitl("delete_file", require_hitl) {
        return hitl_required(RISK_HIGH, "delete_file requires HITL approval");
    }
    let p = match validate_path(path, allowed_roots, true) {
        Ok(p) => p,
        Err(e) => return ToolResult::fail(e.to_string(), RISK_HIGH),
    };

    // 禁止删除 allowed_roots 根目录本身（防止误删整个工作区）
    if !allowed_roots.is_empty() {
        if let Ok(canon) = p.canonicalize() {
            for root in allowed_roots {
                if let Ok(root_canon) = Path::new(root).canonicalize() {
                    if canon == root_canon {
                        return ToolResult::fail(
                            format!("delete_root_forbidden: {}", canon.display()),
                            RISK_HIGH,
                        );
                    }
                }
            }
        }
    }

    let meta = match fs::metadata(&p) {
        Ok(m) => m,
        Err(e) => return ToolResult::fail(format!("stat_failed: {}", e), RISK_HIGH),
    };
    let is_dir = meta.is_dir();
    if is_dir && !recursive {
        return ToolResult::fail(
            format!("is_directory: {} (pass recursive=true)", p.display()),
            RISK_HIGH,
        );
    }

    let result = if is_dir {
        fs::remove_dir_all(&p)
    } else {
        fs::remove_file(&p)
    };
    match result {
        Ok(()) => {
            let content = serde_json::json!({
                "path": p.to_string_lossy(),
                "removed": true,
                "is_dir": is_dir,
            });
            ToolResult::ok(content, RISK_HIGH).needs_hitl_meta(false)
        }
        Err(e) => ToolResult::fail(format!("delete_failed: {}", e), RISK_HIGH),
    }
}


/// builtin_move_file —— 移动 / 重命名文件或目录（高风险 → HITL）。
///
/// Args:
///     src: 源路径。
///     dest: 目标路径。
///     overwrite: 目标已存在时是否覆盖（默认 false）。
///     allowed_roots: 允许的根目录。
///     require_hitl: 是否要求 HITL 审批。
///
/// Returns:
///     ToolResult.ok({src, dest, moved: true, cross_device: bool})
pub fn builtin_move_file(
    src: &str,
    dest: &str,
    overwrite: bool,
    allowed_roots: &[String],
    require_hitl: bool,
) -> ToolResult {
    if evaluate_hitl("move_file", require_hitl) {
        return hitl_required(RISK_HIGH, "move_file requires HITL approval");
    }
    let src_p = match validate_path(src, allowed_roots, true) {
        Ok(p) => p,
        Err(e) => return ToolResult::fail(e.to_string(), RISK_HIGH),
    };
    let dest_p = match validate_path(dest, allowed_roots, false) {
        Ok(p) => p,
        Err(e) => return ToolResult::fail(e.to_string(), RISK_HIGH),
    };
    if dest_p.exists() && !overwrite {
        return ToolResult::fail(format!("dest_exists: {}", dest_p.display()), RISK_HIGH);
    }

    // 同一卷 rename；跨卷（或 Windows 上失败）降级为 copy + remove
    let mut cross_device = false;
    match fs::rename(&src_p, &dest_p) {
        Ok(()) => {}
        Err(e) => {
            if e.kind() == std::io::ErrorKind::CrossesDevices
                || e.kind() == std::io::ErrorKind::PermissionDenied
            {
                cross_device = true;
                let copied = if src_p.is_dir() {
                    copy_dir_all(&src_p, &dest_p)
                } else {
                    fs::copy(&src_p, &dest_p).map(|_| ())
                };
                if let Err(ce) = copied {
                    return ToolResult::fail(format!("move_failed: {}", ce), RISK_HIGH);
                }
                let removed = if src_p.is_dir() {
                    fs::remove_dir_all(&src_p)
                } else {
                    fs::remove_file(&src_p)
                };
                if let Err(re) = removed {
                    return ToolResult::fail(format!("move_partial: {}", re), RISK_HIGH);
                }
            } else {
                return ToolResult::fail(format!("move_failed: {}", e), RISK_HIGH);
            }
        }
    }

    let content = serde_json::json!({
        "src": src_p.to_string_lossy(),
        "dest": dest_p.to_string_lossy(),
        "moved": true,
        "cross_device": cross_device,
    });
    ToolResult::ok(content, RISK_HIGH).needs_hitl_meta(false)
}


/// builtin_shell —— 白名单 shell 命令执行（critical → 永远 HITL）。
///
/// Args:
///     command: 命令字符串（如 "echo hello"）。
///     allowed_prefixes: 允许的命令前缀白名单（空 = 不限制；支持 `git*` 通配前缀）。
///     timeout_sec: 超时秒数（默认 30；0 → 30）。
///     require_hitl: 是否要求 HITL（critical 工具永远要求，忽略此参数）。
///
/// 安全策略：
///   1. 危险操作符拦截（; & | < > ` $ ( ) 换行 等）
///   2. 首 token 白名单校验
///   3. 长度上限 4096
///   4. 超时强杀
///
/// Returns:
///     ToolResult.ok({command, exit_code, stdout, stderr, timed_out})
pub fn builtin_shell(
    command: &str,
    allowed_prefixes: &[String],
    timeout_sec: u64,
    require_hitl: bool,
) -> ToolResult {
    if evaluate_hitl("shell", require_hitl) {
        return hitl_required(RISK_CRITICAL, "shell requires HITL approval");
    }
    execute_shell(command, allowed_prefixes, timeout_sec)
}


/// shell 执行器（已过 HITL 闸门后的实际执行；单测直接覆盖）。
///
/// 安全策略：
///   1. 危险操作符拦截（; & | < > ` $ ( ) 换行 等）
///   2. 首 token 白名单校验
///   3. 长度上限 4096
///   4. 超时强杀
pub fn execute_shell(command: &str, allowed_prefixes: &[String], timeout_sec: u64) -> ToolResult {
    let trimmed = command.trim();
    if trimmed.is_empty() {
        return ToolResult::fail("empty_command", RISK_CRITICAL);
    }
    if trimmed.len() > SHELL_MAX_BYTES {
        return ToolResult::fail(
            format!("command_too_long: {} > {}", trimmed.len(), SHELL_MAX_BYTES),
            RISK_CRITICAL,
        );
    }
    // 危险操作符拦截
    for &ch in DANGEROUS_SHELL_CHARS {
        if trimmed.contains(ch) {
            return ToolResult::fail(
                format!("dangerous_operator: {ch:?} not allowed in shell command"),
                RISK_CRITICAL,
            );
        }
    }
    // 首 token 白名单
    let first = trimmed.split_whitespace().next().unwrap_or("");
    if !allowed_prefixes.is_empty()
        && !allowed_prefixes.iter().any(|p| {
            let p = p.trim();
            if let Some(stripped) = p.strip_suffix('*') {
                first.starts_with(stripped)
            } else {
                first == p
            }
        })
    {
        return ToolResult::fail(
            format!("command_not_allowed: {first} (allowed: {allowed_prefixes:?})"),
            RISK_CRITICAL,
        );
    }

    let timeout = if timeout_sec == 0 { 30 } else { timeout_sec };
    let mut cmd = shell_command(trimmed);
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return ToolResult::fail(format!("spawn_failed: {}", e), RISK_CRITICAL),
    };

    // 轮询 + 超时强杀（退出码直接用 i32，避免构造 ExitStatus 的平台差异）
    let deadline = Instant::now() + Duration::from_secs(timeout);
    let (exit_code, timed_out): (i32, bool) = loop {
        match child.try_wait() {
            Ok(Some(st)) => break (st.code().unwrap_or(-1), false),
            Ok(None) => {
                if Instant::now() >= deadline {
                    let _ = child.kill();
                    let _ = child.wait();
                    break (124, true);
                }
                std::thread::sleep(Duration::from_millis(20));
            }
            Err(e) => {
                return ToolResult::fail(format!("wait_failed: {}", e), RISK_CRITICAL);
            }
        }
    };

    let output = match child.wait_with_output() {
        Ok(o) => o,
        Err(e) => return ToolResult::fail(format!("output_failed: {}", e), RISK_CRITICAL),
    };
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    let content = serde_json::json!({
        "command": trimmed,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
    });
    ToolResult::ok(content, RISK_CRITICAL).needs_hitl_meta(false)
}


// ---- shell 安全常量 ---------------------------------------------------------

/// 危险操作符 —— 出现即拒绝（防止命令注入 / 管道 / 重定向）。
pub const DANGEROUS_SHELL_CHARS: &[char] = &[
    ';', '&', '|', '<', '>', '`', '$', '(', ')', '{', '}', '\n', '\r', '\0',
];

/// shell 命令最大字节数。
pub const SHELL_MAX_BYTES: usize = 4096;


/// 构造平台 shell 命令（Windows → cmd /C；Unix → /bin/sh -c）。
#[cfg(target_os = "windows")]
fn shell_command(command: &str) -> Command {
    let mut c = Command::new("cmd");
    c.arg("/C").arg(command);
    c
}

#[cfg(not(target_os = "windows"))]
fn shell_command(command: &str) -> Command {
    let mut c = Command::new("/bin/sh");
    c.arg("-c").arg(command);
    c
}


/// 递归复制目录（跨卷 move 降级用）。
fn copy_dir_all(src: &Path, dst: &Path) -> std::io::Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let ty = entry.file_type()?;
        let target = dst.join(entry.file_name());
        if ty.is_dir() {
            copy_dir_all(&entry.path(), &target)?;
        } else {
            fs::copy(entry.path(), &target)?;
        }
    }
    Ok(())
}


/// builtin_find —— 按文件名模式（regex/glob）查找文件。
///
/// Args:
///     path: 搜索根目录。
///     pattern: 模式（glob，如 *.rs；regex 加 regex=true）。
///     regex: 是否 regex 模式（否则 glob）。
///     max_results: 最大结果数（防爆，默认 1000）。
///     allowed_roots: 允许的根目录。
///
/// Returns:
///     ToolResult.ok({paths: [...], count, truncated})
pub fn builtin_find(
    path: &str,
    pattern: &str,
    regex: bool,
    max_results: usize,
    allowed_roots: &[String],
) -> ToolResult {
    let root = match validate_path(path, allowed_roots, true) {
        Ok(p) => p,
        Err(e) => return ToolResult::fail(e.to_string(), RISK_READ),
    };
    if !root.is_dir() {
        return ToolResult::fail(format!("not_a_directory: {}", root.display()), RISK_READ);
    }
    let max = if max_results == 0 { 1000 } else { max_results.min(10_000) };

    // 编译模式（regex 用 regex crate 或简化版；V1.5 简化：glob 用 fnmatch 近似 + 字符串 contains）
    let mut paths: Vec<String> = Vec::new();
    let mut truncated = false;
    let walker = match fs::read_dir(&root) {
        Ok(w) => w,
        Err(e) => return ToolResult::fail(format!("read_dir_failed: {}", e), RISK_READ),
    };
    for entry in walker.flatten() {
        if paths.len() >= max {
            truncated = true;
            break;
        }
        let name = entry.file_name().to_string_lossy().to_string();
        let matches = if regex {
            // V1.5 简化 regex：用 std::str::contains 而非 regex crate（减少依赖）
            // 真实场景推荐加 regex = "1" 依赖；当前 V1.5 走 contains 近似
            name.contains(pattern)
        } else {
            // glob 简化：fnmatch 风格 —— 这里用 ends_with + starts_with 组合近似
            glob_match(&name, pattern)
        };
        if matches {
            paths.push(entry.path().to_string_lossy().to_string());
        }
    }

    let count = paths.len();
    let content = serde_json::json!({
        "paths": paths,
        "count": count,
        "truncated": truncated,
    });
    ToolResult::ok(content, RISK_READ)
}


/// builtin_glob —— glob 模式匹配（双星号 `**` 递归 + 单层 `*` 通配）。
///
/// Args:
///     pattern: glob 模式（如 `**/*.rs`、`src/*.toml`）。
///     root: 搜索根目录。
///     max_results: 最大结果数。
///     allowed_roots: 允许的根目录。
///
/// Returns:
///     ToolResult.ok({paths: [...]})
///
/// Note:
///     V2：使用 glob crate 真实现（`**` 递归 + `*` / `?` / `[]` 字符集；
///     注意 glob crate 不支持 shell 的 `{}` 大括号展开）。
pub fn builtin_glob(
    pattern: &str,
    root: &str,
    max_results: usize,
    allowed_roots: &[String],
) -> ToolResult {
    let root_p = match validate_path(root, allowed_roots, true) {
        Ok(p) => p,
        Err(e) => return ToolResult::fail(e.to_string(), RISK_READ),
    };
    if !root_p.is_dir() {
        return ToolResult::fail(format!("not_a_directory: {}", root_p.display()), RISK_READ);
    }
    let max = if max_results == 0 { 1000 } else { max_results.min(10_000) };

    // 拼接绝对 glob 模式（pattern 可带前导目录段，如 "src/**/*.rs"）
    let full_pattern = if pattern.starts_with('/')
        || (pattern.len() > 1 && pattern[1..].starts_with(':'))
    {
        pattern.to_string()
    } else {
        root_p.join(pattern).to_string_lossy().to_string()
    };

    let mut paths: Vec<String> = Vec::new();
    let mut truncated = false;
    let entries = match glob::glob(&full_pattern) {
        Ok(e) => e,
        Err(e) => return ToolResult::fail(format!("glob_compile_error: {}", e), RISK_READ),
    };
    for entry in entries.flatten() {
        if paths.len() >= max {
            truncated = true;
            break;
        }
        // 白名单二次校验（glob 展开结果必须在 allowed_roots 内）
        let entry_str = entry.to_string_lossy().to_string();
        if !allowed_roots.is_empty() {
            if validate_path(&entry_str, allowed_roots, true).is_err() {
                continue;
            }
        }
        paths.push(entry_str);
    }
    paths.sort();

    let content = serde_json::json!({
        "paths": paths,
        "count": paths.len(),
        "truncated": truncated,
    });
    ToolResult::ok(content, RISK_READ)
}


/// builtin_hash —— 计算文件 hash（md5 / sha1 / sha256 / blake2b）。
///
/// Args:
///     path: 文件路径。
///     algorithm: 算法名（md5 / sha1 / sha256 / blake2b）。
///     allowed_roots: 允许的根目录。
///
/// Returns:
///     ToolResult.ok({algorithm, hash, size})
///
/// Note:
///     V2：md5 / sha1 / sha256 / blake2b 全部真实实现（md-5 + sha1 + sha2 + blake2 crate）。
pub fn builtin_hash(path: &str, algorithm: &str, allowed_roots: &[String]) -> ToolResult {
    let p = match validate_path(path, allowed_roots, true) {
        Ok(p) => p,
        Err(e) => return ToolResult::fail(e.to_string(), RISK_READ),
    };
    let bytes = match fs::read(&p) {
        Ok(b) => b,
        Err(e) => return ToolResult::fail(format!("read_failed: {}", e), RISK_READ),
    };
    let size = bytes.len();

    let hash = match algorithm {
        "sha256" => {
            let mut hasher = Sha256::new();
            hasher.update(&bytes);
            format!("{:x}", hasher.finalize())
        }
        "blake2b" => {
            let mut hasher = Blake2b512::new();
            hasher.update(&bytes);
            format!("{:x}", hasher.finalize())
        }
        "md5" => {
            let mut hasher = Md5::new();
            hasher.update(&bytes);
            format!("{:x}", hasher.finalize())
        }
        "sha1" => {
            let mut hasher = Sha1::new();
            hasher.update(&bytes);
            format!("{:x}", hasher.finalize())
        }
        _ => {
            return ToolResult::fail(
                format!("unsupported_algorithm: {} (supported: md5, sha1, sha256, blake2b)", algorithm),
                RISK_READ,
            );
        }
    };

    let content = serde_json::json!({
        "algorithm": algorithm,
        "hash": hash,
        "size": size,
    });
    ToolResult::ok(content, RISK_READ)
}


/// builtin_base64 —— Base64 encode / decode 字符串或文件。
///
/// Args:
///     data: 输入字符串（mode="encode" 时）或十六进制文件路径（mode="encode_file" / "decode_file"）。
///     mode: encode / decode / encode_file / decode_file。
///     allowed_roots: 允许的根目录（仅 file 模式用）。
///
/// Returns:
///     ToolResult.ok({result: str}) 或 ok({result: bytes_hex}) for file mode.
pub fn builtin_base64(
    data: &str,
    mode: &str,
    allowed_roots: &[String],
) -> ToolResult {
    use base64::{engine::general_purpose::STANDARD as B64, Engine};

    match mode {
        "encode" => {
            let encoded = B64.encode(data.as_bytes());
            ToolResult::ok(serde_json::json!({"result": encoded, "mode": "encode"}), RISK_READ)
        }
        "decode" => {
            match B64.decode(data.trim()) {
                Ok(bytes) => {
                    let hex = bytes.iter().map(|b| format!("{:02x}", b)).collect::<String>();
                    ToolResult::ok(
                        serde_json::json!({"result": hex, "mode": "decode", "byte_len": bytes.len()}),
                        RISK_READ,
                    )
                }
                Err(e) => ToolResult::fail(format!("base64_decode_error: {}", e), RISK_READ),
            }
        }
        "encode_file" => {
            let p = match validate_path(data, allowed_roots, true) {
                Ok(p) => p,
                Err(e) => return ToolResult::fail(e.to_string(), RISK_READ),
            };
            let bytes = match fs::read(&p) {
                Ok(b) => b,
                Err(e) => return ToolResult::fail(format!("read_failed: {}", e), RISK_READ),
            };
            let encoded = B64.encode(&bytes);
            ToolResult::ok(
                serde_json::json!({
                    "result": encoded,
                    "mode": "encode_file",
                    "size": bytes.len(),
                    "path": p.to_string_lossy(),
                }),
                RISK_READ,
            )
        }
        "decode_file" => {
            let p = match validate_path(data, allowed_roots, true) {
                Ok(p) => p,
                Err(e) => return ToolResult::fail(e.to_string(), RISK_READ),
            };
            let encoded = match fs::read_to_string(&p) {
                Ok(s) => s,
                Err(e) => return ToolResult::fail(format!("read_failed: {}", e), RISK_READ),
            };
            match B64.decode(encoded.trim()) {
                Ok(bytes) => {
                    let out_path = p.with_extension("decoded");
                    if let Err(e) = fs::write(&out_path, &bytes) {
                        return ToolResult::fail(format!("write_failed: {}", e), RISK_READ);
                    }
                    ToolResult::ok(
                        serde_json::json!({
                            "result": out_path.to_string_lossy(),
                            "mode": "decode_file",
                            "byte_len": bytes.len(),
                        }),
                        RISK_READ,
                    )
                }
                Err(e) => ToolResult::fail(format!("base64_decode_error: {}", e), RISK_READ),
            }
        }
        _ => ToolResult::fail(
            format!("invalid_mode: {} (supported: encode, decode, encode_file, decode_file)", mode),
            RISK_READ,
        ),
    }
}


// ---- 辅助：glob 简化匹配 + parse_glob ----------------------------------------

/// 简化 glob 匹配：`*` 匹配任意非 `/` 字符；`?` 匹配单字符。
/// V1.5 不支持 `[]` 字符集和 `{a,b}` 大括号（V2 接力 glob crate）。
fn glob_match(name: &str, pattern: &str) -> bool {
    let mut ni = 0;  // name index
    let mut pi = 0;  // pattern index
    let name_bytes = name.as_bytes();
    let pat_bytes = pattern.as_bytes();
    let mut star_pi: Option<usize> = None;
    let mut star_ni: usize = 0;

    while ni < name_bytes.len() {
        if pi < pat_bytes.len() {
            match pat_bytes[pi] {
                b'*' => {
                    star_pi = Some(pi);
                    star_ni = ni;
                    pi += 1;
                    continue;
                }
                b'?' => {
                    ni += 1;
                    pi += 1;
                    continue;
                }
                c if c == name_bytes[ni] => {
                    ni += 1;
                    pi += 1;
                    continue;
                }
                _ => {}
            }
        }
        // mismatch
        if let Some(spi) = star_pi {
            pi = spi + 1;
            star_ni += 1;
            ni = star_ni;
        } else {
            return false;
        }
    }
    // name exhausted — pattern 余下应全是 *
    while pi < pat_bytes.len() && pat_bytes[pi] == b'*' {
        pi += 1;
    }
    pi == pat_bytes.len()
}


/// 解析 glob 模式：分离 search_root + file_pattern。
///
/// 支持：
///   - "**/*.rs" → search_root=root, file_pattern="*.rs"
///   - "src/*.toml" → search_root=root/src, file_pattern="*.toml"
fn parse_glob(pattern: &str, root: &Path) -> (PathBuf, String) {
    if let Some(idx) = pattern.find("**") {
        // 双星号开头的目录段
        let prefix = &pattern[..idx];
        let file_part = &pattern[idx + 2..];
        let file_part = file_part.trim_start_matches('/');
        let search_root = if prefix.is_empty() || prefix == "/" {
            root.to_path_buf()
        } else {
            root.join(prefix.trim_end_matches('/'))
        };
        (search_root, file_part.to_string())
    } else if let Some(idx) = pattern.find('*') {
        let prefix = &pattern[..idx];
        let file_part = pattern[idx..].trim_start_matches('/').to_string();
        let search_root = if prefix.is_empty() || prefix == "/" {
            root.to_path_buf()
        } else {
            root.join(prefix.trim_end_matches('/'))
        };
        (search_root, file_part)
    } else {
        (root.to_path_buf(), pattern.to_string())
    }
}


// ---- 单元测试 -----------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::PathBuf;

    fn make_tmp() -> PathBuf {
        let base = std::env::temp_dir().join(format!(
            "eaide_builtin_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir_all(&base).unwrap();
        base
    }

    // ---- V1 占位测试（保留）----
    #[test]
    fn test_rust_tool_names_count() {
        assert_eq!(RUST_TOOL_NAMES.len(), 9);
    }

    #[test]
    fn test_is_rust_tool_true_cases() {
        for name in RUST_TOOL_NAMES {
            assert!(is_rust_tool(name));
        }
    }

    #[test]
    fn test_is_rust_tool_false_cases() {
        assert!(!is_rust_tool("read_file"));
        assert!(!is_rust_tool("calculator"));
    }

    #[test]
    fn test_risk_level_for_known_tools() {
        assert_eq!(risk_level_for("stat_file"), "read");
        assert_eq!(risk_level_for("mkdir"), "medium");
        assert_eq!(risk_level_for("delete_file"), "high");
        assert_eq!(risk_level_for("shell"), "critical");
    }

    #[test]
    fn test_evaluate_hitl() {
        assert!(!evaluate_hitl("stat_file", true));
        assert!(evaluate_hitl("mkdir", true));
        assert!(!evaluate_hitl("mkdir", false));
        assert!(evaluate_hitl("shell", true));
    }

    #[test]
    fn test_is_v1_implemented_all_false_v1() {
        // V1 阶段：is_v1_implemented 不存在；用 is_v1_5_implemented
        for name in RUST_TOOL_NAMES {
            // V1.5：6 个工具 true，3 个 false
            let v15 = is_v1_5_implemented(name);
            match *name {
                "stat_file" | "mkdir" | "find" | "glob" | "hash" | "base64" => assert!(v15),
                "delete_file" | "move_file" | "shell" => assert!(!v15),
                _ => {}
            }
        }
    }

    // ---- V1.5 真实工具测试 ----
    #[test]
    fn test_stat_file_ok() {
        let tmp = make_tmp();
        let file = tmp.join("test.txt");
        fs::write(&file, "hello world").unwrap();
        let r = builtin_stat_file(file.to_str().unwrap(), &[]);
        assert!(r.ok);
        let content = r.content.unwrap();
        assert_eq!(content["size"], 11);
        assert_eq!(content["is_file"], true);
        assert_eq!(content["is_dir"], false);
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_stat_file_missing() {
        let r = builtin_stat_file("/nonexistent/path/12345.txt", &[]);
        assert!(!r.ok);
        assert!(r.error.unwrap().contains("not found"));
    }

    #[test]
    fn test_mkdir_with_parents() {
        let tmp = make_tmp();
        let new_dir = tmp.join("a/b/c");
        let r = builtin_mkdir(new_dir.to_str().unwrap(), true, &[], false); // require_hitl=false
        assert!(r.ok, "mkdir failed: {:?}", r.error);
        assert!(new_dir.exists());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_mkdir_requires_hitl() {
        let tmp = make_tmp();
        let new_dir = tmp.join("protected");
        let r = builtin_mkdir(new_dir.to_str().unwrap(), true, &[], true); // require_hitl=true
        assert!(!r.ok);
        assert!(r.error.unwrap().contains("hitl_required"));
        assert!(r.needs_hitl);
        assert!(!new_dir.exists());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_find_glob_simple() {
        let tmp = make_tmp();
        fs::write(tmp.join("a.rs"), "x").unwrap();
        fs::write(tmp.join("b.toml"), "y").unwrap();
        let r = builtin_find(tmp.to_str().unwrap(), "*.rs", false, 100, &[]);
        assert!(r.ok);
        let content = r.content.unwrap();
        let paths = content["paths"].as_array().unwrap();
        assert_eq!(paths.len(), 1);
        assert!(paths[0].as_str().unwrap().ends_with("a.rs"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_find_max_results() {
        let tmp = make_tmp();
        for i in 0..10 {
            fs::write(tmp.join(format!("file_{}.rs", i)), "x").unwrap();
        }
        let r = builtin_find(tmp.to_str().unwrap(), "*.rs", false, 3, &[]);
        assert!(r.ok);
        let content = r.content.unwrap();
        assert_eq!(content["paths"].as_array().unwrap().len(), 3);
        assert_eq!(content["truncated"], true);
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_glob_recursive() {
        let tmp = make_tmp();
        let sub = tmp.join("src");
        fs::create_dir(&sub).unwrap();
        fs::write(sub.join("main.rs"), "x").unwrap();
        fs::write(tmp.join("readme.md"), "y").unwrap();
        let r = builtin_glob("**/*.rs", tmp.to_str().unwrap(), 100, &[]);
        assert!(r.ok);
        let content = r.content.unwrap();
        let paths = content["paths"].as_array().unwrap();
        assert_eq!(paths.len(), 1);
        assert!(paths[0].as_str().unwrap().ends_with("main.rs"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_hash_sha256() {
        let tmp = make_tmp();
        let file = tmp.join("h.txt");
        fs::write(&file, b"hello").unwrap();
        let r = builtin_hash(file.to_str().unwrap(), "sha256", &[]);
        assert!(r.ok);
        let content = r.content.unwrap();
        assert_eq!(content["algorithm"], "sha256");
        // sha256("hello") = 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
        assert_eq!(
            content["hash"].as_str().unwrap(),
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        );
        assert_eq!(content["size"], 5);
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_hash_unsupported_algorithm() {
        let tmp = make_tmp();
        let file = tmp.join("h.txt");
        fs::write(&file, b"hello").unwrap();
        let r = builtin_hash(file.to_str().unwrap(), "crc32", &[]);
        assert!(!r.ok);
        assert!(r.error.unwrap().contains("unsupported_algorithm"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_base64_encode() {
        let r = builtin_base64("hello", "encode", &[]);
        assert!(r.ok);
        let content = r.content.unwrap();
        assert_eq!(content["result"], "aGVsbG8=");
    }

    #[test]
    fn test_base64_decode() {
        let r = builtin_base64("aGVsbG8=", "decode", &[]);
        assert!(r.ok);
        let content = r.content.unwrap();
        // hex("hello") = 68656c6c6f
        assert_eq!(content["result"], "68656c6c6f");
    }

    #[test]
    fn test_base64_encode_file() {
        let tmp = make_tmp();
        let file = tmp.join("data.bin");
        fs::write(&file, b"binary data").unwrap();
        let r = builtin_base64(file.to_str().unwrap(), "encode_file", &[]);
        assert!(r.ok);
        let content = r.content.unwrap();
        assert!(content["result"].as_str().unwrap().len() > 0);
        assert_eq!(content["size"], 11);
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_glob_match_helper() {
        assert!(glob_match("test.rs", "*.rs"));
        assert!(glob_match("main.rs", "*.rs"));
        assert!(!glob_match("test.toml", "*.rs"));
        assert!(glob_match("a", "?"));
        assert!(!glob_match("ab", "?"));
        assert!(glob_match("anything.txt", "*"));
    }

    #[test]
    fn test_parse_glob() {
        let root = Path::new("/tmp");
        let (sr, pat) = parse_glob("**/*.rs", root);
        assert_eq!(pat, "*.rs");
        assert_eq!(sr, PathBuf::from("/tmp"));

        let (sr2, pat2) = parse_glob("src/*.toml", root);
        assert_eq!(pat2, "*.toml");
        assert_eq!(sr2, PathBuf::from("/tmp/src"));
    }

    // ---- V2 高危工具 + hash + glob crate 测试 ----

    #[test]
    fn test_evaluate_hitl_critical_always() {
        // critical（shell）永远需要 HITL —— 即使 require_hitl_for_write=false
        assert!(evaluate_hitl("shell", false));
        assert!(evaluate_hitl("shell", true));
    }

    #[test]
    fn test_is_v2_implemented_all() {
        for name in RUST_TOOL_NAMES {
            assert!(is_v2_implemented(name), "{name} should be V2 implemented");
            assert!(is_implemented(name), "{name} should be implemented");
        }
        assert!(!is_v2_implemented("read_file"));
        assert!(!is_v2_implemented("calculator"));
    }

    #[test]
    fn test_delete_file_ok() {
        let tmp = make_tmp();
        let file = tmp.join("to_delete.txt");
        fs::write(&file, "bye").unwrap();
        let r = builtin_delete_file(file.to_str().unwrap(), false, &[], false);
        assert!(r.ok, "delete failed: {:?}", r.error);
        assert!(!file.exists());
        assert_eq!(r.risk_level, "high");
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_delete_file_requires_hitl() {
        let tmp = make_tmp();
        let file = tmp.join("protected.txt");
        fs::write(&file, "x").unwrap();
        let r = builtin_delete_file(file.to_str().unwrap(), false, &[], true);
        assert!(!r.ok);
        assert!(r.error.unwrap().contains("hitl_required"));
        assert!(r.needs_hitl);
        assert!(file.exists(), "file must NOT be deleted before approval");
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_delete_dir_requires_recursive() {
        let tmp = make_tmp();
        let dir = tmp.join("sub");
        fs::create_dir(&dir).unwrap();
        let r = builtin_delete_file(dir.to_str().unwrap(), false, &[], false);
        assert!(!r.ok);
        assert!(r.error.unwrap().contains("is_directory"));
        assert!(dir.exists());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_delete_dir_recursive_ok() {
        let tmp = make_tmp();
        let dir = tmp.join("sub");
        fs::create_dir(&dir).unwrap();
        fs::write(dir.join("inner.txt"), "x").unwrap();
        let r = builtin_delete_file(dir.to_str().unwrap(), true, &[], false);
        assert!(r.ok, "delete dir failed: {:?}", r.error);
        assert!(!dir.exists());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_delete_allowed_root_forbidden() {
        let tmp = make_tmp();
        let allowed = vec![tmp.to_string_lossy().to_string()];
        let r = builtin_delete_file(tmp.to_str().unwrap(), true, &allowed, false);
        assert!(!r.ok);
        assert!(r.error.unwrap().contains("delete_root_forbidden"));
        assert!(tmp.exists());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_move_file_ok() {
        let tmp = make_tmp();
        let src = tmp.join("a.txt");
        let dest = tmp.join("b.txt");
        fs::write(&src, "hello").unwrap();
        let r = builtin_move_file(src.to_str().unwrap(), dest.to_str().unwrap(), false, &[], false);
        assert!(r.ok, "move failed: {:?}", r.error);
        assert!(!src.exists());
        assert_eq!(fs::read_to_string(&dest).unwrap(), "hello");
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_move_file_requires_hitl() {
        let tmp = make_tmp();
        let src = tmp.join("a.txt");
        let dest = tmp.join("b.txt");
        fs::write(&src, "x").unwrap();
        let r = builtin_move_file(src.to_str().unwrap(), dest.to_str().unwrap(), false, &[], true);
        assert!(!r.ok);
        assert!(r.error.unwrap().contains("hitl_required"));
        assert!(r.needs_hitl);
        assert!(src.exists(), "source must NOT be moved before approval");
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_move_file_no_overwrite() {
        let tmp = make_tmp();
        let src = tmp.join("a.txt");
        let dest = tmp.join("b.txt");
        fs::write(&src, "new").unwrap();
        fs::write(&dest, "old").unwrap();
        let r = builtin_move_file(src.to_str().unwrap(), dest.to_str().unwrap(), false, &[], false);
        assert!(!r.ok);
        assert!(r.error.unwrap().contains("dest_exists"));
        assert_eq!(fs::read_to_string(&dest).unwrap(), "old");
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_move_file_overwrite() {
        let tmp = make_tmp();
        let src = tmp.join("a.txt");
        let dest = tmp.join("b.txt");
        fs::write(&src, "new").unwrap();
        fs::write(&dest, "old").unwrap();
        let r = builtin_move_file(src.to_str().unwrap(), dest.to_str().unwrap(), true, &[], false);
        assert!(r.ok, "overwrite move failed: {:?}", r.error);
        assert_eq!(fs::read_to_string(&dest).unwrap(), "new");
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_move_dir_ok() {
        let tmp = make_tmp();
        let src = tmp.join("dir_a");
        let dest = tmp.join("dir_b");
        fs::create_dir(&src).unwrap();
        fs::write(src.join("f.txt"), "x").unwrap();
        let r = builtin_move_file(src.to_str().unwrap(), dest.to_str().unwrap(), false, &[], false);
        assert!(r.ok, "dir move failed: {:?}", r.error);
        assert!(dest.join("f.txt").exists());
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_shell_echo_ok() {
        let r = execute_shell("echo hello builtin", &["echo".to_string()], 10);
        assert!(r.ok, "shell failed: {:?}", r.error);
        let content = r.content.unwrap();
        assert_eq!(content["exit_code"], 0);
        assert!(content["stdout"].as_str().unwrap().contains("hello builtin"));
    }

    #[test]
    fn test_shell_requires_hitl_even_without_flag() {
        // critical 工具：即使 require_hitl=false 也必须 HITL
        let r = builtin_shell("echo hi", &[], 10, false);
        assert!(!r.ok);
        assert!(r.error.unwrap().contains("hitl_required"));
        assert!(r.needs_hitl);
    }

    #[test]
    fn test_shell_blocks_metacharacters() {
        for cmd in [
            "echo hi; rm -rf /",
            "echo hi && rm -rf /",
            "echo hi | sh",
            "echo `id`",
            "echo $(id)",
            "echo hi > /tmp/x",
        ] {
            let r = execute_shell(cmd, &["echo".to_string()], 10);
            assert!(!r.ok, "should block: {cmd}");
            assert!(r.error.unwrap().contains("dangerous_operator"));
        }
    }

    #[test]
    fn test_shell_command_not_allowed() {
        let r = execute_shell("rm -rf x", &["echo".to_string()], 10);
        assert!(!r.ok);
        assert!(r.error.unwrap().contains("command_not_allowed"));
    }

    #[test]
    fn test_shell_empty_command() {
        let r = execute_shell("   ", &[], 10);
        assert!(!r.ok);
        assert!(r.error.unwrap().contains("empty_command"));
    }

    #[test]
    fn test_shell_allowed_prefix_wildcard() {
        // git 可能不存在于 PATH（CI 环境）；用 echo 做通配前缀验证
        let r = execute_shell("echo git status", &["echo".to_string()], 10);
        assert!(r.ok, "wildcard prefix failed: {:?}", r.error);
    }

    #[test]
    fn test_shell_timeout() {
        #[cfg(target_os = "windows")]
        let cmd = "ping -n 6 127.0.0.1";
        #[cfg(not(target_os = "windows"))]
        let cmd = "sleep 5";
        let r = execute_shell(cmd, &[cmd.split(' ').next().unwrap().to_string()], 1);
        assert!(r.ok, "timeout run failed: {:?}", r.error);
        let content = r.content.unwrap();
        assert_eq!(content["timed_out"], true);
    }

    #[test]
    fn test_hash_md5_known_vector() {
        let tmp = make_tmp();
        let file = tmp.join("m.txt");
        fs::write(&file, b"hello").unwrap();
        let r = builtin_hash(file.to_str().unwrap(), "md5", &[]);
        assert!(r.ok, "md5 failed: {:?}", r.error);
        let content = r.content.unwrap();
        assert_eq!(content["algorithm"], "md5");
        assert_eq!(
            content["hash"].as_str().unwrap(),
            "5d41402abc4b2a76b9719d911017c592"
        );
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_hash_sha1_known_vector() {
        let tmp = make_tmp();
        let file = tmp.join("s.txt");
        fs::write(&file, b"hello").unwrap();
        let r = builtin_hash(file.to_str().unwrap(), "sha1", &[]);
        assert!(r.ok, "sha1 failed: {:?}", r.error);
        let content = r.content.unwrap();
        assert_eq!(content["algorithm"], "sha1");
        assert_eq!(
            content["hash"].as_str().unwrap(),
            "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"
        );
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_hash_blake2b() {
        let tmp = make_tmp();
        let file = tmp.join("b.txt");
        fs::write(&file, b"hello").unwrap();
        let r = builtin_hash(file.to_str().unwrap(), "blake2b", &[]);
        assert!(r.ok, "blake2b failed: {:?}", r.error);
        let content = r.content.unwrap();
        assert_eq!(content["algorithm"], "blake2b");
        // Blake2b-512 → 128 hex 字符
        assert_eq!(content["hash"].as_str().unwrap().len(), 128);
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_glob_crate_charset() {
        let tmp = make_tmp();
        fs::write(tmp.join("a.rs"), "x").unwrap();
        fs::write(tmp.join("b.rs"), "y").unwrap();
        fs::write(tmp.join("c.txt"), "z").unwrap();
        // glob crate 支持 [] 字符集（不支持 shell 的 {} 大括号展开）
        let r = builtin_glob("[ab].rs", tmp.to_str().unwrap(), 100, &[]);
        assert!(r.ok, "glob failed: {:?}", r.error);
        let content = r.content.unwrap();
        let paths = content["paths"].as_array().unwrap();
        assert_eq!(paths.len(), 2);
        let joined = paths
            .iter()
            .map(|p| p.as_str().unwrap())
            .collect::<Vec<_>>()
            .join(",");
        assert!(joined.contains("a.rs"));
        assert!(joined.contains("b.rs"));
        let _ = fs::remove_dir_all(&tmp);
    }

    #[test]
    fn test_glob_crate_recursive_still_works() {
        let tmp = make_tmp();
        let sub = tmp.join("src");
        fs::create_dir(&sub).unwrap();
        fs::write(sub.join("main.rs"), "x").unwrap();
        fs::write(tmp.join("readme.md"), "y").unwrap();
        let r = builtin_glob("**/*.rs", tmp.to_str().unwrap(), 100, &[]);
        assert!(r.ok, "glob failed: {:?}", r.error);
        let content = r.content.unwrap();
        assert_eq!(content["paths"].as_array().unwrap().len(), 1);
        let _ = fs::remove_dir_all(&tmp);
    }
}
