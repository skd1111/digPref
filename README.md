# EAIDE — Enterprise AI IDE

> 企业内网本地化 AI IDE Agent：让运维 / 开发 / 业务人员通过**自然语言**，在本地 IDE 形态的界面中，安全地查询、操作、跨系统编排企业生产环境中的异构系统（数据库、REST API、SSH 服务器、老旧 Web 系统等）。
>
> 核心特性：**数据不出域**（敏感 LLM 任务强制走本地 Ollama）、**写操作强制 HITL 人工审批**、**全链路审计可追溯**。

---

## 1. 技术栈


| 层     | 技术                                                                 |
| ------ | -------------------------------------------------------------------- |
| 桌面端 | Tauri 2.0 (Rust) + React 18 + TypeScript + Tailwind CSS              |
| 控制层 | Python 3.12 / FastAPI / LangGraph                                    |
| 执行层 | MCP Servers（Python，stdio / HTTP）                                  |
| 协议   | `packages/shared-protocol`（TS ⇄ Python 双侧镜像）                  |
| 存储   | SQLite（audit / sessions / router 等多库物理隔离）+ OS Keychain 凭证 |
| 包管理 | pnpm workspaces（JS）+ uv（Python）+ Cargo（Rust）                   |

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
│   - 结构化意图识别（改写/追问/风险）→ 工具路由 → 写操作检测      │
│     → HITL → 执行 → Auto-Repair                                  │
│   - LMRouter: 五维评估调度 + 多级降级 + 熔断 + 分层缓存(L1/L3)  │
│   - 提示词模板体系: llm/prompts/*.md（版本化）+ JSON 四层防御     │
│   - 多智能体 Orchestrator: 派生子 Agent 并行 + HITL 反向 interrupt│
│   - 本地小模型 / MACC 上下文压缩 / DSpark 推测解码 / 缓存命中率优化│
└──────────────────────────────────────────────────────────────────┘
                          ▲ MCP (stdio / HTTP) + 内置工具
                          ▼
┌──────────────────────────────────────────────────────────────────┐
│  执行层                                                           │
│   - 内置工具 41 个 (进程内 <1ms, 路径沙箱 7 项校验)               │
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


| 模块                        | 说明                                                                                                                                                                                                                                                                                                                                                                                                                             |
| --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 基础架构                    | Tauri 桌面壳 / 四象限 IDE 布局 / LangGraph 状态机 / HITL 审批闸门 / SSE 事件桥 / SQLite 审计                                                                                                                                                                                                                                                                                                                                     |
| 双框架智能体（Phase 18）    | ModeRouter 智能路由 Coding / Work 双模式 + 任务分解 + Auto-Repair 自动修复                                                                                                                                                                                                                                                                                                                                                       |
| 内置工具层                  | 41 个进程内工具（文件读写/搜索/计算器/正则/时间日期含农历/相对日期解析/shell 等）+ 文档处理工具族（file_to_markdown 文件转 Markdown / Excel 查询导出 / PDF 合并拆分 / Word 生成）+ klogg 式大文件只读查看与搜索（突破 100MB 限制）+ 路径沙箱 + HITL 前置闸门                                                                                                                                                                     |
| 结构化意图识别              | Intent Router 一次分析产出改写句/细分意图/实体/追问信号/风险等级；三级快速路径：关键词 → 向量语义路由（semantic-router 模式，本地 embedding 零 LLM）→ LLM 分析，明确信号直达路由省掉决策器调用                                                                                                                                                                                                                                 |
| 智能 LLM 路由               | 五维评估调度 / 多级降级 / 熔断器 / 分层缓存（L1 精确响应 + L3 幂等工具）/ 预算控制 / 关键任务双模型并行+裁判 / 内网或云端可达时自动切换 OpenAI 原生 Function Calling 工具循环（首消息探测一次，不可用回退提示词协议，HITL 闸门不变）/ 中文响应硬兜底（responder 层语言守卫，小模型指令遵循失效时强制中文输出）                                                                                                                                                                                             |
| 缓存命中率优化（Phase 17）  | L1 精确响应缓存（接入 summarise 最终答案链，重复请求直返省一次全链 LLM 调用）+ L3 幂等只读工具结果缓存（白名单 16 工具 + write_detector 双重把关，短 TTL）+ 请求规范化稳定 key + prompt 版本化（bump 自动失效缓存）+ MCP 工具稳定排序 + Ollama keep_alive（多轮 KV 复用）+`GET /router/cache-stats` 命中率统计 / `POST /router/cache-toggle` 一键回滚 + `llm_cache_stats` SSE 实时推送；红线：写操作 / 敏感任务 / 凭证一律不缓存 |
| Token 用量计量              | 状态栏/顶栏「Agent: 就绪」旁徽章实时展示上传（prompt）/ 下载（completion）速率（近 30s 滑动窗口，2s 轮询）+ 当日调用次数；鼠标悬浮弹明细卡片（当日 tokens 累计 / 调用次数 / 费用总 + 按模型明细，单价取模型管理`cost_per_1k_tokens`，本地免费模型不计费）。数据落 router.db `token_usage_daily`，跨重启保留、按日滚动；后端无 usage 字段时按字符数估算                                                                           |
| 提示词模板体系（v2.64）     | 统一六段式`.md` 模板资产（`llm/prompts/`，运行时加载、非工程人员可直接编辑）+ JSON 输出四层防御（API 参数 → Prompt 纪律 → 容错解析 → 重试自纠错）+ 模型族适配矩阵（Claude/OpenAI/开源/国产闭源）                                                                                                                                                                                                                              |
| Skill / MCP 生态            | Skill YAML + MCP JSON Schema / 关键词路由 / 热加载 / 多项目隔离                                                                                                                                                                                                                                                                                                                                                                  |
| 专家团资产                  | 团+成员两级结构化/ 设置页独立维护（CRUD + 导入导出）/ 运营工作台点业务自动选择（Skill 预设 → LLM 本地→内网→云端降级 → 关键词）+ 输入栏手选 + 会话自动注入；选团状态跨模式切换持久化（store 记录业务锚点，切回同业务直接复用不重跑推荐，在途推荐竞态校验防错配）；Skill 预设默认专家团/材料/交付物三字段；资产包格式 zip = 根目录`team.yaml`（提示词/成员定义）+ `templates/`（交付物文档模板 docx/md），导入一次到位、导出整团打包；种子：`docs/expert-team-seeds/due-diligence-team.zip`                                          |
| 专家验收工作流（运营模式）  | 取代传统大 Chat：按专家上传材料（拖拽 / Ctrl+V 粘贴入件）→ AI 审核验收（结论+关键要素+证据链，幻觉证据丢弃，低置信标红）→ 专家迷你提问（人设 + knowledge-base 制度出处引用）→ 交付标准逐项确认 → 交付物 zip 打包导出（交付文件/检查结果/问答记录/业务小结/报告初稿 docx 模板占位符渲染）+ 多文档交叉比对防呆 + 人工改判纠错样本闭环（全操作审计）                                                                            |
| 多环境治理                  | 4 env preset / Keyring 占位符 / PBKDF2+Fernet 加密导入导出 / 环境徽章                                                                                                                                                                                                                                                                                                                                                            |
| 代码导航                    | Tree-sitter AST 索引 + SQLite 符号库 + Monaco 跳转 + AI 语义推断 + 实时语法错误显示（tree-sitter ERROR/MISSING 节点 → Monaco marker，纯语法级不做语义分析）+ 文件树深层展开定位 + 编辑器选区代码自动附加进 prompt（防幻觉跳转）                                                                                                                                                                                                                                                                                                                                                                 |
| 业务功能点导航              | 代码→业务功能点抽象 + YAML 热加载（运营模式业务列表数据源）+ Open Folder 同步触发 AI 功能点提取                                                                                                                                                                                                                                                                                                                                                                     |
| 需求改造工作流              | 功能点发起改造需求 + AI 可行性对齐 + 需求卡片（批次/编号/改造点/影响面/外部系统） + 版本快照 + 按批次导出 MD/Word                                                                                                                                                                                                                                                                                                                |
| 运营模式（独立页签）        | 顶部顶级页签与开发模式并列：运营工作台二栏（左业务列表 16 模块导航 + 中专家验收工作流 ExpertWorkflowPanel：专家团页签 + 横向专家卡拟人化审核，新结果未读徽标）；功能点以 Skill 承载、选中业务自动注入 Skill 与专家团；导出自动生成可审计业务记录卡片                                                                                                                                                                             |
| 右键编译（本地轻量）        | 文件树右键编译 .java（javac，Maven 多模块项目级 classpath 汇总）/ .py（py_compile）/ .c/.cpp（gcc/g++）；错误输出按系统代码页解码（UTF-8 → GBK 回落）；设置页「编译配置」面板（编译器目录手选/自动探测 PATH + 产物输出目录，compile.json 持久化，Agent 离线也能编译）；用户显式触发的 UI 命令，不走 HITL                                                                                                                          |
| 数据字典（公共参数）        | ActivityBar 独立入口（📖，穿透所有模式）：Skill 里写引用 key、字典维护参数值；搜索 / 分类筛选 / CRUD / seed 内置条目可显式覆盖（dict.db）                                                                                                                                                                                                                                                                                        |
| 选项式追问与高级设置        | AI 需确认时输出可点选选项卡片（3-5 选项/理由/推荐项/多问题页签/自定义输入）；推理模式与会话自主性迁入设置-高级设置                                                                                                                                                                                                                                                                                                               |
| 大文件日志查看              | Rust 字节偏移索引 + 流式读取 + 进程内搜索 + GBK 编码 + AI 日志分析                                                                                                                                                                                                                                                                                                                                                               |
| 会话管理（V1.6）            | 会话生命周期 / Checkpoint / FTS5 全文搜索 / 分支 / 共享 / .eas 加密导出导入 / 启动中断会话恢复 / 事件哈希链校验 / 详情五 Tab 中央面板内联视图（#94）                                                                                                                                                                                                                                                                                                |
| MACC 上下文压缩             | 三层自适应压缩（工作记忆 / 情景记忆事件图谱 / 语义规则蒸馏）                                                                                                                                                                                                                                                                                                                                                                     |
| 多智能体调度                | Orchestrator 派生子 Agent / Worker Pool / DLQ / 限流 / 派生树硬上限                                                                                                                                                                                                                                                                                                                                                              |
| DSpark 推测解码             | Qwen2.5 草稿模型 + llama.cpp 推测解码 + 场景化策略路由                                                                                                                                                                                                                                                                                                                                                                           |
| 数据专家模式（V1.1 补齐版） | NL2SQL（向量 Schema 链接选 3-5 表，本地 embedding 不可达退化关键字 + 历史已确认 SQL few-shot 飞轮 + 生成后 sqlglot 校验/问题回喂自纠错重试 1 次 + SELECT 白名单前置校验）+ 业务字典 YAML 外置（config/biz_dict：_global 全局 + 源级术语→编码映射，运营改口径不碰代码，mtime 缓存改文件即时生效，缺失退化内置默认）+ ReadOnlyPool 真实执行（6 方言族）+ 真实 Schema 同步（无 schema 数据源首拉自动补同步）+ 重查询 HITL 确认 + WS/Arrow 大结果流（Rust 中继）+ 虚拟滚动 DataGrid + ECharts + 沙箱 Python 清洗（SQL 结果 df 链路）+ Excel/PDF/CSV 导出（服务端取数 + PII 脱敏 + 水印，支持自选输出路径）+ 定时报表调度 + 历史分析（同一 SQL 去重只留最近一次）                                                                                                                                 |
| 思维链可视化（V1）          | 中文思维链时间线 + 文件操作追踪 + hover Diff 预览 + 任务结束改动文件汇总（changed_files 卡片，write/edit 成功事件累积）                                                                                                                                                                                                                                                                                                                |
| 文档风险审核                | PDF/DOCX/TXT/MD 解析 → LLM 分类（合同/制度/公告/标书 × 合规/法律/数据安全/资金）→ 分块风险分析 → 风险位置正文高亮 + 知识库/案例库引用依据（审核专家模式文档审核 Tab）；财税规则库（knowledge-base/fiscal-tax：BM25 + 本地 embedding 混合检索，按相关性自动注入条款依据，embedding 不可达退化为纯关键词）                                                                                                                                                                                                                                                        |

### 🟡 部分实现


| 模块                     | 现状                                                                                                         | 未实现部分                                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------- |
| 本地小模型 + 知识库      | 后端引擎已交付（分块 + 主图接入；检索为占位）；**方向调整：本地不自建 RAG，未来走外部 RAG 接口 + 本地 grep** | 前端知识库管理界面、Sidecar 生命周期管理                        |
| 审核专家模式（金融审计） | V0 后端骨架（审批队列 + 签名链 + 合规规则）+ 文档审核子功能已交付（风险高亮 + 知识库引用）                   | 前端 AuditDashboard 完整工作台、Monaco Diff 审核、MFA、双人复核 |
| 本地图像处理             | V0 后端骨架（6 端点 + 任务表）                                                                               | ONNX 超分 / PaddleOCR / 倾斜校正真实集成、前端 UI               |
| 前端实时预览             | V0.1（Vite 管理器 + 独立预览窗口 + 设备模式）                                                                | 真实 Vite 端到端 HMR 实测收尾                                   |
| 大文件日志 V1.5          | 核心读写搜索已交付                                                                                           | tail -f 实时监控、ripgrep 集成、AI 分析 UI                      |
| 编译打包引擎（3A）       | 本地右键轻量编译已交付（javac/py_compile/gcc + 编译配置面板 + 项目级 classpath）                                    | 完整工具链探测 + 异步构建流水线 + SSE 实时日志                  |

### ⚪ 未实现（规划中）


| 模块                         | 说明                                         |
| ---------------------------- | -------------------------------------------- |
| 类 FinalShell 远程管理（2B） | SSH PTY + SFTP 双栏 + 资产树联动             |
| 部署流水线（3B）             | 状态机部署 + 自动回滚 + 零停机 swap          |
| Arthas JVM 热更（3C）        | AI 热修复 + 字节码结构校验                   |
| 部署 UI（3D）                | 流水线可视化 + 实时日志                      |
| 多人协同审批（8）            | 跨终端审批路由 + OA/IM 集成（需独立 Server） |
| 任务级协作（9）              | 上下文锚点 + 行级评论 + @ 提醒               |
| 统一身份认证 IAM（10）       | OIDC/LDAP/企微 + RBAC/ABAC + 数字签名 + MFA  |
| 离线授权系统（11）           | 机器指纹 + License RSA 签名 + 试用管理       |

## 4. 安全红线（绝对不可妥协）


| 红线                                 | 实现位置                                                                                                   |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------- |
| HITL 强制审批（写操作不可绕过）      | `services/agent/src/agent/graph/nodes/hitl_gate.py` + 前端 ApprovalCard                                    |
| SQL 只读白名单（除 dev 外仅 SELECT） | `dataexpert/readonly/guard.py` 的 `enforce_select_only`（`EAIDE_ENV` + 豁免开关，fail-safe）               |
| 敏感任务强制本地模型                 | `services/agent/src/agent/llm/router.py` 的 `_LOCAL_ONLY_TASKS`                                            |
| 凭证零落盘                           | `apps/desktop/src-tauri/src/credentials/{windows,macos,linux}.rs`                                          |
| 全链路审计                           | `services/agent/src/agent/audit/store.py`（Python + Rust 双 schema 镜像）                                  |
| 路径沙箱                             | `path_sandbox` 7 项校验（Python / Rust 双侧实现）                                                          |
| 缓存安全（写操作/凭证不缓存）        | `llm/tool_cache.py` 白名单 + `write_detector` 双重把关；`llm/normalize.py` 稳定 key（凭证/时间戳不进 key） |
| MCP 层安全                           | 各`services/mcp-servers/*/safety/` 子模块（SQL 拦截 / 白名单 / 黑名单）                                    |
| SSE 事件契约                         | `graph/stream.py` + `sse_bridge.rs` + `ipc/events.ts` 三处强制同步（含慢任务心跳保活 + CancelledError 终止信号兜底，防静默断连）                                         |

## 5. 目录结构

```
.
├── apps/desktop/              # 【表现层】Tauri 2.0 + React
│   ├── src/                   # React 前端（layouts/components/store/ipc/streams）
│   └── src-tauri/             # Rust 后端（commands/credentials/stream/audit/logviewer/compile…）
├── services/
│   ├── agent/                 # 【控制层】Python Agent（FastAPI + LangGraph，30+ 模块）
│   │   └── src/agent/
│   │       ├── graph/         # LangGraph 状态机（intent/planner/tool_runner/hitl_gate/repair…）
│   │       ├── dual/ coding/  # Phase 18 双框架（Coding Agent / Work Agent）
│   │       ├── llm/           # LMRouter 智能路由 + _LOCAL_ONLY_TASKS + prompts/*.md 模板资产 + 分层缓存（normalize/tool_cache/cache_stats）
│   │       ├── orchestrator/  # 多智能体调度
│   │       ├── sessions/      # 会话管理 + MACC 压缩
│   │       ├── knowledge/     # RAG 知识库
│   │       ├── builtin/       # 内置工具层（41 工具：文件/shell/文档处理/大文件查看等）
│   │       ├── dataexpert/    # 数据专家模式（NL2SQL + 只读池 + 沙箱 + 导出 + WS/Arrow 流）
│   │       ├── trace/         # 思维链收集与文件操作追踪
│   │       ├── ops/ expert_teams/ # 运营验收工作流（case 存储/交叉比对/报告模板）与专家团资产包
│   │       ├── …              # codenav（索引/语法检查）/biznav/reqflow/skills/preview/doc_review（含财税规则库）/audit/safety 等
│   └── mcp-servers/           # 【执行层】MCP Server 矩阵（database/rest/ssh/rpa）
├── packages/shared-protocol/  # 跨语言协议包（TS + Python 唯一事实来源）
├── knowledge-base/            # 文档审核知识库（合规/法律/数据安全/资金风险 + 财税法规/案例/准则 + 案例库，随 exe 打包；运营专家提问制度出处检索源；财税规则库混合检索素材）
├── config/driver/             # 离线数据库驱动 wheel（不入库，构建时本地放置）
├── config/biz_dict/           # NL2SQL 业务字典 YAML（_global 全局 + {source_id} 源级术语→编码映射，改文件即时生效）
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
>
> 📚 文档审核知识库 `knowledge-base/` 已随 PyInstaller 打包（`eaide-agent.spec` datas）；运行时优先读工作目录下的同名目录（便于自行更新），缺失时回退到包内置副本。
>
> 📖 NL2SQL 业务字典种子 `config/biz_dict/` 同样随包分发（同策略：工作目录优先，缺失回退内置副本）；运营在工作目录放同名目录即可覆盖包内版本。

