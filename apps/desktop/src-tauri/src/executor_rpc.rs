//! eaide-executor —— Rust 本地执行器的 JSON-RPC stdio 壳（执行过程可视化 · 阶段二）。
//!
//! 设计（与 `Claude_Code_式执行可视化设计` 计划第 3 节一致）：
//! - `builtin/`（path_sandbox + 9 工具）是 executor-core 本体，本模块是它的
//!   第二种壳：桌面壳内走 `builtin_*` Tauri Command，Agent 独立部署（无桌面壳
//!   注入）时由 Python `JsonRpcStdioClient` 拉起本二进制走 stdin/stdout。
//! - 方法名与 Tauri command 一一对应（`builtin_<tool>`），参数结构与
//!   `commands::builtin` 的 Args 结构体同源 —— Python 侧 `build_rust_args`
//!   产出的 dict 可无缝直发两种壳。
//! - stdout 只传 JSON-RPC 协议（一行一条）；任何诊断输出一律走 stderr，
//!   否则 Python 侧逐行 JSON 解析会炸（方案第 20.1 条纪律）。
//! - 沙箱不旁路：所有路径类方法都经 `builtin::path_sandbox::validate_path`，
//!   与桌面壳形态共用同一实现，消除「Python 兜底与 Rust 沙箱不一致」的安全缺口。

use crate::builtin::{self, ToolResult};
use crate::commands::builtin::{
    Base64Args, DeleteArgs, FindArgs, GlobArgs, HashArgs, MkdirArgs, MoveArgs, PathArgs,
    ShellArgs,
};
use serde_json::{json, Value};

/// JSON-RPC 2.0 parse error（行不是合法 JSON）。
const ERR_PARSE: i64 = -32700;
/// JSON-RPC 2.0 invalid request（缺 method 等）。
const ERR_INVALID_REQUEST: i64 = -32600;
/// JSON-RPC 2.0 method not found。
const ERR_METHOD_NOT_FOUND: i64 = -32601;
/// JSON-RPC 2.0 invalid params（参数反序列化失败）。
const ERR_INVALID_PARAMS: i64 = -32602;
/// 工具执行错误（含 panic 兜底）。
const ERR_EXECUTION: i64 = -32000;

/// 处理一行 JSON-RPC 请求，返回一行 JSON-RPC 响应（永不返 None）。
///
/// 暴露为 pub 供单元测试逐条验证（不必真起子进程）。
pub fn handle_line(line: &str) -> Value {
    let req: Value = match serde_json::from_str(line) {
        Ok(v) => v,
        Err(e) => return rpc_error(Value::Null, ERR_PARSE, &format!("parse error: {e}")),
    };
    let id = req.get("id").cloned().unwrap_or(Value::Null);
    let method = match req.get("method").and_then(|v| v.as_str()) {
        Some(m) => m,
        None => return rpc_error(id, ERR_INVALID_REQUEST, "missing method"),
    };
    let params = req.get("params").cloned().unwrap_or_else(|| json!({}));
    match dispatch(method, params) {
        Ok(result) => json!({"jsonrpc": "2.0", "id": id, "result": result}),
        Err((code, message)) => rpc_error(id, code, &message),
    }
}

/// stdio 主循环：逐行读 stdin → handle_line → 写 stdout（逐行刷新）。
///
/// 供 `src/bin/eaide_executor.rs` 调用；EOF 即退出（Python 端关管道 = 进程退出）。
pub fn run_stdio_loop() {
    use std::io::{self, BufRead, Write};
    let stdin = io::stdin();
    let stdout = io::stdout();
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(v) => v,
            Err(_) => break,
        };
        if line.trim().is_empty() {
            continue;
        }
        let resp = handle_line(&line);
        let mut out = stdout.lock();
        // 写失败 = 对端已关管道，直接退出
        if writeln!(out, "{}", resp).is_err() {
            break;
        }
        let _ = out.flush();
    }
}

fn rpc_error(id: Value, code: i64, message: &str) -> Value {
    json!({"jsonrpc": "2.0", "id": id, "error": {"code": code, "message": message}})
}

/// method → builtin 函数分派。参数结构与 `commands::builtin` Args 同源。
fn dispatch(method: &str, params: Value) -> Result<Value, (i64, String)> {
    // ping：Python 客户端启动后探活用（不做任何 I/O）
    if method == "ping" {
        return Ok(json!({"pong": true, "name": "eaide-executor", "version": env!("CARGO_PKG_VERSION")}));
    }
    let result = match method {
        "builtin_stat_file" => call::<PathArgs>(params, |a| {
            builtin::builtin_stat_file(&a.path, &a.allowed_roots)
        })?,
        "builtin_mkdir" => call::<MkdirArgs>(params, |a| {
            builtin::builtin_mkdir(&a.path, a.parents, &a.allowed_roots, a.require_hitl)
        })?,
        "builtin_delete_file" => call::<DeleteArgs>(params, |a| {
            builtin::builtin_delete_file(&a.path, a.recursive, &a.allowed_roots, a.require_hitl)
        })?,
        "builtin_move_file" => call::<MoveArgs>(params, |a| {
            builtin::builtin_move_file(&a.src, &a.dest, a.overwrite, &a.allowed_roots, a.require_hitl)
        })?,
        "builtin_find" => call::<FindArgs>(params, |a| {
            builtin::builtin_find(&a.path, &a.pattern, a.regex, a.max_results, &a.allowed_roots)
        })?,
        "builtin_glob" => call::<GlobArgs>(params, |a| {
            builtin::builtin_glob(&a.pattern, &a.root, a.max_results, &a.allowed_roots)
        })?,
        "builtin_hash" => call::<HashArgs>(params, |a| {
            builtin::builtin_hash(&a.path, &a.algorithm, &a.allowed_roots)
        })?,
        "builtin_base64" => call::<Base64Args>(params, |a| {
            builtin::builtin_base64(&a.data, &a.mode, &a.allowed_roots)
        })?,
        "builtin_shell" => call::<ShellArgs>(params, |a| {
            builtin::builtin_shell(
                &a.command,
                &a.argv,
                &a.cwd,
                &a.allowed_prefixes,
                a.timeout_sec,
                a.require_hitl,
                a.allow_nonzero_exit,
            )
        })?,
        _ => return Err((ERR_METHOD_NOT_FOUND, format!("unknown method: {method}"))),
    };
    serde_json::to_value(result).map_err(|e| (ERR_EXECUTION, format!("serialize error: {e}")))
}

