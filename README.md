# Enterprise Local AI IDE Agent

> 让运维 / 开发 / 业务人员通过**自然语言**，在本地 IDE 形态的界面中，安全地查询、操作、跨系统编排企业生产环境中的异构系统（数据库、REST API、SSH 服务器、老旧 Web 系统等）。

---

## 1. 架构分层（Single Source of Truth）

```
┌──────────────────────────────────────────────────────────────────┐
│  表现层  Tauri 2.0 (Rust) + React + TS + Tailwind                │
│   ┌──────────┬──────────────────────┬──────────────────────┐     │
│   │ 左:资产树│   中:对话流+代码块   │  右:执行链路 Trace  │     │
│   │          ├──────────────────────┤                      │     │
│   │          │   底:Xterm 日志      │                      │     │
│   └──────────┴──────────────────────┴──────────────────────┘     │
│   内嵌 Monaco Editor / Xterm.js / SSE 订阅                       │
└──────────────────────────────────────────────────────────────────┘
                          ▲ SSE / WebSocket
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  控制层  Python 3.10+ / FastAPI / LangGraph                      │
│   - 意图识别 -> 工具路由 -> 写操作检测 -> HITL -> 执行 -> 修复    │
│   - 模型路由: Ollama (脱敏) | 私有化 LLM (复杂规划)              │
│   - 通过 MCP Client 连接多个 MCP Server                          │
└──────────────────────────────────────────────────────────────────┘
                          ▲ MCP (stdio / HTTP)
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  执行层  MCP Servers (Python)                                    │
│   - mcp-server-database  SQL 拦截 / 语法校验 / 结果截断          │
│   - mcp-server-rest      OpenAPI -> Tool, 白名单方法            │
│   - mcp-server-ssh       命令黑名单 + 主机白名单                 │
│   - mcp-server-rpa       Playwright + 域名白名单                 │
└──────────────────────────────────────────────────────────────────┘
                          ▲
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  横切层                                                            │
│   - 凭证保险箱: macOS Keychain / Windows Credential Manager      │
│   - 审计 SQLite: 全链路可追溯                                    │
│   - 配置中心: agent.yaml / mcp.yaml                              │
└──────────────────────────────────────────────────────────────────┘
```

## 2. 安全红线（绝对不可妥协）


| 红线             | 实现位置                                                                                |
| ---------------- | --------------------------------------------------------------------------------------- |
| HITL 强制审批    | `services/agent/src/agent/graph/nodes/hitl_gate.py` + 前端 `ApprovalCard`               |
| 凭证零落盘       | `apps/desktop/src-tauri/src/credentials/{macos,windows,linux}.rs`                       |
| 全链路审计       | `services/agent/src/agent/audit/store.rs` + `apps/desktop/src-tauri/src/audit/store.rs` |
| MCP / 网络层沙箱 | 各`services/mcp-servers/*/safety/` 子模块                                               |

## 3. Monorepo 目录结构

