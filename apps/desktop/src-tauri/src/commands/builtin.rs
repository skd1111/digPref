//! Phase 1B V2 · 原生工具层 Tauri Command 暴露。
//!
//! 9 工具（V1.5 六安全工具 + V2 三高危工具）通过 Tauri Command 暴露给 Webview。
//! Python dispatcher 通过 tauri::async_runtime 桥接到这些 Command。
//!
//! V1.5 设计：
//!   - 所有 Command 都接收 `allowed_roots: Vec<String>` 参数（默认空 = 不限制）
//!   - 所有 Command 返 ToolResult 结构（ok / content / error / meta / risk_level）
//!   - HITL 标记走 needs_hitl 字段；dispatcher 据此决定是否触发审批
//!
//! V2 增量：
//!   - delete_file / move_file / shell 三个高危工具真实实现
//!   - require_hitl 参数：dispatcher 审批通过后传 false 放行执行

use crate::builtin;
use crate::error::AppResult;


/// 通用参数：path + allowed_roots（用于 stat_file / mkdir / hash / base64）
#[derive(serde::Deserialize)]
pub struct PathArgs {
    pub path: String,
    #[serde(default)]
    pub allowed_roots: Vec<String>,
    #[serde(default, rename = "_require_hitl_unused")]
    pub _require_hitl_unused: bool,
}


/// mkdir 专属参数（含 parents 开关）
#[derive(serde::Deserialize)]
pub struct MkdirArgs {
    pub path: String,
    #[serde(default = "default_parents")]
    pub parents: bool,
    #[serde(default)]
    pub allowed_roots: Vec<String>,
    #[serde(default)]
    pub require_hitl: bool,
}

fn default_parents() -> bool { true }


/// find/glob 专属参数
#[derive(serde::Deserialize)]
pub struct FindArgs {
    pub path: String,
    pub pattern: String,
    #[serde(default)]
    pub regex: bool,
    #[serde(default)]
    pub max_results: usize,
    #[serde(default)]
    pub allowed_roots: Vec<String>,
}


#[derive(serde::Deserialize)]
pub struct GlobArgs {
    pub pattern: String,
    /// 基准目录。别名 `base_dir` —— 工具 schema 对模型声明的是 base_dir，
    /// 此前只接 root 且无默认值，模型按 schema 传参时拿到空串 →
    /// validate_path("") 报 "empty path"，glob 永远不可用（BUGFIX #166）。
    #[serde(default = "default_glob_root", alias = "base_dir")]
    pub root: String,
    #[serde(default)]
    pub max_results: usize,
    #[serde(default)]
    pub allowed_roots: Vec<String>,
}

fn default_glob_root() -> String {
    ".".to_string()
}


#[derive(serde::Deserialize)]
pub struct HashArgs {
    pub path: String,
    pub algorithm: String,
    #[serde(default)]
    pub allowed_roots: Vec<String>,
}


#[derive(serde::Deserialize)]
pub struct Base64Args {
    pub data: String,
    pub mode: String,
    #[serde(default)]
    pub allowed_roots: Vec<String>,
}


/// delete_file 专属参数
#[derive(serde::Deserialize)]
pub struct DeleteArgs {
    pub path: String,
    #[serde(default)]
    pub recursive: bool,
    #[serde(default)]
    pub allowed_roots: Vec<String>,
    #[serde(default)]
    pub require_hitl: bool,
}


/// move_file 专属参数
#[derive(serde::Deserialize)]
pub struct MoveArgs {
    pub src: String,
    pub dest: String,
    #[serde(default)]
    pub overwrite: bool,
    #[serde(default)]
    pub allowed_roots: Vec<String>,
    #[serde(default)]
    pub require_hitl: bool,
}


/// shell 专属参数
#[derive(serde::Deserialize)]
pub struct ShellArgs {
    #[serde(default)]
    pub command: String,
    /// 参数数组形式（首选，BUGFIX #166）：直接执行，绕过 shell 引号规则。
    /// 与 command 二选一，argv 非空时优先。
    #[serde(default)]
    pub argv: Vec<String>,
    /// 工作目录。`cd` 不跨调用生效（每次调用是新进程），切目录必须用它。
    #[serde(default)]
    pub cwd: String,
    #[serde(default)]
    pub allowed_prefixes: Vec<String>,
    #[serde(default = "default_timeout_sec")]
    pub timeout_sec: u64,
    #[serde(default)]
    pub require_hitl: bool,
    /// 非零退出码是否算成功（根治 BUGFIX #165）。默认 false。
    /// 置 true 用于「非零退出是正常语义」的命令：findstr / grep 无匹配返 1、
    /// diff 有差异返 1 等 —— 调用方明确知道自己在做什么时才用。
    #[serde(default)]
    pub allow_nonzero_exit: bool,
}

fn default_timeout_sec() -> u64 { 30 }


// ---- 9 Tauri Commands ----------------------------------------------------

#[tauri::command]
pub async fn builtin_stat_file(args: PathArgs) -> AppResult<builtin::ToolResult> {
    Ok(builtin::builtin_stat_file(&args.path, &args.allowed_roots))
}

#[tauri::command]
pub async fn builtin_mkdir(args: MkdirArgs) -> AppResult<builtin::ToolResult> {
    Ok(builtin::builtin_mkdir(
        &args.path,
        args.parents,
        &args.allowed_roots,
        args.require_hitl,
    ))
}