/// 反序列化 params → 执行工具 → 兜底 panic（工具实现内部意外不能打爆整个循环）。
fn call<A: serde::de::DeserializeOwned>(
    params: Value,
    f: impl FnOnce(A) -> ToolResult,
) -> Result<ToolResult, (i64, String)> {
    let args: A = serde_json::from_value(params)
        .map_err(|e| (ERR_INVALID_PARAMS, format!("invalid params: {e}")))?;
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(move || f(args)));
    match result {
        Ok(r) => Ok(r),
        Err(_) => Err((ERR_EXECUTION, "tool panicked".to_string())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp_root() -> (tempfile_like::DirGuard, String) {
        let dir = std::env::temp_dir().join(format!(
            "eaide_executor_test_{}",
            uuid_like_suffix()
        ));
        std::fs::create_dir_all(&dir).unwrap();
        (tempfile_like::DirGuard(dir.clone()), dir.display().to_string())
    }

    // 极简临时目录守卫（不引 tempfile crate，保持依赖面最小）
    mod tempfile_like {
        pub struct DirGuard(pub std::path::PathBuf);
        impl Drop for DirGuard {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
    }

    fn uuid_like_suffix() -> String {
        use std::time::{SystemTime, UNIX_EPOCH};
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| format!("{}_{}", d.as_secs(), d.subsec_nanos()))
            .unwrap_or_else(|_| "0".into())
    }

    #[test]
    fn parse_error_returns_32700() {
        let resp = handle_line("not json");
        assert_eq!(resp["error"]["code"], ERR_PARSE);
    }

    #[test]
    fn unknown_method_returns_32601() {
        let resp = handle_line(r#"{"jsonrpc":"2.0","id":1,"method":"no_such"}"#);
        assert_eq!(resp["error"]["code"], ERR_METHOD_NOT_FOUND);
    }

    #[test]
    fn missing_method_returns_32600() {
        let resp = handle_line(r#"{"jsonrpc":"2.0","id":1}"#);
        assert_eq!(resp["error"]["code"], ERR_INVALID_REQUEST);
    }

    #[test]
    fn ping_works() {
        let resp = handle_line(r#"{"jsonrpc":"2.0","id":1,"method":"ping"}"#);
        assert_eq!(resp["result"]["pong"], true);
    }

    #[test]
    fn invalid_params_returns_32602() {
        let resp = handle_line(r#"{"jsonrpc":"2.0","id":1,"method":"builtin_find","params":{}}"#);
        assert_eq!(resp["error"]["code"], ERR_INVALID_PARAMS);
    }

    #[test]
    fn stat_file_inside_sandbox_ok() {
        let (_guard, root) = tmp_root();
        let file = format!("{root}/probe.txt");
        std::fs::write(&file, "hello").unwrap();
        let req = json!({
            "jsonrpc": "2.0", "id": 2, "method": "builtin_stat_file",
            "params": {"path": file, "allowed_roots": [root]}
        });
        let resp = handle_line(&req.to_string());
        assert_eq!(resp["result"]["ok"], true, "resp={resp}");
    }

    #[test]
    fn path_escape_is_blocked_by_sandbox() {
        let (_guard, root) = tmp_root();
        // ../../ 跳出 allowed_roots —— 沙箱必须拦截（与桌面壳形态同一实现）
        let req = json!({
            "jsonrpc": "2.0", "id": 3, "method": "builtin_stat_file",
            "params": {"path": format!("{root}/../.."), "allowed_roots": [root]}
        });
        let resp = handle_line(&req.to_string());
        assert_eq!(resp["result"]["ok"], false, "resp={resp}");
    }

    #[test]
    fn shell_require_hitl_returns_needs_hitl() {
        let req = json!({
            "jsonrpc": "2.0", "id": 4, "method": "builtin_shell",
            "params": {"command": "echo hi", "require_hitl": true}
        });
        let resp = handle_line(&req.to_string());
        // require_hitl=true → 工具不执行，返 needs_hitl（审批闸门不旁路）
        assert_eq!(resp["result"]["needs_hitl"], true, "resp={resp}");
    }
}