```
.
├── README.md                        # 本文件
├── Makefile                         # 一键启动 / 测试 / 构建
├── docker-compose.dev.yml           # 本地起 Agent + 各 MCP Server
├── pyproject.toml                   # Python workspace (uv)
├── package.json                     # JS workspace (pnpm)
├── .gitignore
├── .editorconfig
│
├── apps/
│   └── desktop/                     # 【表现层】Tauri 2.0 + React
│       ├── package.json
│       ├── tsconfig.json
│       ├── vite.config.ts
│       ├── tailwind.config.ts
│       ├── index.html
│       ├── src/                     # React 前端源码
│       │   ├── main.tsx
│       │   ├── App.tsx
│       │   ├── layouts/             # 四象限布局
│       │   ├── components/          # chat / editor / terminal / asset-tree / trace
│       │   ├── ipc/                 # Tauri IPC 桥
│       │   ├── streams/             # SSE 订阅
│       │   ├── store/               # Zustand
│       │   ├── hooks/
│       │   ├── lib/
│       │   ├── styles/
│       │   └── types/
│       ├── src-tauri/               # Rust 后端
│       │   ├── Cargo.toml
│       │   ├── tauri.conf.json
│       │   ├── capabilities/
│       │   └── src/
│       │       ├── main.rs / lib.rs
│       │       ├── commands/        # IPC commands
│       │       ├── credentials/     # 跨平台 OS 凭证保险箱
│       │       ├── stream/          # 转发 SSE 到 Webview
│       │       ├── audit/           # 本地 SQLite 审计
│       │       └── mcp_client/      # 可选:本地 MCP 进程管理
│       └── tests/e2e/
│
├── services/
│   ├── agent/                       # 【控制层】Python Agent
│   │   ├── pyproject.toml
│   │   └── src/agent/
│   │       ├── main.py              # FastAPI 入口
│   │       ├── api/                 # /chat /approval /ws /health
│   │       ├── graph/               # LangGraph 状态机
│   │       │   ├── state.py
│   │       │   ├── nodes/
│   │       │   │   ├── intent.py / planner.py
│   │       │   │   ├── tool_runner.py
│   │       │   │   ├── hitl_gate.py
│   │       │   │   ├── repair.py
│   │       │   │   └── responder.py
│   │       │   ├── edges.py
│   │       │   └── compile.py
│   │       ├── llm/                 # Ollama + 私有化 LLM 路由
│   │       ├── mcp/                 # 多 MCP Server 客户端
│   │       ├── tools/               # 内置工具 (含 Auto-Repair)
│   │       ├── safety/              # 写操作检测 / 策略
│   │       ├── audit/               # SQLite 审计
│   │       ├── credentials/         # 与 Rust 凭证保险箱通信
│   │       └── observability/       # Trace / LangSmith
│   │
│   └── mcp-servers/                 # 【执行层】MCP Server 矩阵
│       ├── mcp-server-database/     # SQL: 语法校验 + 高危拦截 + 截断
│       ├── mcp-server-rest/         # HTTP: 白名单 + OpenAPI -> Tool
│       ├── mcp-server-ssh/          # Linux: 命令黑名单 + 主机白名单
│       └── mcp-server-rpa/          # Playwright: 域名白名单
│
├── packages/
│   └── shared-protocol/             # 跨语言协议包 (TS + Python)
│       ├── src/ts/                  # 前端类型
│       └── src/py/                  # 后端 Pydantic
│
├── infra/
│   ├── docker/                      # 各服务 Dockerfile
│   ├── scripts/                     # dev.sh / build-tauri.sh
│   └── config/                      # agent.example.yaml / mcp.example.yaml
│
├── docs/
│   ├── architecture/                # 架构图、数据流、HITL 流程
│   ├── security/                    # 威胁模型、凭证保险箱、沙箱设计
│   ├── api/                         # SSE 协议规范、MCP 工具清单
│   └── ops/                         # 部署、审计查询
│
└── .github/
    └── workflows/                   # ci / release / audit-export
```

## 4. 快速开始

```bash
# 0. 安装基础工具
make bootstrap          # 检查 uv / pnpm / rust / tauri 依赖

# 1. 启动 Agent + 全部 MCP Server
make dev                # = docker-compose -f infra/docker/docker-compose.dev.yml up

# 2. 启动 Tauri 桌面端 (开发模式)
cd apps/desktop
pnpm install
pnpm tauri dev

# 3. 打生产包
make build              # 构建 Tauri 安装包 + Agent 独立可执行

cd d:\ditPref\apps\desktop
$env:PATH = "$env:USERPROFILE\.cargo\bin;$env:PATH"
pnpm tauri build
```

## 5. 开发约定

- **包管理** (Python): `uv` (统一 lockfile)
- **包管理** (JS): `pnpm` workspaces
- **Rust**: `cargo` 1.78+
- **Tauri**: 2.0+ stable
- **Python**: 3.10+
- **Node**: 20+
- **代码风格**: `ruff` (py) + `prettier` + `eslint` (ts) + `rustfmt` (rs)
- **测试**: `pytest` + `vitest` + `cargo test`
- **分支模型**: trunk-based, feat/* → main, 强制 PR + 2 reviewer

## 6. 后续任务路线图

- [X]  **A. 完整目录结构** ← 当前
- [ ]  **B. mcp-server-database 核心代码** (SQL 校验 / 高危拦截 / 结果截断)
- [ ]  **C. LangGraph 状态机** (意图 -> 工具 -> HITL -> 执行 -> 修复)
- [ ]  **D. Tauri ↔ FastAPI SSE 通信胶水代码**
- [ ]  E. 凭证保险箱跨平台实现
- [ ]  F. 前端 ApprovalCard + 执行链路 Trace 可视化
- [ ]  G. LangSmith / 本地 Trace 持久化
- [ ]  H. MCP Server e2e 测试矩阵
