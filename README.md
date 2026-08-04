# EAIDE — Enterprise AI IDE

> 企业内网本地化 AI IDE Agent：让运维 / 开发 / 业务人员通过**自然语言**，在本地 IDE 形态的界面中，安全地查询、操作、跨系统编排企业生产环境中的异构系统（数据库、REST API、SSH 服务器、老旧 Web 系统等）。
>
> 核心特性：**数据不出域**（敏感 LLM 任务强制走本地 Ollama）、**写操作强制 HITL 人工审批**、**全链路审计可追溯**。

---

## 1. 技术栈

| 层 | 技术 |
| --- | --- |
| 桌面端 | Tauri 2.0 (Rust) + React 18 + TypeScript + Tailwind CSS |
| 控制层 | Python 3.12 / FastAPI / LangGraph |
| 执行层 | MCP Servers（Python，stdio / HTTP） |
| 协议 | `packages/shared-protocol`（TS ⇄ Python 双侧镜像） |
| 存储 | SQLite（audit / sessions / router 等多库物理隔离）+ OS Keychain 凭证 |
| 包管理 | pnpm workspaces（JS）+ uv（Python）+ Cargo（Rust） |

## 2. 架构分层

```
┌──────────────────────────────────────────────────────────────────┐
│  表现层  Tauri 2.0 (Rust) + React + TS + Tailwind                │
│   ┌──────────┬──────────────────────┬──────────────────────┐     │
│   │ 左:资产树 │   中:对话流+代码块   │ 右:思维链/执行链路    │     │
│   │ /业务功能 │   Monaco Editor      │ 文件操作追踪 + Diff   │     │
│   └──────────┴──────────────────────┴──────────────────────┘     │
│   内嵌 Monaco Editor / Xterm.js / SSE 订阅 / 实时预览窗口         │
└──────────────────────────────────────────────────────────────────┘
                          ▲ SSE / HTTP
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  控制层  Python / FastAPI / LangGraph                            │
│   - 双框架路由: ModeRouter（Coding Agent ⇄ Work Agent）          │
│   - 意图识别 → 工具路由 → 写操作检测 → HITL → 执行 → Auto-Repair │
│   - LMRouter: 五维评估调度 + 多级降级 + 熔断 + 双层缓存          │
│   - 多智能体 Orchestrator: 派生子 Agent 并行 + HITL 反向 interrupt│
│   - 本地小模型 / RAG 知识库 / MACC 上下文压缩 / DSpark 推测解码   │
└──────────────────────────────────────────────────────────────────┘
                          ▲ MCP (stdio / HTTP) + 内置工具
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  执行层                                                           │
│   - 内置工具 19 个 (进程内 <1ms, 路径沙箱 7 项校验)               │
│   - mcp-server-database  SQL 拦截 / 语法校验 / 结果截断          │
│   - mcp-server-rest      方法白名单 / 请求体限制                  │
│   - mcp-server-ssh       命令黑名单 + 主机白名单                  │
│   - mcp-server-rpa       Playwright + 域名白名单                 │
└──────────────────────────────────────────────────────────────────┘
                          ▲
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  横切层                                                           │
│   - 凭证保险箱: OS Keychain / Windows Credential Manager（零落盘）│
│   - 审计 SQLite: 全链路可追溯 + 哈希签名链                        │
│   - 多环境治理: env preset + Keyring 占位符 + 加密导入导出         │
└──────────────────────────────────────────────────────────────────┘
```

## 3. 功能实现状态

### ✅ 已实现

