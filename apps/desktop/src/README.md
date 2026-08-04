# apps/desktop/src — React/TS frontend

> 内嵌于 Tauri Webview 的前端应用。**不直接连接后端**，所有跨进程交互通过 `src/ipc/` 走 Tauri Command/Event。

## 关键约定

1. **不直连 SSE** — 所有 Agent 流通过 Rust `sse_bridge` 转发，再以 Tauri Event 形式投递到本进程。
2. **HITL 必走 ApprovalCard** — 见 `components/chat/ApprovalCard.tsx`。
3. **凭证零前端** — 前端绝不接触 DB 密码 / Token；只调用 `credential_get` 让 Rust 去 Keychain 取。
4. **Monaco 用于展示，Xterm 用于日志** — 见 `components/editor` 与 `components/terminal`。

## 目录索引

| 子目录 | 作用 |
|--------|------|
| `layouts/` | 四象限 IDE 布局 |
| `components/chat/` | 对话流：消息、输入、代码块、审批卡 |
| `components/editor/` | Monaco 包装、SQL 补全注册 |
| `components/terminal/` | Xterm 日志查看器 |
| `components/asset-tree/` | 系统资产树 |
| `components/trace/` | 执行链路 Trace 时间线 |
| `ipc/` | Tauri invoke/listen 类型化包装 |
| `streams/` | Agent SSE 事件订阅 |
| `store/` | Zustand 全局状态 |
| `hooks/` | React hooks 封装 |
| `lib/` | 纯 JS 工具（可单测） |
| `styles/` | Tailwind globals |
| `types/` | 跨语言类型再导出 |
| `views/` | 路由级页面 |