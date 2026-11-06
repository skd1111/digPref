# apps/desktop/src — React/TS frontend

> 内嵌于 Tauri Webview 的前端应用。**不直接连接后端**，所有跨进程交互通过 `src/ipc/` 走 Tauri Command/Event。

## 关键约定

1. **不直连 SSE** — 所有 Agent 流通过 Rust `sse_bridge` 转发，再以 Tauri Event 形式投递到本进程。
2. **HITL 必走 ApprovalCard** — 见 `components/chat/ApprovalCard.tsx`。
3. **凭证零前端** — 前端绝不接触 DB 密码 / Token；只调用 `credential_get` 让 Rust 去 Keychain 取。
4. **Monaco 用于展示，Xterm 用于日志** — 见 `components/editor` 与 `components/terminal`。
5. **按钮必须用 `.btn` 规范** — 统一在 `styles/globals.css` 的 `@layer components` 定义：`btn` 基类 + 变体 `btn-primary` / `btn-secondary` / `btn-ghost` / `btn-danger` / `btn-success` + 尺寸 `btn-xs` / `btn-sm` / `btn-md` / `btn-icon`。**禁止**再给按钮写 inline `backgroundColor` / `border` / `color`（inline 会覆盖类样式，风格重新分裂）；确需保留语义色时用 `className="btn btn-md"` + 仅保留 `backgroundColor` 内联，并继承基类的尺寸/圆角/过渡。
6. **图标用 `components/ui/Icons.tsx` 的单色 SVG** — 自绘的零依赖线性图标库（90+ 图标，24×24 viewBox、stroke 1.75、`stroke="currentColor"`）。**禁止**再往界面里加 unicode 占位符（`⎇ ⌕ ▶ ⊞ ◧ ⇆`）或 emoji（`💬 🗂 📋 📖 👥 🧠`）充当图标——它们与 SVG 混排时视觉重量不一致（emoji 偏敦实、线条符号偏纤细），是“图标看起来不统一”的根因。新增图标在该文件内按现有风格追加（`<svg>` + `BASE` 公共属性），由外层容器的 `color` 决定颜色；语义由外层 `title` / `aria-label` 承担，SVG 本身 `aria-hidden`。
7. **图标位 vs 功能符号** — 只有「图标位」（按钮 / tab / 树节点 / 面板标题 / 空态大图标）用 SVG；下列**保留原样**：
   - 功能性 emoji：协作面板的表情回复（👍👎✅❌👀）
   - 语义色点：风险等级 🔴🟡🟢、状态 ● ○、熔断 ◐
   - 协议/文案标记：`✓ 已批准` / `✗ 错误` / `📄 filename.ext`（`thinking/fileRef.ts` 的正则依赖它）、chip 里的类型标识（🏷️ 项目画像 / 📎 附加文件）
   - 平台 logo：ShareDialog 的企微/钉钉/飞书 emoji（数据字典驱动）
   - 技术限制：`<select>` 的 `<option>` 与 Monaco 右键菜单 `label` 只能放纯文本
8. **类型→图标的映射表放组件层，不放 types 层** — `types/` 保持纯数据不引 React。参见 `components/asset-tree/assetTypeIcons.ts`（`ASSET_TYPE_ICONS`）与 `components/collab/anchorTypeIcons.ts`（`ANCHOR_TYPE_ICONS`）；同一份映射多处使用时提取成共享常量，别各写一份。
9. **JSX 里不能直接用成员表达式当组件名** — `<meta.icon />` 非法，必须先 `const Icon = meta.icon;` 再 `<Icon />`。

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