#[tauri::command]
pub async fn builtin_find(args: FindArgs) -> AppResult<builtin::ToolResult> {
    Ok(builtin::builtin_find(
        &args.path,
        &args.pattern,
        args.regex,
        args.max_results,
        &args.allowed_roots,
    ))
}

#[tauri::command]
pub async fn builtin_glob(args: GlobArgs) -> AppResult<builtin::ToolResult> {
    Ok(builtin::builtin_glob(
        &args.pattern,
        &args.root,
        args.max_results,
        &args.allowed_roots,
    ))
}

#[tauri::command]
pub async fn builtin_hash(args: HashArgs) -> AppResult<builtin::ToolResult> {
    Ok(builtin::builtin_hash(&args.path, &args.algorithm, &args.allowed_roots))
}

#[tauri::command]
pub async fn builtin_base64(args: Base64Args) -> AppResult<builtin::ToolResult> {
    Ok(builtin::builtin_base64(&args.data, &args.mode, &args.allowed_roots))
}

#[tauri::command]
pub async fn builtin_delete_file(args: DeleteArgs) -> AppResult<builtin::ToolResult> {
    Ok(builtin::builtin_delete_file(
        &args.path,
        args.recursive,
        &args.allowed_roots,
        args.require_hitl,
    ))
}

#[tauri::command]
pub async fn builtin_move_file(args: MoveArgs) -> AppResult<builtin::ToolResult> {
    Ok(builtin::builtin_move_file(
        &args.src,
        &args.dest,
        args.overwrite,
        &args.allowed_roots,
        args.require_hitl,
    ))
}

#[tauri::command]
pub async fn builtin_shell(args: ShellArgs) -> AppResult<builtin::ToolResult> {
    Ok(builtin::builtin_shell(
        &args.command,
        &args.argv,
        &args.cwd,
        &args.allowed_prefixes,
        args.timeout_sec,
        args.require_hitl,
        args.allow_nonzero_exit,
    ))
}


/// 健康检查 command：返回当前实现的工具列表（V2 = 9/9）
#[tauri::command]
pub async fn builtin_status() -> AppResult<serde_json::Value> {
    let implemented: Vec<&str> = builtin::RUST_TOOL_NAMES
        .iter()
        .filter(|n| builtin::is_implemented(n))
        .copied()
        .collect();
    let pending: Vec<&str> = builtin::RUST_TOOL_NAMES
        .iter()
        .filter(|n| !builtin::is_implemented(n))
        .copied()
        .collect();
    Ok(serde_json::json!({
        "version": "2.0",
        "implemented": implemented,
        "pending": pending,
        "total_rust_tools": builtin::RUST_TOOL_NAMES.len(),
    }))
}


// ---- 单元测试（V1.5 覆盖）-----

#[cfg(test)]
mod tests {
    // Tauri Commands 内部都直接调用 builtin::* 函数；上层测试在 mod.rs 完成
    // 这里仅做 schema 序列化测试

    #[test]
    fn test_path_args_deserialize_minimal() {
        let json = r#"{"path": "/tmp/test.txt"}"#;
        let args: super::PathArgs = serde_json::from_str(json).unwrap();
        assert_eq!(args.path, "/tmp/test.txt");
        assert_eq!(args.allowed_roots.len(), 0);
        assert!(!args._require_hitl_unused);
    }

    #[test]
    fn test_mkdir_args_defaults() {
        let json = r#"{"path": "/tmp/new"}"#;
        let args: super::MkdirArgs = serde_json::from_str(json).unwrap();
        assert!(args.parents);  // default
        assert!(!args.require_hitl);
    }

    #[test]
    fn test_find_args_full() {
        let json = r#"{"path": "/tmp", "pattern": "*.rs", "regex": true, "max_results": 50}"#;
        let args: super::FindArgs = serde_json::from_str(json).unwrap();
        assert!(args.regex);
        assert_eq!(args.max_results, 50);
    }

    #[test]
    fn test_tool_result_serialize() {
        use crate::builtin::ToolResult;
        let r = ToolResult::ok(serde_json::json!({"size": 100}), "read");
        let s = serde_json::to_string(&r).unwrap();
        assert!(s.contains("\"ok\":true"));
        assert!(s.contains("\"size\":100"));
        assert!(s.contains("\"risk_level\":\"read\""));
    }

    #[test]
    fn test_tool_result_fail() {
        use crate::builtin::ToolResult;
        let r = ToolResult::fail("path not found", "read");
        let s = serde_json::to_string(&r).unwrap();
        assert!(s.contains("\"ok\":false"));
        assert!(s.contains("\"error\":\"path not found\""));
    }

    #[test]
    fn test_delete_args_defaults() {
        let json = r#"{"path": "/tmp/a.txt"}"#;
        let args: super::DeleteArgs = serde_json::from_str(json).unwrap();
        assert!(!args.recursive);
        assert!(!args.require_hitl);
        assert_eq!(args.allowed_roots.len(), 0);
    }

    #[test]
    fn test_move_args_full() {
        let json = r#"{"src": "/tmp/a", "dest": "/tmp/b", "overwrite": true, "require_hitl": true}"#;
        let args: super::MoveArgs = serde_json::from_str(json).unwrap();
        assert!(args.overwrite);
        assert!(args.require_hitl);
    }

    #[test]
    fn test_shell_args_default_timeout() {
        let json = r#"{"command": "echo hi"}"#;
        let args: super::ShellArgs = serde_json::from_str(json).unwrap();
        assert_eq!(args.timeout_sec, 30);
        assert_eq!(args.allowed_prefixes.len(), 0);
        assert!(!args.require_hitl);
    }
}