| 模块 | 说明 |
| --- | --- |
| 基础架构 | Tauri 桌面壳 / 四象限 IDE 布局 / LangGraph 状态机 / HITL 审批闸门 / SSE 事件桥 / SQLite 审计 |
| 双框架智能体（Phase 18） | ModeRouter 智能路由 Coding / Work 双模式 + 任务分解 + Auto-Repair 自动修复 |
| 内置工具层 | 19 个进程内工具（文件读写/搜索/计算器/正则/shell 等）+ 路径沙箱 + HITL 前置闸门 |
| 智能 LLM 路由 | 五维评估调度 / 多级降级 / 熔断器 / 双层缓存 / 预算控制 / 关键任务双模型并行+裁判 |
| Skill / MCP 生态 | Skill YAML + MCP JSON Schema / 关键词路由 / 热加载 / 多项目隔离 |
| 多环境治理 | 4 env preset / Keyring 占位符 / PBKDF2+Fernet 加密导入导出 / 环境徽章 |
| 代码导航 | Tree-sitter AST 索引 + SQLite 符号库 + Monaco 跳转 + AI 语义推断 |
| 业务功能点导航 | 代码→业务功能点抽象 + YAML 热加载（运营专家模式专属） |
| 大文件日志查看 | Rust 字节偏移索引 + 流式读取 + 进程内搜索 + GBK 编码 + AI 日志分析 |
| 会话管理 | 会话生命周期 / Checkpoint / FTS5 全文搜索 / 分支 / 共享 / .eas 加密导出 |
| MACC 上下文压缩 | 三层自适应压缩（工作记忆 / 情景记忆事件图谱 / 语义规则蒸馏） |
| 多智能体调度 | Orchestrator 派生子 Agent / Worker Pool / DLQ / 限流 / 派生树硬上限 |
| DSpark 推测解码 | Qwen2.5 草稿模型 + llama.cpp 推测解码 + 场景化策略路由 |
| 数据专家模式（V1） | NL2SQL + 虚拟滚动 DataGrid + ECharts 可视化 + 只读铁律 |
| 思维链可视化（V1） | 中文思维链时间线 + 文件操作追踪 + hover Diff 预览 |

### 🟡 部分实现

| 模块 | 现状 | 未实现部分 |
| --- | --- | --- |
| 本地小模型 + 知识库 | 后端引擎已交付（RAG 检索 + 分块 + 主图接入） | 前端知识库管理界面、Sidecar 生命周期管理 |
| 审核专家模式（金融审计） | V0 后端骨架（审批队列 + 签名链 + 合规规则） | 前端 AuditDashboard、Monaco Diff 审核、MFA、双人复核 |
| 本地图像处理 | V0 后端骨架（6 端点 + 任务表） | ONNX 超分 / PaddleOCR / 倾斜校正真实集成、前端 UI |
| 前端实时预览 | V0.1（Vite 管理器 + 独立预览窗口 + 设备模式） | 真实 Vite 端到端 HMR 实测收尾 |
| 大文件日志 V1.5 | 核心读写搜索已交付 | tail -f 实时监控、ripgrep 集成、AI 分析 UI |

### ⚪ 未实现（规划中）

| 模块 | 说明 |
| --- | --- |
| 类 FinalShell 远程管理（2B） | SSH PTY + SFTP 双栏 + 资产树联动 |
| 编译打包引擎（3A） | 工具链探测 + 异步构建 + SSE 实时日志 |
| 部署流水线（3B） | 状态机部署 + 自动回滚 + 零停机 swap |
| Arthas JVM 热更（3C） | AI 热修复 + 字节码结构校验 |
| 部署 UI（3D） | 流水线可视化 + 实时日志 |
| 多人协同审批（8） | 跨终端审批路由 + OA/IM 集成（需独立 Server） |
| 任务级协作（9） | 上下文锚点 + 行级评论 + @ 提醒 |
| 统一身份认证 IAM（10） | OIDC/LDAP/企微 + RBAC/ABAC + 数字签名 + MFA |
| 离线授权系统（11） | 机器指纹 + License RSA 签名 + 试用管理 |

## 4. 安全红线（绝对不可妥协）