## 7. 开发约定

- **Python**：`ruff` + `mypy --strict`；snake_case；行宽 100；pytest（`asyncio_mode = "auto"`）
- **TypeScript/React**：`prettier` + `eslint`（零告警）；camelCase；vitest
- **Rust**：`rustfmt` + `cargo clippy --all-targets -- -D warnings`；行宽 120；模块测试写在 `tests.rs`
- **分支模型**：主干开发，`feat/*` 短分支 → PR 合入 `main`（2 reviewer）
- **协议同步**：线协议类型改动必须同时更新 `shared-protocol` 的 TS 与 Python 两侧
- **SSE 事件**：新增事件名必须在 `graph/stream.py`、`stream/sse_bridge.rs`、`ipc/events.ts` 三处同步
- **提示词模板**：LLM 提示词资产统一放在 `services/agent/src/agent/llm/prompts/*.md`（六段式结构：角色/任务/输入/输出格式/硬性约束/示例），占位符用 `{{KEY}}`；JSON 输出统一走四层防御（`agent.llm.json_discipline`）。索引与接入规范见 `docs/prompt-templates.md`、`docs/llm-prompt-sop.md`
- **Prompt 版本化**：修改 `llm/prompts/*.md` 后必须 `bump_prompt_version()` bump 版本（自动失效 L1 缓存，防旧答案误命中）；缓存相关约束见 `docs/design/phase-17-cache-hit-rate.md`

## 8. License

[MIT](LICENSE) © 2026 skd1111
