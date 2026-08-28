//! eaide-executor —— Rust 本地执行器独立二进制入口（执行过程可视化 · 阶段二）。
//!
//! Agent 独立部署（无桌面壳注入）时，Python `JsonRpcStdioClient` 拉起本进程：
//! - stdin 逐行收 JSON-RPC 2.0 请求（方法名与 `builtin_*` Tauri command 一致）；
//! - stdout 逐行回 JSON-RPC 响应（协议纪律：绝不混入日志，日志一律 stderr）；
//! - 工具实现复用 `eaide_desktop_lib::builtin`（与桌面壳同一份沙箱实现）。
//!
//! 用法：
//! ```bash
//! echo '{"jsonrpc":"2.0","id":1,"method":"ping"}' | eaide-executor
//! ```

fn main() {
    // stderr 启动横幅（不污染 stdout 协议流）：方便日志定位子进程已拉起
    eprintln!(
        "eaide-executor v{} ready (JSON-RPC 2.0 over stdio)",
        env!("CARGO_PKG_VERSION")
    );
    eaide_desktop_lib::executor_rpc::run_stdio_loop();
}