| 红线 | 实现位置 |
| --- | --- |
| HITL 强制审批（写操作不可绕过） | `services/agent/src/agent/graph/nodes/hitl_gate.py` + 前端 ApprovalCard |
| 敏感任务强制本地模型 | `services/agent/src/agent/llm/router.py` 的 `_LOCAL_ONLY_TASKS` |
| 凭证零落盘 | `apps/desktop/src-tauri/src/credentials/{windows,macos,linux}.rs` |
| 全链路审计 | `services/agent/src/agent/audit/store.py`（Python + Rust 双 schema 镜像） |
| 路径沙箱 | `path_sandbox` 7 项校验（Python / Rust 双侧实现） |
| MCP 层安全 | 各 `services/mcp-servers/*/safety/` 子模块（SQL 拦截 / 白名单 / 黑名单） |
| SSE 事件契约 | `graph/stream.py` + `sse_bridge.rs` + `ipc/events.ts` 三处强制同步 |

## 5. 目录结构

```
.
├── apps/desktop/              # 【表现层】Tauri 2.0 + React
│   ├── src/                   # React 前端（layouts/components/store/ipc/streams）
│   └── src-tauri/             # Rust 后端（commands/credentials/stream/audit/logviewer…）
├── services/
│   ├── agent/                 # 【控制层】Python Agent（FastAPI + LangGraph，29 个模块）
│   │   └── src/agent/
│   │       ├── graph/         # LangGraph 状态机（intent/planner/tool_runner/hitl_gate/repair…）
│   │       ├── dual/ coding/  # Phase 18 双框架（Coding Agent / Work Agent）
│   │       ├── llm/           # LMRouter 智能路由 + _LOCAL_ONLY_TASKS
│   │       ├── orchestrator/  # 多智能体调度
│   │       ├── sessions/      # 会话管理 + MACC 压缩
│   │       ├── knowledge/     # RAG 知识库
│   │       ├── builtin/       # 内置工具层
│   │       ├── dataexpert/    # 数据专家模式（NL2SQL + DataGrid）
│   │       ├── trace/         # 思维链收集与文件操作追踪
│   │       └── …              # codenav/biznav/skills/preview/audit/safety 等
│   └── mcp-servers/           # 【执行层】MCP Server 矩阵（database/rest/ssh/rpa）
├── packages/shared-protocol/  # 跨语言协议包（TS + Python 唯一事实来源）
├── config/driver/             # 离线数据库驱动 wheel（不入库，构建时本地放置）
├── infra/                     # Docker / 脚本 / 配置模板
├── Makefile                   # 统一命令入口
├── build-all.bat              # Windows 全量构建脚本
└── .github/workflows/         # ci / release / audit-export
```

## 6. 快速开始

前置工具：`uv`、`pnpm`、`cargo`、`tauri-cli`、Python 3.12+、Node 20+。

```bash
# 检查工具链
make bootstrap

# 安装全部依赖（uv sync + pnpm install + cargo fetch）
make install

# 本机开发：Agent（FastAPI :8765）+ 桌面端
make dev-agent
make dev-desktop

# 测试 / 检查 / 格式化（三语言栈）
make test
make lint
make fmt

# 构建安装包（Windows 请用 build-all.bat，会加载 MSVC 环境）
make build
```

> ⚠️ 数据库离线驱动（`config/driver/*.whl`，约 30MB）**不在仓库中**。完整构建前需自行将 wheel 文件放到 `config/driver/` 目录。

## 7. 开发约定

- **Python**：`ruff` + `mypy --strict`；snake_case；行宽 100；pytest（`asyncio_mode = "auto"`）
- **TypeScript/React**：`prettier` + `eslint`（零告警）；camelCase；vitest
- **Rust**：`rustfmt` + `cargo clippy --all-targets -- -D warnings`；行宽 120；模块测试写在 `tests.rs`
- **分支模型**：主干开发，`feat/*` 短分支 → PR 合入 `main`（2 reviewer）
- **协议同步**：线协议类型改动必须同时更新 `shared-protocol` 的 TS 与 Python 两侧
- **SSE 事件**：新增事件名必须在 `graph/stream.py`、`stream/sse_bridge.rs`、`ipc/events.ts` 三处同步

## 8. License

未开源许可 —— 保留所有权利（企业内部项目）。
