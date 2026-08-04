//! SSE 桥 —— 长连接消费者，挂在 Python Agent 的
//! `/chat/{run_id}/stream` SSE 端点上，把每个事件
//! 重新发成一条 Tauri Event（`agent://*` 通道）。
//!
//! Webview 无法跨过 Tauri 的 CSP 直接连 127.0.0.1:8765 拉 EventSource，
//! 所以所有流数据都通过这个 Rust 任务代理。

mod sse_bridge;

pub use sse_bridge::SseBridge;
