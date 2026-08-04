# apps/desktop/src-tauri — Rust backend

> Tauri 2.0 desktop shell. 关键职责：
> 1. **凭证保险箱** — `credentials/` 跨平台封装（macOS Keychain / Windows Credential Manager / Linux Secret Service）
> 2. **SSE 桥** — `stream/sse_bridge.rs` 把 Python Agent 的 SSE 流翻译成 Tauri Event 推给 Webview
> 3. **本地审计 SQLite** — `audit/store.rs` 与 Python Agent 共享同一份 schema
> 4. **MCP 进程托管（可选）** — `mcp_client/` 一般由 Agent 端托管

## 模块索引

| 模块 | 职责 |
|------|------|
| `main.rs` / `lib.rs` | Tauri 启动入口 |
| `commands/` | IPC 命令实现（agent / credentials / audit / asset / shell） |
| `credentials/` | OS 凭证保险箱 |
| `stream/` | SSE ↔ Tauri Event 桥 |
| `audit/` | 本地 SQLite 审计日志 |
| `mcp_client/` | （可选）本地 MCP stdio 进程管理 |
| `state.rs` | 全局 `AppState` |
| `config.rs` | 配置加载 |
| `error.rs` | 统一错误类型 |