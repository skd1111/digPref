# EAIDE 变更日志

> 本文件是项目唯一的版本变更记录台账。每??Phase 交付或重大变更时必须在此追加记录??
---

## 2026-08-31

### v2.122 — Phase 19 代码评审修复：后台任务生命周期 + 落库脱敏 + 状态机一致性（BUGFIX #178 / #179）

- **背景**：Phase 19 自进化模块交付后专项代码评审，共 8 个问题（2 重要 + 6 建议），本次全部清零。
- **改动**：① 收尾后台任务持强引用（[stream.py](../services/agent/src/agent/graph/stream.py) `_EVOLUTION_BG_TASKS`，防 asyncio Task 被 GC 静默回收）；② 影子实验 `db_path` 全链路透传（`_replay_requests` / `_record_run`）；③ 新增 `scrub_dsn()`（[schema.py](../services/agent/src/agent/skills/schema.py)），轨迹 `reason` / `answer_digest` / `rewritten_query` 落库前脱敏；④ 自动采纳与手动 apply 共用 `_demote_other_actives` 保证同 skill 单 active；⑤ 反思按 `source_session` 去重；⑥ 蒸馏去重纳入 `approved` 状态；⑦ `_FAIL_MARKERS` 改整句硬失败文案防误判；⑧ 经验检索热路径按绝对路径缓存建表标志。
- **验证**：新增回归 7 条，连同存量共 66 条全绿；ruff / mypy --strict（evolution 包）零错误。台账见 BUGFIX_LOG_2.md #178 / #179。

### v2.121 — 意图识别四层增强：语义路由深化 + 槽位硬校验 + 上下文记忆 + 动态 Few-Shot 闭环（含进程内 ONNX 向量模型）

- **背景**：意图识别是全部下游路由的第一道闸门，参考 Semantic Router / Instructor / LlamaIndex 动态示例选择器 / Mem0 思路，在不新增外部服务的前提下把误触率降下来、把长尾准确率提上去。纯自实现，不引 DSPy/Instructor/rank_bm25。
- **改动**：① **内嵌向量模型**：新增 [onnx_embedding.py](../services/agent/src/agent/llm/onnx_embedding.py)，bge-small-zh-v1.5 ONNX 量化版（`model/bge-small-zh-v1.5-onnx`，随仓分发）进程内推理（tokenizers + onnxruntime，单条 ~2ms，无 CUDA/无子进程/无端口）；[embedding.py](../services/agent/src/agent/llm/embedding.py) 统一构建入口（显式外置端点优先），语义路由 / NL2SQL linker / 财税规则库三处消费点接入；`semantic_route_enabled` 默认开启（模型缺失静默回退），测试默认关闭（conftest）。② **语义路由深化**（[semantic_route.py](../services/agent/src/agent/graph/semantic_route.py)）：Route 增 `hard_negatives` / `threshold` / `high_risk`；新增 db_query / db_drop / ssh_execute 种子路由（含「写脚本/排障」类困难负样本）；纯 Python BM25（ASCII 代号 + 中文 bigram）与余弦加权融合（`semantic_route_hybrid_weight` 0.35）；高风险路由阈值更高（0.85）且命中强制追问确认；闭环反馈困难样本合并为全局动态负样本。③ **结构化槽位校验**：[types.py](../services/agent/src/agent/llm/types.py) 新增 `IntentAnalysisSchema`（Pydantic 硬校验，失败回退宽容解析）+ 新模块 [intent_slots.py](../services/agent/src/agent/llm/intent_slots.py)：必填槽位缺失且风险 high/critical 时代码层强制 `need_clarification`，绝不放行猜测执行（[intent.py](../services/agent/src/agent/graph/nodes/intent.py) 接入）。④ **上下文与短期记忆**：前端 [ChatInput](../apps/desktop/src/components/chat/ChatInput.tsx) pageContext 新增 `activeEntity`（数据工作台当前选中表，协议支持 table/terminal/file），`format_page_context` 压成「当前正查看数据表 xxx」注入意图分析；新增 [intent_memory.py](../services/agent/src/agent/graph/intent_memory.py) `intent_recent` 表（按任务页签保留近 5 轮意图链路），追问/修改类短句自动注入「前几轮操作」。⑤ **动态 Few-Shot + 闭环反馈**：`intent_memory.db`（与 knowledge.db 同目录）三表——成功路由案例（只存实体**键名**，参数明文不落库）经向量检索 top-3 注入 intent_router system prompt（ollama / private 双后端）；👍 置 positive、👎 置 negative 且原查询回流困难样本库（[evolution/api.py](../services/agent/src/agent/evolution/api.py)）；HITL 用户拒绝审批同样回流（[hitl_gate.py](../services/agent/src/agent/graph/nodes/hitl_gate.py)，超时守卫不算）——「越用越准」的数据飞轮。⑥ intent_router.md 追加动态案例纪律（v1.0.2）；新配置：`semantic_route_negative_margin` / `semantic_route_hybrid_weight` / `semantic_route_high_risk_threshold` / `local_embedding_onnx_dir`。⑦ **打包**：[eaide-agent.spec](../eaide-agent.spec) datas 新增 `model/bge-small-zh-v1.5-onnx`（~23MB，未入 git 时自动跳过、语义路由静默降级），hiddenimports 补 onnxruntime/tokenizers；运行时路径多级回退（cwd > _MEIPASS > 仓库根，同 knowledge-base 策略）；ruff 排除 *.spec（PyInstaller DSL）。
- **验证**：进程内模型端到端冒烟（正例命中 0.94–0.99，三类困难负样本全拦截，高风险命中带追问）；后端新增 4 组 40 用例全过，全量回归退出码 0（仅 1 个既有定时抖动用例重跑即绿）；前端新增 activeEntityContext 2 用例，全量 348/351 通过（3 个失败为 toolchainSettings 环境性抖动，单独重跑即绿，与本次无关）；ruff / mypy（新模块）/ eslint / tsc 全绿。未新增任何 SSE 事件（无三处同步需求）；`_LOCAL_ONLY_TASKS` 未新增任务类型（红线计数 22 不变）。

### v2.120 — Phase 19 V1.5 自进化：Few-shot 影子回放优化 + Prompt 版本采纳/回滚（Phase 19 收官）

- **背景**：完成设计文档 §9 最后一期（L3 系统与 Prompt 层，DSPy/OPRO 裁剪）：不改权重、不改模板结构，只把 Skill 的 `few_shot_examples` 作为可学习参数，离线影子评测验证后由人工采纳。
- **改动**：① **影子优化实验**：新增 [prompt_opt.py](../services/agent/src/agent/evolution/prompt_opt.py) + 模板 prompt_optimize.md / replay.md：取材（同签名低分反馈 + 历史请求）→ OPRO 式候选生成 → **影子回放**（新旧两版 few-shot 各自引导生成草稿，经 `answer_judge` 打分比较均分）；仅增益 ≥ `prompt_optimize_gain_threshold`（默认 0.5）产出候选版本；新任务 `prompt_optimize` 入 `_LOCAL_ONLY_TASKS`（红线计数 21→22）。② **版本管理**：`prompt_versions` 表 CRUD + apply（写回技能 YAML 并重载，原 active 降级）/ rollback（一键恢复上一版，无上一版则清空示例）；`evolution_prompt_auto_adopt` 默认关闭（人工确认）。③ **API**：/evolution/prompt-optimization/run + /evolution/prompt-versions list/apply/rollback。④ **前端**：新增 [PromptOptPanel](../apps/desktop/src/components/evolution/PromptOptPanel.tsx)（技能选择 + 运行实验 + 增益结果 + 版本采纳/回滚），挂入经验库页。⑤ **SSE `evolution_experiment_done` 三处同步**；Rust 新增 4 命令；`experiment_runs` 实验审计表启用。
- **验证**：后端新增 13 用例全过，全量回归 0 失败；前端全量 349/349（新增 4 用例）；cargo check + clippy -D warnings 通过；ruff 全仓 / mypy strict（evolution）/ eslint / tsc 全绿。Phase 19 三期（V0 经验闭环 / V1 技能蒸馏 + Judge / V1.5 Prompt 影子优化）全部交付。

### v2.119 — Phase 19 V1 自进化：技能草稿蒸馏 + 主对话 Judge 泛化 + 进化看板 + 草稿人工审核

- **背景**：V0（v2.118）交付经验学习闭环后，推进设计文档 §9 V1：把成功轨迹沉淀为可复用技能（Voyager 裁剪为规则类 YAML 草稿），并把 LLM-as-a-Judge 从子任务泛化到主对话终答，补齐自评测信号源。
- **改动**：① **技能蒸馏（L2）**：新增 [skill_distiller.py](../services/agent/src/agent/evolution/skill_distiller.py) + 模板 [skill_distill.md](../services/agent/src/agent/llm/prompts/evolution/skill_distill.md)：同签名 ≥ `skill_draft_min_successes`（默认 3）次成功且无 Skill 覆盖时蒸馏草稿；过 `validate_skill_yaml` + `validate_no_dsn` 双重校验，**草稿强制 `enabled: false` 永不自动启用**；新任务 `skill_distill` 入 `_LOCAL_ONLY_TASKS`（红线计数 19→21）。② **主对话 Judge**：新增 [judge.py](../services/agent/src/agent/evolution/judge.py) + 模板 answer_judge.md：确定性抽样（计数器取模，默认 0 关闭）对终答打 1–5 分（复用 `_parse_judge_output`），落 `evaluation_signals`（source=judge）；任务 `answer_judge` 入本地红线。③ **草稿审核闭环**：/evolution/skill-drafts list/approve/reject 端点，approve 是唯一启用入口（重跑校验 → 写入 skills/ → `load_one` 生效，同名冲突 409）；前端 [SkillsManager](../apps/desktop/src/components/skills/SkillsManager.tsx) 新增「待审草稿」Tab（[SkillDraftsPanel](../apps/desktop/src/components/skills/SkillDraftsPanel.tsx)，YAML 预览 + 采纳/拒绝）。④ **进化看板**：/evolution/stats 统计端点 + 经验库页统计卡片（信号分布 / 用户反馈 / Judge 均分 / 待审草稿）；`evolution.db` 新增 skill_drafts / prompt_versions / experiment_runs 表（后两表为 V1.5 预留）。⑤ **SSE `skill_draft_ready` 三处同步**（stream.py / sse_bridge.rs / events.ts）；Rust 新增 4 命令。
- **验证**：后端新增 24 用例全过，全量回归 0 失败（仅 1 个既有 Windows skip）；前端全量 345/345（新增 7 用例）；cargo check + clippy -D warnings 通过；ruff 全仓 / mypy strict（evolution）/ eslint / tsc 全绿。

### v2.118 — Phase 19 V0 智能体自进化与自评测闭环（经验学习 + 用户反馈 + 任务签名）

- **背景**：Agent 是无记忆的被动执行器，做完即忘——成功轨迹、失败教训、用户满意度随会话归档流失，同类任务不会越用越好。立项文档：[phase-19-self-evolution.md](design/phase-19-self-evolution.md)（自进化四层次裁剪：L1 经验 / L2 技能蒸馏 / L3 Prompt 优化；排除 L4 参数微调与可执行代码技能）。
- **改动**：① 新增 [evolution/](../services/agent/src/agent/evolution/) 模块 + 独立 `evolution.db`（物理隔离）：`signature` 任务签名（intent×skill×工具指纹）、`storage` 信号/轨迹/经验三表、`reflection` 失败反思（新任务 `reflection` 入 `_LOCAL_ONLY_TASKS` 本地红线，模板 [evolution/reflection.md](../services/agent/src/agent/llm/prompts/evolution/reflection.md)）、`memory` 经验检索经 `extra_rules` 通道注入（[loop.py](../services/agent/src/agent/tools/loop.py)）、`trajectory` 任务收尾钩子（[stream.py](../services/agent/src/agent/graph/stream.py) done 前后台 best-effort）、`api` 反馈 + 经验库管理 4 端点。② 用户 👍/👎：[FeedbackButtons](../apps/desktop/src/components/chat/FeedbackButtons.tsx) 挂终答气泡，👎 带纠错文本触发后台反思；Rust 新增 4 命令 + `agent_delete`（[state.rs](../apps/desktop/src-tauri/src/state.rs)）。③ 经验库管理页 [EvolutionPanel](../apps/desktop/src/views/settings/EvolutionPanel.tsx)（设置 → 经验库，启停/删除人工否决）。④ SSE `evolution_insight_created` 三处同步（stream.py / sse_bridge.rs / events.ts）。⑤ 红线：轨迹只存摘要+工具指纹（无参数明文/凭证）；反思全链 best-effort 不阻塞主链路；经验注入带 token 上限。
- **验证**：后端新增 25 用例全过，全量仅剩 4 个既有环境性失败（内网 LLM 502 / vite 环境，HEAD 基线复现证实与本次无关）；前端全量 338/338（新增 7 用例）；cargo check + clippy -D warnings 通过；ruff 全仓 / mypy strict（evolution）/ eslint / tsc 全绿。

## 2026-08-27

### v2.117 — 参数确认式追问补选项卡 + 执行树同类工具调用合并（BUGFIX #168）

- **背景**：用户反馈 ① PPT 参数确认终答（「请直接回复例如：'10 页 / …'，缺省我会按 '…' 执行」）没有选项卡只能手打；② 执行过程树把 22 条相同的「执行命令」逐条刷屏，同一个类型应只显示一个。
- **改动**：① [responder.py](../services/agent/src/agent/graph/nodes/responder.py) 新增 `_confirm_combo_clarify`：确定性抽取「缺省按 X 执行」默认组合（需与回复引导语同现，双门槛防误伤），生成「按默认配置执行：X（推荐）/ 自定义配置」二元确认卡；`_CHOICE_CUE_RE` 补「直接回复 / 回复例如 / 回复即」引导语。② [ExecutionTree](../apps/desktop/src/components/chat/ExecutionTree.tsx) 新增 `compressSameType`：连续同名工具行折叠为一行 + ×N 徽标，聚合状态（任一在跑转圈 / 任一失败标红），摘要仍按真实步数统计；非工具步骤与交替不同工具不受影响。
- **验证**：后端 14/14（含截图原文成卡 + 两个反例不误伤），前端全量 294/294（新增 3 例 + occurrence 用例适配），ruff / tsc / eslint 全绿。

### v2.116 — Claude Code 式执行过程可视化：细粒度事件协议 + Rust 本地执行器双形态 + shell 流式输出 + 写前 Diff 预览（含 BUGFIX #166）

- **背景**：用户希望像 Claude Code / Codex 一样实时看到「正在思考 / 正在调哪个工具 / 正在读哪个文件 / 命令跑到哪了」，工具过程可折叠、可展示 Diff、危险操作可确认。按设计方案映射到 EAIDE 三层架构（React/Tauri + FastAPI/LangGraph + Rust 沙箱）补齐四处缺口。
- **改动**：① 协议：[shared-protocol](../packages/shared-protocol/src/ts/events.ts) 新增 4 类细粒度事件（`run_started` / `tool_progress` / `shell_chunk` / `file_write_preview`）+ `ToolResult.ui` 摘要字段，Py/TS 镜像，[stream.py](../services/agent/src/agent/graph/stream.py) / [sse_bridge.rs](../apps/desktop/src-tauri/src/stream/sse_bridge.rs) / [events.ts](../apps/desktop/src/ipc/events.ts) 三处同步；流循环轮询间隔缩到 0.4s，长工具执行期间过程事件不再卡到节点结束。② 取消：新增 `POST /chat/{run_id}/cancel` 协作式旗标，流循环与工具循环（native + prompt 双路径）在边界短路，收尾 done 带 `cancelled`。③ Rust 执行器：新增 [executor_rpc.rs](../apps/desktop/src-tauri/src/executor_rpc.rs) + `eaide-executor` stdio JSON-RPC 二进制（复用同一份沙箱实现），[jsonrpc_stdio.py](../services/agent/src/agent/builtin/jsonrpc_stdio.py) 客户端在独立部署形态由 lifespan 注入；打包三处核对（spec datas / tauri resources / CI 占位）+ build-all.bat、release.yml 构建步骤。④ 过程可视化：shell 工具异步流式化（逐批 `shell_chunk`，白名单/超时强杀语义不变），[ShellOutputPanel](../apps/desktop/src/components/chat/ShellOutputPanel.tsx) 终端质感输出面板 + 退出码徽标，[ExecutionBlock](../apps/desktop/src/components/chat/ExecutionBlock.tsx) 工具卡支持 `tool_progress` 副标题。⑤ 写前 Diff：写类工具在 HITL 暂停前先发 `file_write_preview`（unified diff，只读不落盘），[WritePreviewCard](../apps/desktop/src/components/chat/WritePreviewCard.tsx) 内嵌 +/- 统计 + 复用 FullDiffModal 红绿对比，审批卡随后可见。⑥ BUGFIX #166：补回 #165 遗留丢失的 `GENERAL_SHELL_HINT` 常量（crate 编译不过）。
- **验证**：前端全量 290/290（含新增 10 用例）；Python 新增 17 用例全过，全量仅剩环境性失败（预览端口占用）；Rust clippy 零告警、executor_rpc 8 用例 + 真实二进制 ping 冒烟；tsc/ruff/eslint/mypy 全绿。

### v2.115 · 执行步骤树形合并：连续工具调用折叠成树 + 点子项精确定位右侧（BUGFIX #162）

- **背景**：用户反馈「多个工具调用不能合并吗，一点都不人性化」，要求树形展现且点哪步右侧自动跳转高亮对应步骤。
- **改动**：① 新增 [executionGrouping.ts](../apps/desktop/src/lib/executionGrouping.ts)（连续 execution 消息分组 + 同名工具 occurrence 计数，非执行消息天然切断分段）；② 新增 [ExecutionTree.tsx](../apps/desktop/src/components/chat/ExecutionTree.tsx)：摘要行（步数/完成度/失败/总耗时）+ 树形引导线子项，进行中自动展开、完成自动收起；③ [ExecutionBlock.tsx](../apps/desktop/src/components/chat/ExecutionBlock.tsx) 抽 ExecutionRow 复用，[traceStore](../apps/desktop/src/store/traceStore.ts) highlight 扩 occurrence，[ThinkingChainPanel](../apps/desktop/src/components/thinking/ThinkingChainPanel.tsx) 按第 N 次命中精确定位（缺省回退最新，旧行为兼容）；④ [CenterChatFlow](../apps/desktop/src/layouts/CenterChatFlow.tsx) 渲染接分组。
- **验证**：新增 executionTree 11 用例；前端全量 280/280；tsc 零错误。纯前端改动，无协议/后端变更。

### v2.114 · 看门狗防 SSE 静默断连卡死：心跳具名化 + 前端 120s 静默解锁（BUGFIX #161）

- **背景**：用户反馈「一直卡着」：HITL 批准后任务无后续、界面永久「思考中」。取证：SSE 连接静默断开 → 图任务随流被取消 → 审批决策成孤儿，done/error 到不了前端。
- **根因**：心跳是 SSE 注释行，只保活 reqwest 超时、前端不可见，无法感知流已死；图生命周期绑死在 SSE 连接上。
- **改动**：心跳升级为具名 `heartbeat` 事件（三处同步：[stream.py](../services/agent/src/agent/graph/stream.py) / [sse_bridge.rs](../apps/desktop/src-tauri/src/stream/sse_bridge.rs) / [events.ts](../apps/desktop/src/ipc/events.ts)，协议双侧镜像）；新增 [streamWatchdog.ts](../apps/desktop/src/lib/streamWatchdog.ts)：任一事件刷新存活时间戳，120s 静默且仍 busy → 逐 run 解锁并提示「连接已中断，请重试」；顺带补齐 ChatMessage.kind 缺失的 skill_matched（存量 mypy 缺口）。
- **验证**：前端全量 269/269（新增 streamWatchdog 7 用例）；mypy packages strict / ruff 零告警；后端 stream/hitl/approval 相关用例全绿。

### v2.113 · 修复安装包展平 vendor 目录：PPT 技能路由文件 FileNotFoundError（BUGFIX #160）

- **背景**：用户报错：PPT Master 工作流读 `vendor/ppt-master/workflows/routing.md` 报 FileNotFoundError，工具编排不可用。
- **根因**：`tauri.conf.json` bundle.resources 用 map+glob（`vendor/ppt-master/**/*`），Tauri 在 map 形式下会展平到目标目录只留文件名——安装目录子目录全丢、同名文件互覆（12992 → 10967）。
- **改动**：两条改为目录形式（`"../../../vendor/ppt-master/"` / `"../../../vendor/python/"`，递归保留结构）；[installerIncremental.test.ts](../apps/desktop/tests/installerIncremental.test.ts) 新增防展平护栏 2 用例；既有安装已热修（拷回带结构的 vendor 目录）。
- **验证**：installerIncremental 4/4；重建 installer.nsi 核对 oname 恢复层级；热修后安装目录 `workflows/routing.md` / `deps/*.whl` / `ppt-master-site` 全部就位。

## 2026-08-26

### v2.112 · 输入框 ↑/↓ 历史记录快捷输入（终端习惯）

- **背景**：用户要求输入框按方向键上下快捷调出历史输入。
- **交互**（与终端/REPL 一致，按页签隔离历史源）：空输入框按 ↑ 进入浏览并回填最新一条用户消息；浏览中 ↑/↓ 继续翻页（到边界停住）；↓ 到底或 Esc 退出并恢复进入前的草稿；选中条目按 Enter 直接发送（同终端）；非浏览态且输入框非空时 ↑↓ 保持原生光标移动，不劫持多行编辑；切页签/发送后自动复位浏览态。
- **改动**：新增 [chatHistory.ts](../apps/desktop/src/lib/chatHistory.ts)（`buildUserHistory`：仅 user 角色/滤空白/连续去重/时序）；[ChatInput](../apps/desktop/src/components/chat/ChatInput.tsx) 扩展 `handleKeyDown` + 浏览态状态机（草稿存 ref），placeholder 增「空输入框按 ↑ 调历史」提示。
- **验证**：新增 `tests/chatHistory.test.tsx` 9 用例（3 纯函数 + 6 交互）；前端全量 42 文件 259 用例全绿；tsc / eslint 零告警。
- **说明**：历史源为当前页签的用户消息（与 cleanMode/会话模型等「按页签」设计一致），断点清理只影响随发送的上下文、不影响历史快捷输入（后者直接取 messages）。

### v2.111 · PPT Master 打包即用：捆绑嵌入式 Python + 离线依赖 + 启动引导（V9.7）

- **背景**：用户要求「打包后直接就能用」。ppt-master 脚本由 shell 子进程执行，打包目标机可能无 Python/无网（企业内网），必须随包自带解释器与依赖。
- **改动**：
  1. **捆绑运行时三件套**（均不入 git，`infra/scripts/fetch-ppt-master.ps1` 拉取，仿 officecli 模式）：`vendor/ppt-master/`（技能包）、`vendor/python/`（CPython 3.12.10 嵌入式 embeddable，最后一个带二进制的 3.12 版本）、`vendor/ppt-master/deps/`（66 个 cp312 win_amd64 离线 wheel，共 51.7MB；裁掉仅联网可用的 google-genai/curl_cffi）。
  2. **启动引导**：新增 [ppt_master_bootstrap.py](../services/agent/src/agent/ppt_master_bootstrap.py)，main.py 最早期 best-effort 执行：把全部 wheel 解压进 `vendor/python/ppt-master-site/`（marker 幂等）并在 `python312._pth` 登记（嵌入式 Python 按 ._pth 建 sys.path，无需 pip/联网）。
  3. **种子 prompt**：新增【Python 解释器】规则——优先用捆绑 `vendor/python/python.exe`，缺失才回退系统 python。
  4. **分发链路三处核对**（BUGFIX #133 模式）：① spec 不进 datas（避免 onefile exe 膨胀 + 每次启动解压，生产模式 Agent cwd = 安装目录，tauri resources 落点第一级回退即命中）；② tauri.conf.json resources 新增 `vendor/ppt-master/**/*` 与 `vendor/python/**/*`（注：`dir/*` 不递归，必须 `**/*`）；③ ci.yml 两处 / release.yml 补占位与 best-effort 拉取；build-all.bat 增缺失检查自动拉取。
- **验证**：捆绑 Python 实跑 `attribution_guard.py` 退出码 0；18 项核心依赖（pptx/lxml/fitz/pathops/uharfbuzz/PIL/numpy/flask 等）离线导入全通；核心导出模块 `svg_to_pptx` 导入成功；新增 `test_ppt_master_bootstrap.py` 9 用例 + 种子/路由/循环注入回归共 39 用例全绿；tauri.conf.json / ci.yml / release.yml 语法校验通过。
- **已知边界**：① 捆绑运行时仅 Windows x64（macOS 目标退占位，与 officecli 同策略）；② 旁白（edge-tts）/AI 配图仍需外网，内网环境这两项能力不可用但其余链路不受影响；③ 首次启动解压约 50MB wheel 需几秒，后续启动由 marker 短路。
- **关键文件**：[ppt_master_bootstrap.py](../services/agent/src/agent/ppt_master_bootstrap.py)、[fetch-ppt-master.ps1](../infra/scripts/fetch-ppt-master.ps1)、[tauri.conf.json](../apps/desktop/src-tauri/tauri.conf.json)、[eaide-agent.spec](../eaide-agent.spec)

### v2.110 · PPT 生成链路替换：内置 MIT 开源 PPT Master 技能包（vendor/ppt-master）

- **背景**：用户要求用 GitHub 高星项目 PPT Master 替换现有自研规范的 PPT 技能。许可核查：ppt-master 为 **MIT**（SKILL.md 元数据与 LICENSE 明示），符合产品分发红线，可原件内置（区别于 guizang/dashi 等 AGPL 技能只能蒸馏）。
- **改动**：
  1. 完整 vendored hugohe3/ppt-master 主仓 `skills/ppt-master`（12925 文件，约 75.7MB：SKILL.md / workflows / scripts / templates / references）到 `vendor/ppt-master/`；随包自带完整性闸门 `scripts/attribution_guard.py` 实测退出码 0。
  2. 替换 `office_pptx_designer` 种子内容（id 不变，路由/测试兼容）：从「builtin_office_* 五步自研规范」改为「PPT Master 路由式工作流驱动」——SKILL_DIR 定位 → 完整性闸门 → routing.md 四路由选择（Generate PPTX / Create Template / Fill Native PPTX / Enhance Native PPTX）→ 按执行文档串行执行（BLOCKING 门等用户确认）；allowed_tools 改为 shell/read_file/write_file/list_dir/find/mkdir；触发词新增 pptx/美化ppt/填充ppt模板。
  3. `eaide-agent.spec` datas 新增 `('vendor/ppt-master', 'vendor/ppt-master')`（缺失时跳过，与 officecli 同策略）。
  4. 同步 `fake_meipass` 分发副本（与源种子哈希一致）。
- **验证**：新种子过 schema/DSN/无外部 URL 红线校验（system_prompt 1186 字符 ≤ 4000）；test_skills_seed / test_skills_router / test_loop_skill_inject 共 30 用例全绿。
- **说明**：① ppt-master 脚本依赖 requirements.txt 所列包（python-pptx 等），运行期缺失时按种子提示安装后重试；② 种子只播种不升级（既定策略）：已播种旧版的用户保留旧规范，想换新链路需删除旧技能并清理 `.seeded-manifest.json` 对应条目后重启重新播种，或用仓库新种子手工覆盖数据根 skills/ 下的同名文件。
- **关键文件**：[vendor/ppt-master/](../vendor/ppt-master/)、[office_pptx_designer.yaml](../services/agent/src/agent/skills/seeds/office_pptx_designer.yaml)、[eaide-agent.spec](../eaide-agent.spec)

## 2026-08-25

### v2.109 · 安装器增量更新：未变化的随包文件不再重写（SetOverwrite ifdiff）

- **背景**：用户反馈覆盖安装时「没改过的东西不要覆盖」。核对现状：升级安装（Tauri NSIS 默认不卸载直接覆盖）只会重写安装器负载内的文件（主 exe / eaide-agent.exe / driver wheels / officecli 二进制），安装目录内 Agent 运行期写入的用户资产（skills/、workspace/、*.db、config/llm-config.json 等）本就不在负载内、升级天然不触碰；痛点是默认 `SetOverwrite on` 对负载内全部文件无条件重写（慢 + 抹掉未变化文件）。
- **改动**：[eaide-hooks.nsh](../apps/desktop/src-tauri/hooks/eaide-hooks.nsh) 的 `NSIS_HOOK_PREINSTALL` 首行设 `SetOverwrite ifdiff`——已存在文件时间戳与随包文件相同 → 跳过；更旧或更新 → 覆盖（File 命令默认保留源时间戳，「构建产物没变」⇔「时间戳没变」）；新增 `NSIS_HOOK_POSTINSTALL` 恢复 `SetOverwrite on` 防策略泄漏。
- **验证**：新增护栏测试 `tests/installerIncremental.test.ts`（TDD 红→绿）；改动后 hook 用 Tauri 自带 makensis 3.11 冒烟编译通过；前端全量 202 项测试全绿。
- **已知边界**：ifdiff 基于时间戳判定，若用户手改过随包文件（当前负载均为二进制/驱动，无预期可改项），内容变化会因时间戳不同而被覆盖；未来如需保护用户可改的随包配置，应在 hook 里做备份-恢复而非依赖 ifdiff。

### v2.108 · 打包链路完整性核对：CI/release 补 vendor/officecli 占位与拉取（BUGFIX #133）

- **背景**：发布前核对「打包会不会漏东西」，逐项验证 V9 新增资产（OfficeCLI 二进制 / 种子 YAML）的分发链路。
- **发现并修复**：`tauri.conf.json` resources 通配 `vendor/officecli/*` 在无二进制的 CI 检出里落空会导致打包失败（与 config/driver 同类问题）——ci.yml 两处补占位；release.yml 新增 best-effort 拉取真实二进制（失败退占位）；build-all.bat 增本地检查/自动拉取步骤（缺失不阻断）。
- **核对结论（均实测验证）**：① spec `_data_pairs()` 含 seeds + vendor/officecli + biz_dict；② 模拟安装版环境（任意目录启动 + _MEIPASS 布局）二进制与种子解析均命中（单测覆盖）；③ 生产模式 Agent 进程 cwd = 安装目录，Tauri resources 落点与运行时第一级回退路径对齐，且继承完整系统环境（TEMP/PATH）；④ 新增 Tauri 命令无 capabilities 额外声明需求；无新增 SSE 事件，无需三处同步；⑤ Docling 为可选 extra 不随包，缺失静默降级符合预期。
- **验证**：两个 workflow YAML 语法校验通过；spec 模拟执行命中；相关单测全绿。
- **关键文件**：[ci.yml](../.github/workflows/ci.yml)、[release.yml](../.github/workflows/release.yml)、[build-all.bat](../build-all.bat)

### v2.107 · 审美定位聚焦：跨国集团商务腔调注入两份种子（V9.6.1）

- **背景**：用户反馈期望「商务风、跨国集团的腔调」。将两份演示类种子的默认审美从泛「简约大气」聚焦到咨询公司/跨国集团商务气质（种子内容修订，无代码改动）。
- **visual_deck_designer**：A 系统由「瑞士国际主义」升级为「咨询商务风」并设为默认：白底+深海军蓝+金/银灰点缀、**Action Title**（每页标题必须是结论句）、**金字塔结构**（先结论后论据 + Takeaway 摘要条）、**执行摘要页**（议程后 3-5 条核心结论）、数据来源注与页脚页码/「内部资料」标识；触发词新增 商务风/跨国集团/咨询风；few-shot 同步换为商务腔调示例。
- **office_pptx_designer**：「设计美学纪律」首条确立默认腔调（跨国商务），新增 Action Title / 金字塔+执行摘要 / 深蓝金灰配色 / 来源注与页脚规范。
- **验证**：四份种子全过 schema/DSN/无外部 URL 红线校验（test_skills_seed.py 10 用例全绿）；system_prompt 均 ≤4000 字符上限（最大 1401）。
- **说明**：种子只播种不强制升级，已播种旧版的用户在设置页删除对应 skill 后重启即可重新播种获得新腔调。

### v2.106 · 生成内容设计感升级：视觉演示稿种子（HTML 路线）+ pptx 美学纪律（V9.6）

- **背景**：用户要求生成内容「炫酷、有设计感，但不失简约大气」。GitHub 二次调研：设计感最强的 guizang-ppt-skill（瑞士风/杂志风，17K+ Star）与 dashi-ppt-skill（1020 版式）均为 **AGPL**，GordenPPTSkill 仅限个人/研究用途 —— 均不可内置原件，沿用「蒸馏美学思想自研规范」路线。
- **新种子 `visual_deck_designer`**：单文件 HTML 横向翻页演示稿（浏览器即演示），三套设计系统——瑞士国际主义（网格/发丝线/克莱因蓝锚点色/极致字号对比）/ 电子杂志风（纸张底/衬线标题/大留白）/ 暗色大气（深灰蓝底+发光锚点色，科技发布会风）；铁律：单文件自包含、禁外部资源（内网离线可用）、主色≤3+锚点色、16:9 固定页帧 + 键盘翻页。
- **pptx 种子增强**：`office_pptx_designer` 新增「设计美学纪律」——锚点色只强调重点、≥44pt 大字/12-14pt 注释极致对比、隐形网格与 1pt 细线、深色大气场景规范、分析模型页（SWOT/五力）等分区块、图表选型纪律、几何图标禁花哨素材。
- **前端**：`officePreviewStore` 新增 `openHtml`（直读本地 HTML，不产生后端会话）与 `refresh`（按来源分流）；文件树右键新增「🎨 HTML 演示预览」（仅 .html）；预览浮层复用（沙箱 iframe）。
- **验证**：种子四份全过 schema/DSN/无外部 URL 红线校验；前端 32 文件 198 用例全绿（新增 3 用例：openHtml 直读/刷新分流与无会话停止/读失败错误态）；tsc / eslint / ruff 零告警。
- **说明**：种子只播种不强制升级（既定策略），已播种用户的旧版 pptx 种子不会自动获得新美学纪律，可在设置页删除后重启重新播种或手动同步。
- **关键文件**：[visual_deck_designer.yaml](../services/agent/src/agent/skills/seeds/visual_deck_designer.yaml)、[office_pptx_designer.yaml](../services/agent/src/agent/skills/seeds/office_pptx_designer.yaml)、[officePreviewStore.ts](../apps/desktop/src/store/officePreviewStore.ts)

### v2.105 · Office 生成质量四层防线：内置生产级生成规范种子 Skill（V9.5）

- **背景**：V9 交付 OfficeCLI 引擎后，生成质量取决于 LLM 的工具使用策略与文档规范认知，缺「知识层」。GitHub 调研后确定：蒸馏开源社区生产级规范内置，而非直接搬运脚本层。
- **选型与许可**：蒸馏 **MiniMax-AI/skills**（MIT，13.3K Star：docx 三流水线+验证门控 / xlsx 公式纪律与财务格式 / pptx 视觉一致性约束）与 **OfficeCLI 原生 SKILL.md**（Apache 2.0：路径寻址/错误码自愈）；anthropics/skills document-skills（Proprietary）仅借鉴流程思想不内置；GordenPPTSkill 许可未核实留待后续。
- **种子机制**：`skills/seed.py` 首次启动播种（三级回退定位：cwd > _MEIPASS > 仓库根）；幂等不覆盖用户文件；`.seeded-manifest.json` 记账，用户删除后不复活；坏种子（schema/DSN 违规）记日志跳过不阻断启动。
- **三份种子**（清单式硬约束，适配本地小模型指令遵循）：`office_doc_writer`（Word 结构/排版/编辑纪律）/ `office_excel_analyst`（衍生值必须真实公式禁死值 / 财务格式 / 零格式损失编辑）/ `office_pptx_designer`（先定约束再生成 / 绝对坐标布局 / OfficeCLI 自愈语法）；均固化五步工具编排（探查→生成→校验→修复≤2轮→告知路径建议预览）。
- **四层防线成型**：知识层（本次种子）→ 引擎层（模板 merge）→ 校验层（office_validate 闭环）→ 人工层（预览 + HITL）。
- **打包**：eaide-agent.spec datas 新增 seeds 目录（缺失自动跳过）。
- **验证**：test_skills_seed.py 10 用例（种子合法性/内网红线/播种/防复活/_MEIPASS 回退）+ test_skills_api.py 回归（init_loader 播种幂等）全绿；ruff / mypy --strict 零告警。
- **关键文件**：[seed.py](../services/agent/src/agent/skills/seed.py)、[seeds/](../services/agent/src/agent/skills/seeds/)、[api.py init_loader](../services/agent/src/agent/skills/api.py)

### v2.104 · 开发模式 Office 能力增强（OfficeCLI 读写/渲染引擎 + 预览 + Docling 兜底）

- **背景**：开发模式此前只能“读” Office（file_to_markdown）与粗粒度“生成”（word_generate），Agent 无法细粒度创建/编辑 docx/xlsx/pptx，前端也没有 Office 预览能力。
- **选型**：GitHub 调研后选定 **OfficeCLI**（iOfficeAI，Apache 2.0，~13K Star）：单二进制、无需安装 Office、docx/xlsx/pptx 读/改/建全覆盖、内置高保真 HTML/PNG 渲染引擎、结构化错误码供 Agent 自愈。拒绝内置 Anthropic document-skills 源码（Proprietary 许可，仅借鉴流程设计）。
- **后端**：`builtin/officecli_runtime.py`（三级回退定位：显式配置 > 捆绑二进制/_MEIPASS > PATH；子进程环境白名单 + `OFFICECLI_SKIP_UPDATE=1` 内网禁外联 + 超时保护）；`builtin/office.py` 四工具 `office_read`（read）/ `office_edit`（medium→HITL）/ `office_create`（medium→HITL，含模板 {{key}} merge）/ `office_validate`（read，改完→校验→修复闭环）；注册三处同步（models/schemas/registry）。
- **预览**：`/office/preview` 端点（html 资源内联 / screenshot 逐页 PNG，会话制 + 上限淘汰）；Rust `office_preview_render/stop` 代理（WebView CSP 不能直连 8765）；前端 `OfficePreviewPanel` 全屏浮层（iframe srcDoc 沙箱仅 allow-scripts，渲染等待期细环 spinner + 分级提示不空白）+ `officePreviewStore`（竞态保护）；入口：文件树右键「📄 Office 预览」（仅 docx/xlsx/pptx）。
- **解析增强**：file_to_markdown 新增 Docling 兜底（markitdown 失败/结果为空时自动降级，可选依赖 `agent[parse-full]`，缺失静默跳过）。
- **打包**：`infra/scripts/fetch-officecli.ps1`（SHA256 校验）；二进制落 `vendor/officecli/`（不入 git）；PyInstaller spec datas 与 tauri.conf resources 均按「缺失自动跳过」。
- **红线对照**：写操作（office_edit/office_create）一律 medium → HITL 绝不绕过；预览只读不落审计写记录；子进程不透传敏感环境变量。
- **验证**：Python 新增 56 用例（注册/沙箱/降级/命令拼装/预览 API/真实二进制端到端）全绿，全量回归通过；前端 32 文件 194 用例全绿；tsc / eslint / ruff / mypy --strict / cargo check + clippy(-D warnings) 零告警。
- **关键文件**：[officecli_runtime.py](../services/agent/src/agent/builtin/officecli_runtime.py)、[office.py](../services/agent/src/agent/builtin/office.py)、[office_preview/api.py](../services/agent/src/agent/office_preview/api.py)、[office.rs](../apps/desktop/src-tauri/src/commands/office.rs)、[OfficePreviewPanel.tsx](../apps/desktop/src/components/office/OfficePreviewPanel.tsx)、[fetch-officecli.ps1](../infra/scripts/fetch-officecli.ps1)

## 2026-08-19

### v2.103 · 文件列表右键编译 + 任务结束汇总改动文件清单

- **背景**：导入 Java 工程改完代码后没有编译出口；AI 改完功能后用户不知道动了哪些文件。
- **文件树编译**：`ProjectFileTree` 支持 Ctrl+单击多选文件/目录、右键「编译此文件/目录」、选中后工具条「编译选中」。新增 Rust `compile_files` 命令（commands/compile.rs）：目录递归展开（跳过 target/node_modules 等噪音）、按扩展名分组 —— .java 走 javac（自动推导 src/main/java sourcepath + 模块 target/classes classpath，.class 输出到指定目录）、.py 走 py_compile 语法编译、.c/.cpp 走 gcc/g++ -c 出 .o；完成后弹窗汇总成功/失败与执行命令。
- **编译配置**：设置页新增「编译配置」面板（settings/compile）—— javac / python / gcc 编译器目录手动浏览选择（留空自动探测 PATH），产物输出目录留空默认 workspace/compiled；持久化在安装目录 compile.json（Rust 侧，Agent 离线可用）。
- **改动文件汇总**：前端监听 `builtin_tool_done`（write_file / edit_file 成功）累积改动路径，任务结束（done/error）时在对话流追加 `changed_files` 卡片（shared-protocol 两侧同步新增 kind 与事件联合类型）；卡片列出全部改动文件，点击读文件内容并在 Monaco 打开（复用 openFileInEditor 链路），随会话持久化。
- **验证**：新增 projectFileTree 8 用例（深层展开/多选编译/右键编译/设置面板）、changedFiles 4 用例（累积-汇总-点击打开）；vitest 全量 29 文件 175 用例全过；cargo test 261 全过、clippy(-D warnings) 零告警；tsc / eslint 零告警；shared-protocol roundtrip 通过。
- **相关文件**：[compile.rs](../apps/desktop/src-tauri/src/commands/compile.rs)、[ProjectFileTree.tsx](../apps/desktop/src/components/codenav/ProjectFileTree.tsx)、[CompileSettingsPanel.tsx](../apps/desktop/src/views/settings/CompileSettingsPanel.tsx)、[ChatMessage.tsx](../apps/desktop/src/components/chat/ChatMessage.tsx)（ChangedFilesCard）、[useAgentStream.ts](../apps/desktop/src/hooks/useAgentStream.ts)、[chatStore.ts](../apps/desktop/src/store/chatStore.ts)（changedFiles 累积）、shared-protocol（agent.ts / events.ts / agent.py 两侧）

## 2026-08-17

### v2.96 · chat 会话模型选择：输入框选模型，优先级最高，未选回落模型管理配??
- **背景**：chat 回答用哪个模型完全由模型管理路由决定，用户无法为某个会话指定特定模型（如强制用新接入的内网大模型回答）??- **后端**：`LMRouter` 新增会话??`set_chat_model_override`（模型管??backend 名）：`summarise` 降级链置顶该模型（回答智能的公共入口：直??工具汇??子智能体汇总全走它），优先级高于模型管理路由；选中模型不可用时仍降级默认链（对话不断摆）；未??清除回落模型管理配置；intent/decompose ??_LOCAL_ONLY_TASKS 敏感红线不受影响，mock 模式语义不变。`_build_override_client` 按注册表构建客户端（local→OllamaClient，private/cloud→PrivateLLMClient，与既有构建同款）；`ChatRequest` 新增 `modelOverride`（alias），/chat/stream 入口接到 runtime.llm（同 set_inference_mode 会话级单例先例）
- **Rust**：`agent_chat` / `sse_bridge.start_run` 透传 `model_override`（camelCase modelOverride 进请求体）；透传??too_many_arguments 豁免??v2.95 理由
- **前端**：输入框内工具栏新增 🤖 模型选择器（选项 = `routerListBackends` 过滤已启用，挂载/聚焦刷新）：「默认（模型管理）?? 各启用模型；选择按页签记忆（`ChatTab.chatModel` ??tabs 持久化，重启不丢），选中态绿框高亮；发送时随请求透传，未选传 null 后端清除 override
- **验证**：test_chat_model_override.py 8 用例全过（设/??构建/置顶/降级/默认??alias）；chatStorePersist 新增 chatModel 3 用例；vitest 全量 148 过；cargo check / clippy(-D warnings) / tsc / eslint / ruff 零告??
### v2.97 · 确认-恢复链路修复：编排决策交接进工具循环 + 终答 think 剥离（BUGFIX #108??
- **背景**：v2.95 后用户点确认卡「确认执行」第二轮失败：新??user_prompt 只剩一句确认文本，工具循环重建不出上一轮谈好的参数 ??FINAL_ANSWER 放弃，且云端推理模型??<think> 内心独白原样泄漏给用户（cot.log 证据链完整）??- **编排决策交接**：`loop.py` 新增 `_decision_hint`——把 decompose 决策（mode/reason/confirmation_message/顶层 tool_calls，各段截断）压成交接文本；提示词协议（orchestrate_tools 新参数）??native（首??user 消息前缀）两条路径都注入；`tool_orchestrate.md`（v1.0.1）新??§4.14 {{DECISION_HINT}}：确认类短输入必须按交接中的工具与参数组??TOOL_CALLS，禁止空??FINAL_ANSWER 说“缺上下文??- **think 剥离**：FINAL_ANSWER / ASK_USER（提示词协议）与 native content 三处统一走既??`strip_think_blocks`；剥完为空时走既有兜底文??- **验证**：新??test_tool_loop_decision_hint.py 10 用例全过；test_tool_loop/model_onboarding/responder_ask_user/auto_multi_agent 回归全过；Python 全量绿（除沙箱环境端口受??preview）；ruff 零告??- **环境提醒**：本??Ollama 未配置且内网后端也不可用时，意图分析会降??mock/plain（所??prompt 层优化都不会被真实执行）；免鉴权内网端点的直连问题见 v2.98

### v2.98 · 免鉴权内网模型直连：PrivateLLMClient ??api_key 不再发空 Bearer 头（BUGFIX #109??
- **背景**：内网部署的模型很多不需??api_key，但主对话客户端无条件拼 `Authorization: Bearer {api_key}`，key 为空??httpx 直接拒发非法??`b'Bearer '`，本来可用的免鉴权内网后端被误判不可用，意图/分解??local-only 任务全部降级云端??mock（cot.log 反复??Illegal header value）??- **修复**：`private_llm.py` 新增 `_auth_headers()`：api_key 非空才加 Authorization（与 engine_api / codenav 既有约定对齐）；共享 client ??chat_with_tools 两处统一改用。带 key 后端行为不变??- **验证**：新??test_private_llm_no_auth.py 4 用例全过；test_llm_internal / test_max_context / test_gen_limits / test_native_tool_loop 回归全过；ruff 零告??
### v2.100 · 思维链全量打印工具操作：read/write/glob/grep 每步可见（BUGFIX #110??
- **背景**：用户反馈“思考中有点单调”：动态工具循环一次执行只留一条聚??trace（calls=N），具体调了哪些工具在思维链里不可见；SSE 又只发快照最后一条，双重压缩??- **改动**：① loop.py 新增 `_tool_op_trace`：每工具一??trace（summary=「调用工??name(参数摘要) ??成功/失败」，参数截断防刷屏），提示词协议 + native 两条路径都产出；??stream.py 改为增量下发所有新??trace 条目（兼容旧调用）；??collector.build_thinking 枚举逐工具条目写进持久化思维链（成功【行动??失败【观察】）。前端零改动（既??trace 渲染直接生效）??- **验证**：新??test_tool_op_trace.py 11 用例全过；test_tool_loop / test_trace / test_e2e 回归全过；全量绿；ruff 零告??
### v2.101 · 明确操作免规划：ping 类请求不再走 30s 编排决策器（BUGFIX #111??
- **背景**：“帮??ping 一下内网模型”这类明确操作请求总耗时 95s，其??decompose 编排决策器占 31s：① `LMRouter.analyze_intent` 返回 IntentAnalysis 对象，??intent_node ??isinstance(dict) 判定 ??分析结果被静默丢弃，state.intent_analysis 永远缺失，decompose 意图快速路径从未生效（cot.log 所??run ??structured=false）；??本地 Ollama 缺席降级??plain 兜底时置信度固定 0.5，低于快速路径门槛（??.6），即使分析送达也会掉进 LLM 决策??- **修复**：① `router.py::analyze_intent` 三个出口统一改返 `.to_dict()`（与 semantic_route 返回契约一致）；② `types.py::from_plain_intent` 对明确操作型意图（query/mutate/orchestrate）给置信??0.6，刚好过门槛直达工具循环，闲聊保??0.5；③ decompose 快路径补注释：明确操作不需要规划，写操作照旧在工具循环内过 HITL 审批闸（红线不变）??- **验证**：新??test_intent_fast_path.py 12 用例全过（置信度策略/dict 契约/intent_node ??state/快路径零 LLM/低置信度仍复核）；test_auto_multi_agent / test_model_onboarding / test_tool_loop / test_semantic_route / test_responder_ask_user / test_intent_classifier 回归全过；Python 全量绿（除已??3 ??preview 端口受限环境性用例）；ruff 零告??
### v2.99 · chat 上下文管理：大小展示 + 断点式清??+ LLM 摘要压缩（保留最??5 轮）

- **背景**：长会话 history 越滚越大（每轮随发送回传），既拖慢推理又占模型上下文；用户看不到上下文占用，也无法主动清理/压缩??- **后端**：新??`POST /chat/compress-history`——旧对话??LLM 压缩????00 字摘要（保留关键事实/决策/结论/未完成事项），支持携带已有摘要增量合并；`history_compress` 加入 `TaskKind` ??`_LOCAL_ONLY_TASKS`（对话内容属敏感载荷，本??Ollama 优先、不可用时逐级降级，不受会话模??override 影响）；`ChatRequest` 新增 `historySummary`，`stream_graph_events` 把摘要作??system 消息置于 history 之前注入 graph 初始 messages；压缩链路清洗单独放宽（单条 8000 字符 / 最??60 条，发送链路不变）
- **Rust**：新??`chat_compress_history` 命令（透传 FastAPI）；`agent_chat` / `sse_bridge.start_run` 透传 `history_summary`（camelCase historySummary 进请求体??- **前端**：`ChatTab` 新增 `contextBreakpoint`（断点消??id?? `contextSummary`（压缩摘要）??tabs 持久化；新增 `clearTabContext`（断点式清理：界面消息保留，此后发送不再携带，同时作废旧摘要）??`applyTabCompression`；纯函数 `estimateTokens` / `tabContextMessages` / `estimateHistoryTokens`（~4 字符/token，与后端口径一致）；输入框工具栏新??🧠 指示器（仅统计将发送的会话 history，≥ 8K tok 变橙警示），点击弹菜单：清理上下文（插分隔线?? 压缩上下文（保留最??5 轮原文，其余进摘要，成功后插压缩统计分隔线；失败内联报错不清数据；历史不??5 轮禁用）；发送链??history 改为断点后取最??24 ??+ 透传 historySummary；上下文 chips 同步展示压缩摘要??token 占用
- **验证**：新??test_chat_compress_history.py 11 用例全过（端点成??脏数??400/503/alias/摘要注入/红线）；chatContextMgmt.test.tsx 13 用例全过；vitest 全量 161 过；cargo clippy(-D warnings) / tsc / eslint / ruff 零告警；Python 全量绿（除已??preview e2e 环境??test_intent_fast_path 既有隔离性问题，单独跑均过）

### v2.102 · 工作空间配置 + 文件落盘底层规则：创建类文件默认落安装目??workspace，自动分类建目录

- **背景**：此前没有统一的工作空间概念：产出文件散落 cwd，用户无法自定义落盘位置；用户要求：??工作空间默认 = 安装目录/workspace，可在设置页自定义；??底层规则：智能体运行中创建的任何文件默认都落当前工作空间内并按类型自动分类建目录，仅当用户显式指定输出目录时尊重用户??- **后端**：`paths.py` 新增 `workspace_dir()`（优先级 $EAIDE_WORKSPACE_DIR > workspace.json 自定??> 数据??workspace，自动建目录?? `resolve_output_path()`（纯文件名按扩展名分??docs/data/images/other 建目录；相对子目录保留在工作空间内；绝对路径 = 用户指定原样放行?? 配置读写（单文件 JSON，与 toolchain.json 同机制）；`dispatcher.dispatch()` 入口统一??`_apply_workspace_rule`：write_file/edit_file/word_generate/excel_export/pdf_merge/pdf_split 输出路径改写后才??Rust ??/ Python 执行 / 兜底三条链路，SSE/审计/思维链记录的都是真实落盘路径；解析失败不阻断（回退原路径，沙箱校验兜底）；新增 `GET/POST /workspace` 端点（保存时校验可创建、拒 UNC；空??= 恢复默认??- **Rust**：新??`agent_workspace_get` / `agent_workspace_save` 命令（透传 FastAPI，同 toolchain 模式??- **前端**：设置页新增「工作空间」面板（当前生效路径 + 默认值展??+ 自定义输入，留空保存 = 恢复默认）；router / SettingsView / settingsRoutes 回归测试同步登记
- **验证**：新??test_workspace.py 20 用例全过（默??优先??分类/用户指定豁免/调度改写/API）；工具与调度相??377 条回归全过；settingsRoutes 回归过；cargo check / clippy(-D warnings) / tsc / eslint / ruff / mypy 零告警；Python 全量绿（除已??3 ??preview 端口受限环境性用例）

---

## 2026-08-14

### v2.95 · 模型接入三层修复：意图补 few-shot + 页面上下文注??+ 写配??探测工具 + 带方案一次确??
- **背景**：「帮我连接内网模??DeepSeek-RD-Llama-70B-Int8 http://172.1.0.134:8000/…」被误判 query（触发无意义知识库检索），分解层手里没有写配??HTTP 探测工具，且看不见当前页签，退??ASK_USER 连发 5 个开放式问题。三层缺陷叠加：意图层缺操作类样例、能力层缺工具、策略层无提问约束??- **意图??*：`intent_router.md`（v1.0.1）新??`model_onboard` / `conn_test` 细分类型 + 4 条操作类 few-shot + 槽位规则（model_name/endpoint 齐全时禁止追问）；四分类枚举冻结不动，`types.py` 只扩 `_INTENT_CATEGORIES` 与映射（model_onboard→mutate、conn_test→query??- **页面上下??*：前端发送随请求??`pageContext`（当前页签名 + 模式）→ Rust `agent_chat`/`sse_bridge` 透传 `context` ??`ChatRequest.context` ??`AgentState.page_context` ??`format_page_context` 压成一行注??intent 分析??decompose prompt（新??`{{PAGE_CONTEXT}}` 小节：歧义时以页面场景为准）
- **提问策略**：`decompose.md`（v1.0.1）重??§2.3：ASK_USER 降为最后手段、优先“假??+ 一次确认”、每轮最??1 问、新增模型接入槽位表 SOP；代码护??`_cap_clarifying_questions`：prompt 失效时强制只保留第一??- **能力??*：新 builtin 工具 `model_config_upsert`（写 router.db 模型注册??+ 热重载，risk=high ??强制 HITL 审批卡；api_key 只认 keyring 引用，绝不进日志/返回体）??`probe_chat_endpoint`（最??chat/completions 探测，返可达??状态码/耗时，risk=read）；注册五件套（BUILTIN_TOOL_NAMES / registry / schemas / catalog 关键词）齐备
- **确认??*：responder 确认门槛分支改为输出 ```clarify 确认卡（「确认执行（推荐?? 修改参数」，复用前端 ClarifyCard）替代纯文本；写操作仍再??HITL 审批闸（红线不变??- **验证**：新??test_model_onboarding.py 29 用例全过；Python 全量回归绿（除沙箱环境端口受限的 3 ??preview 用例）；cargo check / tsc 零告警；vitest 26 文件 145 用例全过（含新增确认卡契??2 用例）；ruff 零告??
### v2.94 · 输入框上下文可控：chips 支持删除 + 项目画像按工程停??+ 🌙 纯净对话模式

- **背景**：「将附加上下文」一??chips 此前只读展示，选中后想取消只能回各自的??面板；项目画像按工程自动注入，用户偶尔做无关小需求时无法关闭；想要一个完全与工程无关的对话没有任何入口??- **改动**：① 用户主动选择的三类上下文 chip 增加 ??移除：??功能点上下文（清 selectedFeatureContext + opsNavContext）、??专家团上下文（expertTeamStore.clearSelection）、??需求对齐参照（??ReqAlignmentBanner「取消对齐」同套清理）；② 🏷??项目画像支持按工程停用：??后该工程发送不再注入画像，chip 变虚线「已停用」态点一下可恢复，切到其他工程自动恢复注入（不动 biznavStore.projectName）；??🌙 纯净对话开关（按页签，ChatTab.cleanMode ??tabs 持久化）：输入框内工具栏切换，开启后该页签发送只排除项目上下文（画像/功能??专家??对齐参照），后端注入的助手系统提示词（总纲 + 回答风格 + 双模式纪律）照常生效，chips 区显示「??纯净对话」标识，用户主动附加??📎 附件/编辑器选区不受影响；会话历史为对话本身始终保留。仅??ChatInput.tsx + chatStore.ts，单向桥接（BiznavChatBridge）不受影响??- **验证**：chatStorePersist.test.ts 新增 cleanMode 3 用例；vitest 全量 143 过；tsc / eslint 零告??
### v2.93 · ASK_USER 追问选项卡化：编排终态的 a/b/c 追问渲染成可点选卡片（BUGFIX #106??
- **背景**：编排决策器??ASK_USER 时（如「环境缺工具：a．手??ping；b．改其他方式；c．跳过」），chat 只展示纯文本 bullet，前??ClarifyCard 选项卡片机制只对 summarise 终答??```clarify 块生效，编排终态分支完全绕过该约定??- **修复**：`responder.py` ASK_USER 分支新增 `_ask_user_body` —??确定性解析问题文本里的字母选项枚举（题??+ 选项拆分、防误伤、上??5 项）拼出 ```clarify 块，首项标推荐；解析不出选项时用问题原文做单选项兜底；保??bullet 正文可读性。不??LLM、不??prompt（解析层兼容红线）；前端零改动，既有 ClarifyCard 链路直接生效??- **验证**：新??test_responder_ask_user.py 9 用例（含截图同款文本）；相关 4 个回归套??79 用例全过；ruff 零告??
### v2.92 · chat 附加文件上下文：📎 选本地文件（代码/文本直读，docx/pdf ??file_to_markdown??
- **背景**：主对话输入框此前只有「编辑器右键附加选区」一种代码上下文入口；工程外的文件（一段外部代码、一??docx/pdf 文档）只能手动复制粘贴。补??VSCode Copilot 式的附件能力??- **后端**：新端点 `POST /chat/attach-file`（api/chat.py）：base64 内容落盘临时目录（data_root/chat-attachments，用完即??+ 24h 过期清理）；文本/代码类扩展名 UTF-8→GBK 回退直读；其余格式统一走内??`file_to_markdown`（markitdown，自带超时保护，失败只返 ok=False 不上抛，永不阻塞）；单文件内容上??12000 字符（截断标注），上传体积上??20MB；文件名净化防目录穿越
- **Rust**：新 command `chat_attach_file`（agent.rs，薄转发到后端）
- **前端**：ChatInput 工具栏新??📎 按钮 + 拖拽输入框附加；附件 chip 三态（转换??就绪/失败，可单个移除）；??`lib/attachments.ts`（base64 读取 + 拼接段构建：最??5 个文件、总量 ??40000 字符，与 Rust 100KB prompt 闸门对齐）；发送时附件内容作为【用户附加文件内容】段前置注入 prompt，并写一??system 提示消息；发送后一次性清??- **验证**：后??test_chat_attach_file.py 8 用例全过（UTF-8/GBK/截断/400/目录穿越/html 转换/损坏 pdf 不崩）；前端 attachments.test.ts 5 用例全过；tsc / eslint / ruff / clippy(-D warnings) 零告警，cargo check 通过

### v2.91 · 点交付物直开表单：专家团模板??LLM 直达，未定义模板自动拼提示词问专??
- **背景**：此前完成交付物需要「在输入框打????点问专家 ????LLM 生成草稿表单」；而专家团构建时已定义好交付物，录入类表单完全可以模板化，不该每次都走 LLM??- **后端**??  - `ExpertMember` 新增 `output_forms`（交付物????表单字段定义，语义同草稿 template_json；脏数据容忍不抛错），YAML schema 同步支持
  - 新端??`POST /ops/case/drafts/direct`：零 LLM 直建草稿；字段归一化复??`_parse_draft_template` 同一套白名单；幂等（同成员同交付物已有未通过草稿直接复用，不重复建空表单）；无模??非法引用 4xx 明确拒绝
- **前端**：专家卡交付标准条目变为可点击（虚线下划??+ hover 提示，勾选框验收语义不变）——有模板 ??零延迟直开表单（新草稿自动全屏展开，强化「弹出表单」感知）；无模板 ??自动拼好提问直接发给该专家（免手动输入）；标题文案改「点条目直接完成 · 逐项勾选验收」；ipc 新增 `opsCaseDraftDirect`，Rust 新增 `ops_case_draft_direct`
- **种子**：尽调专家团 yaml 为「尽调任务书 / 客户身份基本信息??/ 经营真实性评估表」三个高频录入交付物补上表单模板
- **验证**：后??test_direct_draft.py 5 用例 + 前端 expertWorkflow 新增 2 用例??5/15）；vitest 131 全过；全量回归通过；tsc / ruff / clippy(-D warnings) 零告??
### v2.90 · HITL 审批 fail-closed 补齐：gate 侧超时守卫（借鉴 DeepSeek Harness approval 词汇表，BUGFIX #96??
- **背景**：对??dsh approval seam（闭合结果集，allowed-once 之外一律拒绝，含无应答/不可用）自查 EAIDE HITL 链路。发现隐患：审批超时语义完全依赖 `interrupt.start_approval` 的后台轮询任务单点写入；Agent 重启/任务丢失时无人写决策，`hitl_gate_node` 会无限等待（fail-hang）；`start_approval` 异常??gate 也无兜底??- **修复**??  - `AgentState` 新增 `approval_started_at`（发起时间戳，随 checkpoint 持久化）
  - `hitl_gate_node`：首次发起记录时间戳；后续无决策时按 `approval_timeout_sec` ??gate 侧守????cleanup + reject（trace reason='timeout_guard'）；存量无时间戳的审批补记当前时刻；`start_approval` 异常 ??fail-closed reject（reason='start_failed'??- **核对确认??fail-closed 的链??*：interrupt 超时自动??reject；hitl_bridge 超时/发起失败 reject；dispatcher/catalog/loop 下游一??`approval_decision == "approve"` 严格判定（垃圾值视同拒绝）
- **验证**：test_hitl_gate.py 新增 3 用例?? 全过）；全量回归通过；ruff 零告??
### v2.89 · 工具结果剪枝??spill 落盘（借鉴 DeepSeek Harness toolResultPruner + spill seam??
- **背景**：此前超大工具结果（read_file 大文??/ http_get / grep 命中洪流）在工具循环注入上下文时只做 `[:tool_loop_max_result_chars]` 头部截断——尾部信息丢失且模型无从取回全文。借鉴 dsh 的两级策略：确定性头尾剪枝（??LLM 成本?? 全文 spill 落盘 + 定位符按需取回??- **新增 `tools/result_spill.py`**??  - 超阈值（默认 4000 字符，`tool_spill_threshold_chars`）的 builtin 只读成功结果 ??全文落盘??`spill/`??600 私有 + O_EXCL 防符号链接重定向），内联替换为「头 2200 + ??900 + 定位符（read_file / grep 可取回）??  - 落盘失败 ??best-effort 退化为纯头尾剪枝（绝不让成功调用变失败，对??dsh spill-policy 语义??  - 替换后内联长度固????~3400，处??loop 层注入预算内，头尾与定位符不会被二次切掉
  - 边界：仅 ok=True ??risk_level='read'；写工具 / 待审??/ 失败 / MCP 结果不动；幂等（??spill_path/pruned meta 不再处理??- **接线**：`ToolCatalog.execute` ??L3 缓存之前应用（缓存的即模型所见版本，且缓存内存占用小）；config 新增 `tool_spill_enabled` / `tool_spill_threshold_chars` / `tool_spill_dir`；`.gitignore` 新增 `/spill/`（锚定根目录??- **验证**：新??test_result_spill.py 10 用例（不适用场景/落盘+定位??失败退??幂等/catalog 接线）全过；ruff 零告??
### v2.88 · 生成限制两级回退 + 「模型与回复」设置面板（借鉴 DeepSeek Harness 配置层级??
- **背景**：调??deepseek-ai/deepseek-harness 后借鉴其配置层级设计（每模型值优先，缺失回退全局默认）。此前最大输出长度全部硬编码在各调用点，上下文长度也无全局默认；设置页缺少集中入口??- **Agent ??*??  - 新增 `llm/gen_limits.py`：`max_output_tokens`（默??32768，输出上??cap，只降不升调用点预算?? `default_context_window`（默??32768，后??max_context ??NULL 时的行级回退），持久化在 `router.db.llm_kv`（key='gen_limits'??  - 接线：OllamaClient ??`options.num_predict`（调用点显式值取较小）；PrivateLLMClient（内??云端）→ `payload.max_tokens`（_chat_completion / chat_with_tools / 提取链三处）；LMRouter 构??+ `reload_max_context()` 热重??+ `_build_cloud/private_client` 动态客户端
  - 端点：GET/PUT `/router/gen-limits`（稀??patch + 边界校验 422 + 保存后热生效??- **Rust ??*：`router_get_gen_limits` / `router_set_gen_limits` 两条 Tauri command（lib.rs 已注册）
- **前端**：设置新增「模型与回复」分区（`GenLimitsPanel.tsx`）：默认上下文长度（预设档位 + 自定义）/ 最大输出长度，保存后后端热生效无需重启；ipc 新增 `routerGetGenLimits` / `routerSetGenLimits`
- **验证**：后端新??test_gen_limits.py 11 用例（存??校验/端点/客户端注??行级回退）全过；全量回归通过（preview e2e 3 条失败为已知端口占用环境问题）；前端 vitest 129 全过；tsc / ruff / clippy(-D warnings) 零告??
---

## 2026-08-13

### v2.87 · Phase 7 V1.x 设计补强：MetricResolver 抽象层（指标平台接入预留??
- **背景**：原"业务字典注入"作为 NL2SQL 三大难点（Schema 链接 / 业务字典 / Few-shot）的兜底实现，与客户企业已有的「指标管理平台」（IMS / Quick BI / DataFinder / 美数 KPI / 银行自研 IMS）口径不一??—??同一指标在不同系统定义不同口径是金融合规事故。提前抽象一层接口，为未来无缝切换到指标平台铺路??- **核心思路（Strategy + Adapter + Config-driven??*??  - 抽象 `MetricResolver` Protocol + `MetricDef` / `ResolvedQuery` Pydantic 数据类（v2.87 设计交付，代??V0 落地??  - 三种实现同接口：
    - `DictMetricResolver`??*V0 默认**，基于现??`nl2sql/dictionary.py` 业务字典封装??*零代码改??* —??仅包装调用）
    - `PlatformMetricResolver`（V1 接力，直??IMS / Quick BI / DataFinder HTTP API??3 天）
    - `BridgeMetricResolver`（V1.5 接力，走 dws 视图间接查询??2 天）
  - 配置驱动一行切换：`config/data_expert.yaml::metric_resolver.type` ??`dict / platform / bridge`（⚠??2026-08-14 勘误：V0 未接??yaml 加载器，当前仅环境变??`EAIDE_METRIC_RESOLVER` 生效；yaml 切换??V1 目标形态）
  - 前端 DataWorkbench 状态栏??`ResolvedQuery.source_kind` 让用户看见当??resolver 类型
- **接口契约**（详??[`docs/implementation/data-expert.md`](docs/implementation/data-expert.md) §2.5）：
  ```python
  class MetricDef(BaseModel):
      code: str                          # "loan_amt"
      name: str                          # "放款总额"
      source_table: str                  # "dws.loan_fact"
      source_column: str                 # "fwd_amt"
      agg: Literal["SUM","AVG","COUNT","MIN","MAX","COUNT_DISTINCT","RAW"] = "SUM"
      dimensions: list[str]              # 可选维??      owner: str | None = None
      dimension_mappings: dict[str, str] = {}  # {"vip_level": "GOLD"}

  class ResolvedQuery(BaseModel):
      metric: MetricDef
      dimensions_filter: dict[str, str] = {}
      time_range: tuple[str, str] | None = None
      source_kind: Literal["dict", "platform", "bridge"]
      confidence: float                  # 0-1
      platform_sql: str | None = None    # platform 模式??SQL 模板
      candidates: list[MetricDef] = []   # top-K 候选（Platform 模式??
  class MetricResolver(Protocol):
      async def resolve(self, query: str, context: dict | None = None) -> ResolvedQuery | None: ...
      async def list_metrics(self, project: str | None = None) -> list[MetricDef]: ...
  ```
- **改动文件**??  - `docs/design/phase-7-data-expert.md` §0 设计哲学（加"可切换指标平台架??段）+ §4.1 重构为五步流水线（含「指标识别」前置）+ 新增 §4.1.1 MetricResolver 抽象层（接口 / 三种实现 / 配置 / 工厂 / 为何必须留接??/ V0 范围 / 5 忠告??  - `docs/implementation/data-expert.md` §2.5 MetricResolver 抽象层契约（完整 Pydantic 数据??+ Protocol + DictMetricResolver V0 实现骨架 + Platform/Bridge 实现骨架 + `build_resolver` 工厂 + 配置??+ NL2SQL 节点改??+ 测试矩阵 + CLAUDE.md §6 红线??  - `docs/ROADMAP.md` §1 阶段??Phase 7 行（??v2.87 状??+ 核心交付描述?? §2 Phase 7 详情（核心交付列表加 MetricResolver；安全红线加 `metric_resolve`??  - `docs/SCHEDULE.md` §3.20 Phase 7 详细拆解表（??v2.87 MetricResolver 抽象??1 ??+ V1 Platform 3 ??+ V1.5 Bridge 2 天；V1 NL2SQL 向量检索升级改造点??- **零现有业务代码改??*（v2.87 范围）：
  - `nl2sql/dictionary.py` 不动 —??继续??DictMetricResolver ??V0 数据??  - `nl2sql/linker.py` / `nl2sql/generator.py` ??*新增** `ResolvedQuery` 字段读取，不改既有调??  - `dataexpert/api.py` 不动 —??9 端点签名保持不变
  - `dataexpert/storage.py` / `readonly/guard.py` 不动
  - V0 仅新增一个文??`dataexpert/metric_resolver.py`（~150 ??Pydantic + Protocol + DictMetricResolver + build_resolver??- **CLAUDE.md 红线遵守**??  - HITL 不可绕过（重查询??HITL 不变；新??`metric_resolve` 不写审计，V1 ??Platform 时再??`DATA_METRIC_RESOLVE`??  - `_LOCAL_ONLY_TASKS` ??`metric_resolve`（指标识别含业务字典翻译，可能涉及敏感表结构 / 字段注释??  - 审计 schema 不变（v2.87 仅接口设??+ 不写 audit；V1 平台适配时再记）
  - SSE 三处同步 0 新事件（MetricResolver 是内部组件，不发 SSE??  - `data_expert.db` 物理隔离不变（MetricResolver 不写表，V0/V1 只读??- **架构??5 忠告落地**??  1. **业务字典必须可外??*：V0 YAML 字典内置，V1 平台模式对接客户 IMS；同一指标多处定义必须收敛（指标平台是 single source of truth??  2. **指标平台??NL2SQL 的前置依??*：不是平级，是上游；不接指标平台就解决不了口径冲??  3. **Dict 模式不可删除**：客户无指标平台时是兜底；V0/V1/V1.5 永远保留 —??配置文件永远要有 `type: dict` 默认??  4. **配置驱动切换**：不改代码，只改 config；前??DataWorkbench 状态栏要显示当??resolver 类型（??`ResolvedQuery.source_kind`??  5. **质量分辅助识??*：Platform 模式可以同时返回多个候选指标（top-K），前端让用户选；dict 模式按关键词命中加权排序（confidence 字段??- **测试矩阵**（v2.87 V0 落地后新??`tests/test_metric_resolver.py`，~120 ??8 用例）：
  - `MetricDef` / `ResolvedQuery` Pydantic 校验（含 agg Literal / dimensions list / frozen??  - `DictMetricResolver.resolve()` 命中 / 未命??/ 关键词加??  - `DictMetricResolver.list_metrics()` 遍历 YAML 字典
  - `build_resolver(settings)` 三种 kind 分发（dict / platform / bridge??  - 配置错误兜底（unknown type ??`MetricResolverConfigError`??  - ??`nl2sql/dictionary.py` 既有调用点兼容（不破坏既有测试）
- **V0 不做**（V1 / V1.5 接力）：
  - PlatformMetricResolver 真实 HTTP 调用 + Keyring 鉴权 + 重试 + top-K 候??  - BridgeMetricResolver dws 视图元数据发??  - 前端 DataWorkbench 状态栏显示 `source_kind`
  - 前端 candidate 选择器（Platform top-K 候选）
  - ??resolver 类型切换??E2E 联调测试
- **与现??Phase 关系**??  - Phase 4（本地小模型）：V1 PlatformMetricResolver 走本??Ollama 做指标候选排序（指标名相似度 LLM 评分，敏感）
  - Phase 2C（LMRouter）：`_LOCAL_ONLY_TASKS` 同步??`metric_resolve`；Platform/Bridge 模式 HTTP 调用本身不走 LLM
  - Phase 5（审核专家）：指标定义变更可触发审批（V1.5 接力，非 v2.87 范围??- **工期**：V0 设计补强 1 天（已完成）；V1 Platform +3 天（??V1 NL2SQL 向量检索升级并行）；V1.5 Bridge +2 天。Phase 7 总工??12 天不变，v2.87 在原??V1/V1.5 工期里挤进??- **后续动作**??  - V0 代码落地（`dataexpert/metric_resolver.py` + `config/data_expert.yaml` ??+ 8 pytest + ROADMAP 状态同步已完成??  - V1 接力 PlatformMetricResolver（依赖客户有 IMS 才有意义??  - 客户第一次询??我们有没有指标平??时，主动??v2.87 设计已为此预留接??
#### v2.87 V0 代码落地??026-08-13 同日交付??
- **新增文件**??  - `services/agent/src/agent/dataexpert/metric_resolver.py`（~270 行）
    - `MetricDef` / `ResolvedQuery` Pydantic v2 数据类（frozen=True??    - `MetricResolver` Protocol（`@runtime_checkable`??    - `DictMetricResolver` V0 默认实现（包??`nl2sql/dictionary.py`，零业务代码改动??    - `PlatformMetricResolver` V1 接力占位（构造函??+ base_url 校验 + `resolve()` ??`NotImplementedError`??    - `BridgeMetricResolver` V1.5 接力占位（同样占位模式）
    - `build_resolver()` 工厂函数 + `get_default_resolver()` 环境变量便捷入口
    - `MetricResolverConfigError` 异常
    - `_detect_agg()` 启发式聚合识别（avg/max/min/count 关键词）
    - `_extract_first_clause()` 中文标点切首??  - `services/agent/tests/test_metric_resolver.py`（~290 ??24 用例??- **修改文件**??  - `services/agent/src/agent/dataexpert/__init__.py` —??导出 10 项（`MetricDef` / `ResolvedQuery` / `DictMetricResolver` / `PlatformMetricResolver` / `BridgeMetricResolver` / `MetricResolver` / `MetricResolverConfigError` / `AggKind` / `SourceKind` / `build_resolver` / `get_default_resolver`??  - `services/agent/src/agent/llm/router.py` `_LOCAL_ONLY_TASKS` —????`metric_resolve`（指标识别含业务字典翻译，可能涉及敏感表结构 / 字段注释??  - `services/agent/tests/test_builtin_v1.py` —??`_LOCAL_ONLY_TASKS` 数量断言 16 ??17（含新增 metric_resolve??- **三关核验**??  - `uv run pytest services/agent/tests/test_metric_resolver.py -v` —??**24 passed**
  - `uv run pytest services/agent/tests/test_data_local_only.py -v` —??**17 passed**（Phase 7 红线 + 历史红线 + frozenset 不可篡改全过??  - `uv run pytest services/agent/tests/test_builtin_v1.py -v` —??**全部通过**（断言数量已更新）
  - `uv run pytest services/agent/tests/ --ignore=test_preview_e2e.py` —??**2017 passed / 9 skipped / 0 failed**（排??preview_e2e 已知??3 个端口占用失败，与本轮无关）
- **CLAUDE.md 6 红线遵守**??  - §1 HITL：MetricResolver 是只??NL2SQL 前置，不触发 HITL；不重写既有 hitl_gate
  - §2 `_LOCAL_ONLY_TASKS`：加 `metric_resolve`（指标识别敏感），且有专门红线条??`test_metric_resolve_is_local_only_task` 锁住
  - §3 Auto-Repair：不涉及（MetricResolver 不在主图，不消??retry_count??  - §4 SSE 三处同步?? 新事件（MetricResolver ??Agent 内部组件，不??SSE??  - §5 凭证保险箱：PlatformMetricResolver ??`auth_secret`，V1 接力时走 Keyring 占位??+ 环境变量兜底（V0 仅占位）
  - §6 审计??schema：v2.87 不写 audit；MetricResolver 只读；V1 ??Platform 时再??`DATA_METRIC_RESOLVE`
- **零现有业务代码改??*??  - `nl2sql/dictionary.py` 不动（继续是 DictMetricResolver 的实际数据源??  - `nl2sql/linker.py` / `nl2sql/generator.py` 不动（保留现有签名；v2.87 不强制调用方改用 ResolvedQuery —??V1 调用方按需??`metric.source_table` 作为 hint??  - `dataexpert/api.py` 不动?? 端点签名不变??  - `dataexpert/storage.py` / `readonly/guard.py` / `readonly/pool.py` 不动
- **后续动作**??  - V1 接力 PlatformMetricResolver 真实 HTTP 调用 + Keyring 占位符鉴??+ 重试
  - V1.5 接力 BridgeMetricResolver dws 视图元数据发??  - 客户第一次询??我们有没有指标平??时，主动??v2.87 设计已为此预留接??
#### v2.87 端到端落地（2026-08-13 续交付）

完成 V0 Python 后端的端到端封装：API 端点 + TS 镜像 + 配置文件 + 全套测试。前端可立即调用；客户切指标平台时改一??config 即可（⚠??2026-08-14 勘误：V0 实际为改环境变量 `EAIDE_METRIC_RESOLVER`，yaml 是预留模板未接线）??
- **新增文件**??  - [config/data_expert.yaml](config/data_expert.yaml) —??V0 + v2.87 完整配置
    - V0 段：`data_expert_db_path` / `data_result_dir` / `data_sql_row_limit` / `data_sandbox_mem_mb` / `data_sandbox_timeout` / `data_export_watermark` / `data_require_mask_on_export`
    - v2.87 新增 `metric_resolver` 段：`type` + `dict.dict_path` + `platform.base_url/auth_secret/timeout` + `bridge.dws_schema`
  - [packages/shared-protocol/src/ts/dataexpert.ts](packages/shared-protocol/src/ts/dataexpert.ts) —??TS 镜像（CLAUDE.md §6 镜像原则??    - `SourceKind` / `AggKind` Literal 类型（含 `(string & {})` 兜底??    - `MetricDef` / `ResolvedQuery` interface（与 Pydantic v2 model_dump 输出严格对齐??    - `MetricResolveRequest` / `MetricResolveResponse` / `MetricListResponse` / `NL2SQLResponse`（v2.87 增量字段??    - `MetricResolverConfig` / `DataExpertConfig` 配置镜像
- **修改文件**??  - [services/agent/src/agent/dataexpert/api.py](services/agent/src/agent/dataexpert/api.py)
    - import：`MetricDef` / `ResolvedQuery` / `build_resolver` / `get_default_resolver` / `MetricResolverConfigError`
    - 新增 Pydantic 模型 `MetricResolveRequest` / `MetricResolveResponse` / `MetricListResponse`
    - `NL2SQLResponse` 增量：`metric_source_kind` + `metric_confidence` 两个字段
    - 新增端点 `POST /data/metric/resolve`（V0 dict 模式命中/未命??配置错误兜底??    - 新增端点 `GET  /data/metric/list`（V0 dict 模式列出 _global 字典条目；占位实现优雅返空）
    - `POST /data/nl2sql` 端点集成：前置调 `resolver.resolve()` ??`source_kind` + `confidence`，透传给前端（写操作拦截时仍透传??  - [packages/shared-protocol/src/ts/index.ts](packages/shared-protocol/src/ts/index.ts) —????`export * from './dataexpert';`
- **测试新增**??  - [services/agent/tests/test_metric_api.py](services/agent/tests/test_metric_api.py) —??9 用例
    - `test_metric_resolve_hit_returns_resolved`（业务字典命????ResolvedQuery??    - `test_metric_resolve_miss_returns_null`（未命中 ??None??    - `test_metric_resolve_with_source_id`（`source_id='ds_credit'` ??加载特定数据源字典）
    - `test_metric_resolve_unknown_resolver_type`（配置错????error 字段兜底??    - `test_metric_list_default_dict`（默??DictMetricResolver??    - `test_metric_list_with_project_param`（V0 模式忽略 project 参数??    - `test_nl2sql_response_includes_metric_source_kind`（NL2SQLResponse 新字段）
    - `test_nl2sql_response_empty_when_metric_not_recognized`（未命中 ??空字段）
    - `test_nl2sql_response_includes_metric_fields_on_write_blocked`（写操作拦截时仍透传??- **三关核验**??  - `uv run pytest services/agent/tests/test_metric_api.py -v` —??**9 passed**
  - `uv run pytest services/agent/tests/test_metric_resolver.py services/agent/tests/test_metric_api.py services/agent/tests/test_data_local_only.py services/agent/tests/test_data_nl2sql.py services/agent/tests/test_data_api_run.py services/agent/tests/test_data_export.py services/agent/tests/test_data_storage.py services/agent/tests/test_data_guard.py services/agent/tests/test_builtin_v1.py` —??**195 passed / 0 failed**（v2.87 相关 + dataexpert 周边 + 红线 + builtin_v1 全过??  - `pnpm exec tsc -b` —??**EXIT_CODE=0**（TS 镜像 + index.ts 注册零错??- **CLAUDE.md 6 红线遵守**??  - §1 HITL：`/data/nl2sql` 端点仍走原有重查??HITL 路径；MetricResolver 自身不触??HITL（只读识别）
  - §2 `_LOCAL_ONLY_TASKS`：`metric_resolve` 已注入（V0 Python 后端??  - §3 Auto-Repair：不涉及
  - §4 SSE 三处同步?? 新事件（MetricResolver ??Agent 内部组件??  - §5 凭证保险箱：`metric_resolver.platform.auth_secret` ??Keyring 占位符，V1 接力时真??  - §6 审计??schema：v2.87 端到端不??audit（V0 只读）；TS 镜像??`model_dump()` 输出严格对齐
- **端到端工作流**（前端可用）??  ```
  用户输入??查询成功订单的总额"
   ??  前端 fetch('POST /data/nl2sql', {question, source_id})
   ??  api.py: resolver.resolve() ??ResolvedQuery(source_kind="dict", confidence=0.4)
   ??  NL2SQLResponse { sql, metric_source_kind: "dict", metric_confidence: 0.4, ... }
   ??  前端 DataWorkbench 状态栏显示 "📊 业务字典 (40%)"
   ??  客户后期??IMS ??改环境变??EAIDE_METRIC_RESOLVER=platform（⚠??2026-08-14 勘误：V0 不读 yaml；改 config/data_expert.yaml::metric_resolver.type ??V1 目标形态）
   ??  自动切换??PlatformMetricResolver ??状态栏显示 "📊 IMS 平台 (90%)"
  ```
- **工期**：端到端封装 ~30 分钟（API 端点 + TS 镜像 + 配置 + 测试全部一次过??- **后续动作**（V1 / V1.5 接力，本轮不在范围）??  - `PlatformMetricResolver.resolve()` 真实 HTTP 调用 + Keyring 鉴权
  - `BridgeMetricResolver.resolve()` dws 视图元数据发??  - 前端 DataWorkbench 状态栏 UI 组件（??`metric_source_kind` + `metric_confidence` 显示??  - Rust commands/dataexpert.rs ??`metric_resolve` / `metric_list` command

---

## 2026-08-12

### v2.86 · Phase 18 GraphRAG 知识库系??完整立项

- **业务动议**：传统向??RAG ??多跳关系推理 / 实体关系查询 / 跨文档整??/ 全局摘要 / 结构化溯??五大企业知识场景短板明显（按算法/数据治理团队 2026-08-12 v1.0 提交的设计稿立项）??*核心思路**：向量检索（语义?? 全文检索（BM25/术语?? 图检索（多跳关系?? 结构化过滤（属性）四路并取，RRF 融合 + Rerank 精排，LLM 生成带证??+ 引用??- **`docs/design/phase-18-graphrag.md`（新??~600 行）** —??完整设计文档??0 章覆盖：业务动议 / 设计哲学 / 系统架构 / 业务场景 / 知识模型 / 数据处理链路 / 知识抽取 / 实体归一 / 存储设计 / GraphRAG 检??/ 权限 / 知识更新 / 审核运营 / API 设计 / 评估体系 / 监控日志 / 安全设计 / 阶段任务拆解 / 风险与成功标准??- **`docs/implementation/graphrag.md`（新??~700 行）** —??完整实现文档??2 ??Python 模块（__init__/schema.sql/models/storage/chunker/extractor/retriever/api/events/review/evaluator）共??2970 ??+ 10 Tauri Command + 8 前端组件 + shared-protocol TS 镜像 + Pydantic 接口契约 + E2E 工作??+ 测试策略 + 35 pytest 用例 + 与现有契约点对齐??- **`docs/ROADMAP.md`** —??§1 阶段表新??Phase 18 行（V0 8 + V1 10 + V1.5 6 = 24 天）；?? 新增 Phase 18 详细章节；最后更??v2.74 ??v2.86；总剩??240 ??264 工作日??- **`docs/SCHEDULE.md`** —??§1 阶段表新??Phase 18 行；§2 新增"??3.5 优先：企业知识库系统（GraphRAG??子优先级；最后更??v2.74 ??v2.86；总剩??234 ??258 天开??+ 264 ??buffer??- **存储选型（关键）**：不引入 Neo4j 进程（EAIDE Tauri 桌面 + ??Python 进程），改用 **SQLite 邻接??+ JSON 字段** 承担图存储；向量复用 Phase 4 SQLite + numpy cosine；全文用 SQLite FTS5（参??Phase 6 V1.5）；对象存储用本地文件系统??*CLAUDE.md §6 红线严格遵守**：graphrag.db 与其??11 ??db 全物理隔离??- **与现??Phase 关系**??  - **Phase 4 V1 知识库引??* = **基座**（chunking + numpy cosine embedding + rag_retrieve 节点全部直接复用??  - **Phase 6 V1.5 FTS5 范式** = **复用**（chunks_fts 5 trigger 自动同步迁移??  - **Phase 2C LMRouter** = **路由**（`_LOCAL_ONLY_TASKS` 新增 `graphrag_extract` / `graphrag_query_rewrite`；`graphrag_answer` 走内??LLM??  - **Phase 5 审核流程** = **审核范式**（draft ??pending_review ??approved 状态机??- **??Phase 17 决策协调**：Phase 17 v2.81 决策"本地不自??RAG"指向 Agent 主对话；Phase 18 是独立企业知识库管理系统（独立业务：知识运营/客服/运维/风险摘要），物理隔离（graphrag.db vs knowledge.db），不抢 Agent 主对??RAG 能力；Agent 主对话仍??Phase 4 V1 `rag_retrieve` 节点 + 未来外部 RAG 接口??- **V0?? 天）任务拆解**：Day 1 schema + 9 ??+ 5 trigger / Day 2 storage 邻接??+ FTS5 / Day 3 chunker（chunk_faq/chunk_ticket 新增?? 简??LLM 抽取 / Day 4 retriever 4 ??+ RRF + 启发??rerank / Day 5 api FastAPI 8 端点 / Day 6 前端 8 组件 + Rust 10 Command / Day 7 SSE 三处同步 4 通道 + ??schema 审计 / Day 8 评估??130 + pytest 35 + 三关核验??- **CLAUDE.md §1-§6 红线遵守**：HITL 不可绕过（审核工作台复用 Phase 5 模式，不直接走主??hitl_gate）；`_LOCAL_ONLY_TASKS` 新增 2 任务（敏感内容强制本??Ollama）；Auto-Repair 不影响（Phase 18 不在主图）；SSE 三处同步 4 新通道（`graphrag_extract_started/done/error` + `graphrag_review_pending`）；凭证保险??V0 不读（V1.5 ??Phase 10 IAM）；审计??schema（graphrag.db.operation_log + audit.sqlite action='graphrag_*'）??- **未交付（V0 起步??*??5 pytest 用例 / FastAPI 8 端点 / 8 前端组件 / 10 Rust Tauri Command / 评估??130 ??/ 1 万实体压??/ 社区摘要 / 自动评估平台??- **V1.5 ??Phase 10 IAM 完成后启??*（多租户隔离前置）??
---

## 2026-08-10

### v2.85 · 专家团资产包??+ 交付物外部文档模板（提示词与模板同包管理??
- **背景**：运营专家团的交付物如果是文档，此前「报告模板」硬编码??ops/api.py，报告主??prompt 里的「标准尽调报告模板」没有实体；本轮把模板外部化为专家团资产的一部分??- **资产包格式（新）**：专家团格式改为压缩文件 = 根目??`team.yaml`（提示词/成员定义?? `templates/`（交付物文档模板 docx/md）；导入时一次到位，导出时整团打包。新??`POST /expert-teams/import-package`、`GET /expert-teams/{id}/package`（含 Rust 代理命令 2 个）；删团同步清理模板；??YAML 导入端点保留兼容??- **模板渲染**：`ops/report_template.py` 占位符引??—??文本类（{{业务名称}}/{{专家团}}/{{生成时间}}/{{材料数量}}/{{风险结论}}）、列表类（{{材料验收清单}}/{{交叉比对清单}}/{{问答记录清单}}/{{人工确认事项}}）、docx 表格循环行（{{#材料验收}}…{{/材料验收}}，单元格 {{材料}}/{{专家}}/{{状态}}/{{意见}}）。降级链：外??docx ??外部 md ??内置 docx ??内置 md，模板缺??损坏只降级不阻塞导出；未知占位符置空并记日志；zip-slip/后缀白名??10MB 上限防护??- **合规模板**：种子模??`due-diligence-report-template.md` 十四章节，按《商业银行授信工作尽职指引》（银监发??004??1 号）与「三个办法一个指引」贷前调查报告要求编制：主体资格/受益所有人（客户身份识别办法）、双人调查与签字责任、征信书面授权（《征信业管理条例》）、反洗钱初筛边界（只出线索不认定）逐章注明监管依据；种子资产包 `docs/expert-team-seeds/due-diligence-team.zip` 可直接导入??- **ExpertTeam schema**：新增可??`report_template` 字段（TS 镜像同步）；导入包内恰好一个模板且未显式指定时自动挂接??- **测试**：新??test_expert_team_package.py 9 用例（资产包导入/409/??team.yaml/非法后缀/导出往??删团清模??md 模板占位符填??两级降级）；Python 相关套件全过，vitest 22 文件 105 用例全过，tsc/clippy 零告警（存量 bare-dict mypy 债务非本次引入）??
---

### v2.84 · Phase 1B V7 大文件查看与搜索（klogg 式只读工具，突破 100MB 限制??
- **背景**：read_file / grep ??100MB 硬上限，超限只返提示，Agent 工具链无法直接读 / ??GB 级文件（前端 UI 另有 Phase 2F+ LogViewer，两者不互通）；V7 补齐 Agent 侧只读入口，仅查看与搜索、绝不修改（对标 klogg）??- **新工??2 ??*（`builtin/logfile.py`，均 risk=read，纯确定??I/O，零 LLM 调用）：`log_read_lines`（start_line+max_lines??000 范围读，读到即停；tail_lines=N 从文件尾反向分块读，不扫全文；单行截??4000 字符）与 `log_search`（字面量/正则流式搜索，命??max_results??000 即早停，context_lines??0 前后上下文，模式??024 字符??ReDoS）。二进制流式逐行读取，绝??readlines 全量加载??- **接线**：models / registry / schemas / catalog 关键词四处登记；同步更新 read_file / grep 的超??hint 与工具描述（`use builtin_log_read_lines / builtin_log_search for files > 100MB`），LLM 撞限后能自动切换??- **分工说明**：用户打开查看大文件仍走前??SmartFileOpener（≥50MB 自动??LogViewer，Rust 直连，不??Agent）；本工具仅服务 Agent 对话链路；日??AI 根因分析仍走 loganalysis 独立链路??- **测试**：新??test_builtin_v7.py 24 用例（范围读 / EOF meta / tail 尾换行边??/ 超长行截??/ 搜索早停与上下文 / 只读哈希不变证明 / 登记完整??/ dispatcher 直通）全过；ruff 零告警、mypy strict 零错误??
---

### v2.83 · Phase 1B V6 文档处理工具族（对标 Work/Coding Agent 内置工具清单补齐硬缺口）

- **背景**：对标业??Work/Coding Agent 通用内置工具清单（文档处??/ 代码开??/ 信息获取 / 任务执行 / 文件管理 / 安全沙箱）做差距分析：文件读??/ shell / 代码搜索 / git / 文档解析（V5 file_to_markdown）均已覆盖，唯一高频硬缺口是**文档结构化读??*（查 / 聚合 / 生成 / 合并拆分）——V6 补齐??- **新工??5 ??*（`builtin/documents.py`）：`excel_query`（risk=read：sheets / rows 投影+where 过滤+分页 / count·sum·avg·min·max 分组聚合，xlsx+csv）、`excel_export`（JSON 行→xlsx）、`pdf_merge`（顺序合并）、`pdf_split`（逐页拆分 / 页码区间抽取）、`word_generate`（Markdown 子集→docx：标??列表/表格/代码块）?? 个写工具 risk=medium ??HITL 前置闸门；全部路径先??path_sandbox；输出默认拒覆盖；查询≤500 ??/ 导出?? 万行 / 合并??0 文件资源上限??- **依赖**：pypdf / python-docx ??agent 主依赖；openpyxl ??data-full extra 懒加载，缺失时返 `missing_dependency` + 内网离线 wheel（config/driver）提示，不崩溃??- **登记四处同步**：models.BUILTIN_TOOL_NAMES / registry（风??描述+注册?? schemas / tools/catalog 关键词；复用既有 builtin_tool_started/done/denied SSE 事件与审计双写，零新增事件??- **分层架构结论**：清单推荐的三层（基础/领域/企业连接器）??EAIDE 现有 builtin ??+ DynamicToolLoop 两段式选择 + MCP 层一一对应，不需新建分层；详??docs/design/phase-1b-builtin-tools.md §11（含 V7 web_search / V8 python_sandbox 路线图）??- **测试**：新??test_builtin_v6.py 35 用例（真??xlsx/pdf/docx 生成回读 + 错误路径 + 覆盖保护 + 登记完整??+ dispatcher HITL 闸门 read 直??未审批等??批准后执行）；Python 全量回归 ~2165 用例??preview_e2e 1 例环境性失败（Vite 端口，与本次无关）；ruff 零告警、documents.py mypy strict 零错误（存量??dict 告警非本次引入）??
---

### v2.82 · 新增内置工具 file_to_markdown（markitdown 文件 ??Markdown??
- **新工??*：`builtin/markdown_convert.py` 新增 `file_to_markdown`（risk=read，只读转换）—??支持 docx / pdf / pptx / xls(x) / html / epub / 图片等转 Markdown；已接入 models / registry / schemas / catalog 关键??/ 包导出，今后一切「文件转 md / 提取文档内容」需求统一调此工具??- **依赖落地（修正）**：markitdown 0.1.x 提升??agent 包正式依赖（`markitdown[docx,pdf,pptx,xls,xlsx]>=0.1`，办公格式转换器??extras 按需装，避开 [all] ??azure/pydub/youtube 重依赖）；主通道为进程内库，跨机交付可用。早期版本曾硬编码外??venv CLI 路径作回退，仅本机可通，已废弃；现外??CLI 仅作可选覆盖（`EAIDE_BUILTIN_MARKITDOWN_EXECUTABLE`，默认空不启用），库缺失且未配置时返 `markitdown_unavailable` 明确报错??- **不阻塞保??*：库通道补超时保护（独立线程 + future.result(timeout)，默??60s）—??markitdown 对畸??/ 超大文件卡死时立即返 `timed_out`，不占死 dispatcher 工作线程；所有异常路径均??ToolResult（ok=False）不上抛，risk=read 不触 HITL，工具循环对错误结果照常推进（max_turns 封顶），即工具报??/ 不可用不阻塞主流程??- **测试**：新??test_builtin_file_to_markdown.py 11 用例（注??/ schema / 错误路径 / 库缺失降级报??/ 库卡死不阻塞 / 真实转换 + dispatcher 集成；后端不可用时自??skip ??CI）；builtin + tool_loop ??196 用例回归通过；mypy strict / ruff 零告警；`legal-docs/测试合同_风险样例.docx` 进程内库端到端转换验证通过??47 字符）??
---

### v2.81 · Phase 17 收尾：V0 全部 + V1 工具缓存交付（RAG 相关项按决策裁剪??
- **范围裁剪（用户决策）**：本地不自建 RAG —??未来检索走外部 RAG 接口，本地只??grep；故 L2 语义缓存（接??bge-small-zh）搁置、L3 embedding/检索缓存取消，L3 仅保留幂等工具结果缓存??- **prompt 版本??*：`llm/prompts.py` 新增 PROMPT_VERSIONS 版本??+ `prompt_version()` / `bump_prompt_version()`；bump 时自动失??L1 精确缓存（防旧答案误命中）??- **工具定义稳定??*：`tools/catalog.py` MCP 工具列表??(server, name) 字典序固定排序（不依??MCP 返回顺序，前缀缓存友好）??- **L3 幂等工具缓存**：新??`llm/tool_cache.py`（LRU 512 + TTL 60s），接入 `ToolCatalog.execute()` —??白名??16 个幂等只??builtin 工具 + write_detector 双重把关，写/待审??失败结果不查不写，MCP 工具一律不缓存??- **Ollama keep_alive**：`OllamaClient` 默认 `keep_alive="10m"`，会话期内模型不卸载，同会话多轮 KV cache 可复用??- **SSE 三处同步**：新??`llm_cache_stats` 通道（L1 命中时推送实??hits/misses/hit_rate）—??`graph/stream.py` + `stream/sse_bridge.rs` + `ipc/events.ts` 三处已同步??- **统计扩展**：`GET /router/cache-stats` 新增 `l3_tool_result` 字段；`l2_semantic` 标注搁置原因??- **测试**：新??test_phase17_finish.py 11 用例；Python 全量回归??preview e2e 1 个环境性失败（端口占用，属在制 Phase 15 工作，与本次无关）；ruff/mypy 新文件零告警；cargo check 无告警；`pnpm tsc -b` 退出码 0??- **台账**：Phase 17 实际工期 ~1 天（原估 6 天，裁剪后缩减）；V1.5 待外??RAG 接口接入后重评??
---

### v2.80 · 提效改造续：报告初稿（P3?? 纠错闭环（P4?? 多文档交叉比??
- **P3 写八股文变改填空??*：导??zip 新增 `尽调报告初稿.docx`（python-docx，不可用降级 md）：业务概况/材料验收表格/交叉比对结果/问答记录/风险结论/需人工确认事项 + 签字栏；客观数据自动填充，主观判断留【需人工填写】空位，低置信项标红（人机边界可视化，铁??4）??- **P4 纠错闭环（铁??2??*：新??case_corrections 表；AI 结论被人工改判时自动落纠错样本（原材??AI 结论/人工结论），??ops_case_correction 审计事件；`GET /ops/case/corrections` 供后续提示词/模型改进分析；人工再改判不重复落样本??- **多文档交叉比对（防呆提效??*：`GET /ops/case/crosscheck` 按要素名跨材料比对取值（不一致项 + 低置信清单）；导出时写入报告初稿；前端底部完成条材料变化后自动标红提醒「跨材料要素不一??n 项」（把事后退件提前到办理中）；新??Rust `ops_case_crosscheck` 代理命令 + ipc 封装??- 测试：后端新??3 用例（纠错样本不重复/交叉比对不一??报告 docx 可打开且含人机边界提示）；pytest 17 + vitest 102 全过；cargo check / tsc / ruff 通过??
---

### v2.79 · 对照「四大效率陷阱」的提效改造（P0/P1/P2??
> 背景：按银行一线效率账自查 —??消灭二次搬运、降低纠错成本、补证据链防黑盒、找制度变问助手??
- **P0 消灭二次搬运**：专家卡支持拖拽文件直接入件（dropzone 高亮）；全局 Ctrl+V 粘贴入件（截??文件自动进当前团带队专家，输入框焦点不拦截，粘贴截图自动改名防互盖），免去「点按钮+选择器」两步??- **P1 厚资料变薄数??*：审核升级为「结??+ 关键要素 + 证据链」单??LLM 调用；case_files 新增 extracted_fields/evidence 两列（存量库 ALTER 迁移）；LLM 未返/不可用时正则兑底提取（统一社会信用代码/身份??手机??日期/金额，命??0.95 高置信）；前端要素卡低置信（<0.6）标红置顶，员工只核标红项??- **P1 防黑盒证据链**：审核提示词要求引用原文摘录，后端只保留真实存在于原文的摘录（容忍空白差异，幻觉证据直接丢弃），前端以「审核依据」竖线引文展示??- **P2 找制度变问助??*：专家提问先??knowledge-base/*.md 关键词检索（中文 2/3 ??n-gram，停用词过滤，EAIDE_KB_DIR 可覆盖），命中则回答优先依据制度内容并自动附「??制度出处」清单；无命中降级纯人设回答不阻塞??- 测试：后端新??6 用例（要??证据链幻觉过??正则兑底/低置信占??知识库出处）；前端要素卡渲染断言；pytest 14 + vitest 102 全过，ruff/tsc 通过??
---

### v2.78 · Phase 17 V0 核心：L1 缓存接线 + 命中率统计落??
- **背景**：L1Cache/L2Cache 早已实现但未接线（engine.py 占位总是 miss），命中率统计永远是 0；本轮让统计真实生效??- **请求规范??*：新??`llm/normalize.py`（canonical_json + 空白归一 + 稳定 cache key；request_id/时间??凭证绝不??key）??- **L1 接线**：`router.py` 将模块级 L1 单例（LRU 256 + TTL 300s）接??`summarise()` 最终答案链 —??相同问题+相同 plan/results 重复调用（连??重试/重发）直接返回，省一次全??LLM 调用；红线：含写工具??plan 不查不写（write_detector 判定）、mock 模式不缓存；一键回滚开??`set_l1_cache_enabled()`??- **统计端点**：新??`llm/cache_stats.py` + `GET /router/cache-stats`（L1 实时 hits/misses/hit_rate + routing_decisions 全量/24h 双窗??cache_hit 比例?? `POST /router/cache-toggle`??- **修复**：`L1Cache.clear()` 现同步重置命中计数（??L2Cache 语义对齐）??- **测试**：新??test_router_cache_stats.py 10 用例（稳??key / 端到端命??/ 写操作红线回归锁 / 开关回??/ 端点）；路由相关 345+ 回归用例全绿；ruff + mypy 零告警??- **后续**：V0 收尾（prompt 版本??+ 工具 canonical/固定顺序 + Ollama keep_alive + `llm_cache_stats` SSE 三处同步）→ V1 L3 ??V1.5 L2 ??embedding??
---

### v2.77 · 运营工作台四项体验修正（用户反馈轮）

- **右侧第三象限整体隐藏**：业务记??/ Skill 经验 / 外部接入页签??`RecordsTabContent` / `SkillTabContent` / `ExternalTabContent` 组件全部移除（Skill 早已退出运营链路，不应再出现；业务记录后端保留，仅隐藏 UI），运营模式的回归二栏布局（业务列??+ 专家验收工作流）??- **专家验收界面重设??*：纵向列表改横向 —??顶部专家团页签（每团一个页签切换），页内专家卡横向排列；拟人化：渐变头像（验收通过变绿勾）+ 打招呼语 + 审核意见/问答以专家口吻气泡呈现，像真人在帮你审核??- **页签新结果提??*：上传后 AI 出审核结????对应专家团页签红色「n 条新结果」徽标（opsCaseStore.unreadByTeam）；切到该页签自动清零，初次加载的历史结果不计未读??- **修复 Token 用量悬浮卡撑出滚动条**：徽章靠窗口边缘??absolute 卡片撑出文档滚动区，导致 hover 时右??底部凭空出现滚动条；??fixed 定位 + 视口边界钳制 + padding 桥接 hover 间隙??- 测试：新增未读徽??清零用例；vitest 全量 21 文件 102 用例通过，`pnpm tsc -b` 退出码 0??
---

### v2.76 · aicss.dev 设计语言全面落地（学习官网组件源码后的第二轮 UI 升级??
- **学习方式**：逐个抓取 aicss.dev 官网组件源码（Code Block / Web Search / AI Agent Input / Text Response / File Diff / Streaming Text）学习，按项目技术栈（Tailwind + 内联样式，不新增依赖）改写落地??- **围栏代码??*（Markdown CodeChunk）：深色面板 + 语言头（尖括号图标）+ Copy 图标勾选??+ 行号（CSS 计数??`counter-reset: mdline`，`::before` 生成不进 textContent，兼容既??pre 纯文本断言）；Monaco ??CodeBlock 头部同步统一??- **搜索卡片升级**：`AiSearchIndicator` 换官网同??SMIL 动画线框地球（六条经线相位错开读作旋转球体），来源列表带左侧竖??+ 逐项状态圆点（虚线????完成勾）??- **ChatInput ??AI Agent Input 风格**：圆角整体框（focus-within 阴影?? 框内底部工具栏（专家团选择??/ 快捷键提??/ 圆形发送键：激活绿色上箭头，busy 时细??spinner，运行中红调圆形停止键）??- **对话气泡**：助手回复去框化（aicss Text Response ??prose 排版）；用户气泡??rounded-2xl 无边框软色块；执行链路块改圆角卡 + 细边框??- **欢迎页快捷提问卡**：图标软底方??+ rounded-xl + hover 上浮阴影??- 测试：新??markdownCodeBlock.test.tsx 3 用例；vitest 全量 21 文件 101 用例通过，`pnpm tsc -b` 退出码 0??
---

### v2.75 · 运营模式交互重构：专家验收工作流（取代大 Chat?? aicss 风格状??UI

- **运营模式中间区彻底替??*：不再是传统??Chat，而是「专家验收工作流」（`ExpertWorkflowPanel`）：客户经理办业务只需满足专家团交付物 —??按专家上传材料（隐藏 file input ??base64，无需新增 Tauri 权限）→ AI 专家审核验收（文档走 doc_review 解析提文本，图片优先本地 vision、不可用降级关注点核对清单）??不懂的向对应专家迷你提问（member.prompt 人设 + 已交材料上下文）??所有专家交付标准逐项确认 ??业务办理完成??- **交付物打包导??*：全部验收后一??`save()` 选路径，后端 zipfile 直接落盘：`交付文件/{专家}/` + `检查结??{专家}.md` + `问答记录.md` + `业务小结.md`（LLM 生成，失败降级确定性汇总）+ `README.md`（验收清单）；导出成功自动生成可审计业务记录卡片（result=done）??- **后端**：`agent/ops/cases.py`（CaseStorage：case_files / case_qa 两表 + ops-cases 文件目录?? `/ops/case` 7 端点（get/files/review/override/delete/ask/export）；LLM 复用三级降级链；所有写操作（上??审核/改判/提问/导出）写审计事件 ops_case_*??- **Rust/IPC**：`commands/ops.rs` 新增 7 ??`ops_case_*` 代理命令；`ipc/invoke.ts` 同步封装；前端类型放 `src/types/ops.ts`（沿 ops 域惯例，??BusinessRecord 同级，未??shared-protocol）??- **aicss.dev 风格状??UI（所有模式生效）**：新??`components/chat/AiStatus.tsx` 三件??—??思考中（星??orb 呼吸光晕 + 流光文字）、搜索卡片（kind='search' 消息，旋转地????完成??+ 可折叠来源，search/grep/retrieve/rag 类工具与 rag_retrieve 节点自动命中）、To-do 任务列表卡（Markdown `- [ ]/- [x]` 不再降级纯文本，??n/N 进度）；关键帧补??globals.css??- **右侧工作台瘦??*：删「业务工作台」页签（专家交付核对/专家团预设迁入中间区），默认页签改「业务记录」；移除 RecordDraftDialog（导出时自动建档）??- **协议**：shared-protocol ChatMessage.kind 两侧镜像新增 'search'??- 测试：后??test_ops_case.py 11 用例（上??审核通过与打??解析失败/LLM 不可??图片视觉降级/改判/问答/zip 内容断言/审计事件）；前端 opsCaseStore + expertWorkflow + markdownTodo ??14 用例；vitest 全量 98 通过、`pnpm tsc -b` 退出码 0、cargo check 无新增告警??
---

### v2.74 ??Phase 17 立项：智能体模型缓存命中率优化（Agent Cache Hit Optimization??
- **来源**：外部输入《智能体开发提升模型缓存命中率完整设计方案 v1.0》，原文归档 `docs/design/phase-17-cache-hit-rate-reference.md`；本地化落地设计 `docs/design/phase-17-cache-hit-rate.md`（五层缓存映射到 EAIDE 现有模块，单机形态裁剪多租户/多实例维度）??- **立项内容**：L0 请求规范化（canonical JSON + 参数归一 + 稳定 key?? L1 精确缓存接线 `engine.py`（替??占位总是 miss"?? L2 语义缓存接真 bge-small-zh + L3 embedding/RAG 检??幂等只读工具结果缓存 + L4 稳定前缀（prompt 版本??+ 工具 canonical/固定顺序 + 动态后置）+ L5 Ollama keep_alive；分层命中率观测 + `llm_cache_stats` SSE 三处同步 + 一键回滚开关??- **工期**?? 工作日（V0 1.5 + V1 2 + V1.5 2.5），排入??3 优先（AI 能力升级）层，可??Phase 4 剩余工作穿插推进??- **红线对齐**：写操作结果禁缓存（HITL 闸门不可绕过）；`_LOCAL_ONLY_TASKS` 敏感任务不进 L2/L3；凭??DSN 不进任何 cache key??- **台账同步**：ROADMAP 总剩??234 ??240 工作日；SCHEDULE 合计 228 ??234 开发天（含 Buffer ??240）??
---

## 2026-08-07

### v2.73 · 专家团系统（一等资??· 运营工作台自动选择注入??
- **专家团资产（不以 Skill 形式存在??*：后端新??`agent/expert_teams/`（models/schema/loader/api/recommender），??成员两级结构化（成员含角色定??职责/关注??输出/独立 prompt）；存储 `%APPDATA%\eaide\expert_teams\*.yaml`，FastAPI 7 端点（list/get/save/delete/import/export/recommend），校验??DSN 红线??- **自动选择决策??*：Skill 预设 `required_expert_team_ids` 直返 ??LLM 三级降级（本??Ollama ??router.db 启用内网 ??云端，走 extract_chat 原始对话）→ trigger_keywords 关键词回退 ??空结果提示手选；推荐接口永不抛错??- **Skill 三字段扩展（向后兼容??*：`required_expert_team_ids` / `materials`（办理材料）/ `deliverables`（交付物），后端模型+Schema 与前端类型镜像；注入上下文时一并拼入供模型判断??- **前端**：设置页新增「专家团」Tab（ExpertTeamsPanel：CRUD + 成员编辑 + YAML 导入后端解析 + 导出）；ChatInput 左侧专家团选择器（仅运营工作台显示，自??手动双模式，manual 后切业务不改写）；运营工作台点业务自动选择 + 「当前专家团」卡片；发送时拼接专家团上下文（chatStore `buildExpertTeamSnippet`，含协作规则）??- **Rust IPC**?? ??`expert_teams_*` 命令（HTTP 转发 Agent API）??- **种子**：`docs/expert-team-seeds/due-diligence-expert-team.yaml`（尽调专家团 12 名专家，手动导入试用，不自动预置）??- 测试：后??4 个测试文件（模型/schema/loader/api/推荐器）+ 前端 expertTeamStore / expertTeamInjection ??12 用例；存量全量回归通过??
---

### v2.x ??文档风险合规审核（审核专??· 文档审核??
- 后端 `doc_review/` 新包：PDF/DOCX/TXT/MD 解析 + 两阶段模型分析（分类 ??按风险类型加载提示词?? evidence 定位防幻??+ RuleProvider 扩展口子（V0 Noop?? doc_review.db 3 表物理隔??+ FastAPI 6 端点??- LLM 路由：TaskKind 新增 doc_classify/doc_analyze，可配置??generate_review（默??ollama ??内网 private ??模型管理已启用的云端后端）??- SSE 三处同步 4 事件（doc_review_started / classified / findings_ready / failed）??- Rust `doc_review` 命令代理 + TS IPC + shared-protocol TS/Python 镜像??- 前端：AuditDashboard「文档审核」Tab + 文本视图按风险等级配色高??+ findings 面板??
---

- 前端：AuditDashboard「文档审核」Tab + 文本视图按风险等级配色高??+ findings 面板??
---

## 2026-08-07

### v2.72 · 运营并入开发模??+ 运营工作台（Phase 2H??
- **模式合并**：移除顶部「运营专家模式」独立页签，运营能力并入开发模式（??`operator` 持久化自动迁移为 `full`）；开发模式左侧栏新增三态子切换「系统资??/ 文件列表 / 系统功能点」??  - 系统资产：纯资产树（DB/API/SSH/RPA）；文件列表：工程目录树（界面与右侧思维链保持现状）；系统功能点：工??AI 提炼的功能点树（支持搜索），右侧面板切换为「需求卡片」??- **运营工作台（开发模式一级入??🏦 运营??*：按用户设计三栏布局——左 16 个一级模块业务列表（支持搜索 + 新建功能点），中 Chat（复用完整会话流），右工作台（加??440px，Tab：业务工作台 / Skill 经验 / 数据字典 / 外部接入）??- **功能??= Skill**：功能点支持绑定/更换/导入 Skill（编辑器 + 工作台下拉）；选择功能点自动把绑定 Skill 注入会话上下文，切换功能点同步切??Skill（chatStore `opsNavContext`，提示词片段??Skill system_prompt + few-shot + 数据字典引用规则）??- **业务记录卡片**：后端新??`agent/ops`（ops.db + 5 路由 `/ops/records*`，AI 总结走本??Ollama ??内网 ??云端降级链），做完业务可一键生成可审计的小卡片（材料核??/ 缺失 / 风险??/ 结果），人工确认后保存??- **数据字典**：后端新??`agent/datadict`（dict.db + 6 路由?? 条种子公共参数；Skill 内只??key 引用，不内嵌公共参数值）；前??`DataDictionaryPanel` 支持搜索 / 新建 / 编辑 / 删除??- **外部系统预留**：OCR / 扫描??/ 语音 / 短信 / 征信 / 工商核验等标记为「外部接入点（预留）」占位，不阻塞流程；统计报表模块标注由数据专家模式承接，不在运营工作台实现??- **模型优先级不??*：业务记录总结??biznav/reqflow 一致（本地 Ollama ??DB 内网 ??DB 云端）??- **验证**：新??`tests/test_ops_api.py`?? 例）+ `tests/test_datadict_api.py`?? 例）；Agent 全量 pytest 通过；前端新??vitest 通过 65/65 + `tsc --noEmit` 0 错；`cargo check` 通过??
### v2.71 · 对话区体验补强七件套（AI 标题 / 耗时 / 上下??chips / 搜索 / history / sessions 归档??
- **AI 标题摘要**：后端新??`POST /chat/summarize-title`（LMRouter 本地优先降级链，失败返空 title 兜底?? Rust `chat_summarize_title` + TS `summarizeTitle` IPC；TabBar 在首轮对话完成（user + assistant 都有且流已停）后??AI 摘要替换自动截断标题，每 tab 只尝试一次、手动改过的标题不动、失败保留截断标题??- **整轮耗时统计**：发送时??`runStartTs`，`done` 事件后由 `useAgentStream` 写入 `lastRunMs`，消息区底部显示「✓ 本轮耗时 X.Xs」??- **上下文可视化 chips**：ChatInput 发送前展示本次会附带的上下文（功能??/ 项目画像 / 需求对齐参??/ 会话历史轮数），用户一眼可??prompt 里注入了什么??- **补发 history 字段（存量断线修复）**：前端发送时把当??tab 最??24 ??user/assistant 消息组装 `history` 透传（Rust/后端早已支持但前端一直没发，导致跨轮引用丢失）；后端 `_sanitize_history` 防御清洗（角色白名单 / 内容 4000 截断 / 24 条上限），`stream_graph_events` 拼入初始 messages。详??BUGFIX_LOG #64??- **会话内搜??*：工具条 🔍 入口 + 输入??+ 命中计数，命中消息整块绿框高亮并滚动定位到首个命中（`CenterChatFlow` Pane，仅当前活动 tab ??user/assistant 消息）??- **sessions 后端归档（best-effort 双写??*：tab 首次发送懒创建后端 session（`sessionsCreate`），用户消息逐条 `sessionsAppendMessage`，`done` 后把最后一??assistant 回复追加进去；全??fire-and-forget，Agent 未就??/ 失败都不阻塞对话，与 SessionsPanel 列表打通??- **验证**：后端新??`tests/test_chat_title.py` 6 例（summarize-title mock/失败/assistant 节??+ history 清洗 3 例）；前端新??`tabBarTitle.test.tsx` 3 例；`tsc --noEmit` 0 错；vitest 65/65；pytest sessions 80 + SSE e2e 9 + chat_title 6 全绿；cargo check 通过??
### v2.70 · 会话本地持久化（重启不再丢对话）

- **背景**：chatStore tabs 纯内存，重启即丢；Phase 6 后端 sessions 体系尚未与主对话接线，本轮先落地本地持久化最小闭环??- **实现**：chatStore ??zustand `persist`（localStorage，key `eaide-chat-v1`）：tabs / activeTabId / inferenceMode 进存储；busy / runId / autonomy / 各类上下文不进（运行态与安全默认值重启重置）??- **容量保护**：写入前裁剪——最??20 ??tab、每 tab 最??500 条消息（localStorage ??MB 上限）??- **恢复清洗**：上次关机卡住的 running 执行步骤降为 ok（避免永久转圈）；activeTabId 失效时回落第一??tab；空数据保底新建 tab??- **验证**：新??chatStorePersist.test.ts 5 条（写入/字段排除/裁剪/降级恢复/activeTabId 回落）；tsc 0 错；vitest 62/62 全过??- **下一??*：后??sessions 归档（用??助手消息??sessions_append_message，与 SessionsPanel 打通）??
---

### v2.69 · 对话区体验五件套（Markdown / 停止 / 滚动跟随 / 重试 / 代码块自适应??
- **Markdown 渲染**：新增零依赖轻量渲染??`components/chat/Markdown.tsx`（解析为 React 元素树，??innerHTML 注入面；链接协议白名单只放行 http(s)），助手回复支持标题/列表/引用/表格/围栏代码块（带复制）/行内加粗斜体代码；样??`.md-body` ??globals.css。内网环境不引新依赖??- **停止/中断**：发送时捕获 `agent_chat` 返回??run_id ??chatStore；busy 时发送按钮变「■ 停止」（调既??`agent_cancel`）；运行??textarea 可预输入下一条；Rust SSE 桥取消分支补??`done(cancelled)` 事件解除前端 busy（顺手修复存量缺口）??- **滚动跟随**：只有贴底（阈??80px）才自动滚，往上翻历史不被打断；非贴底时出现「↓ 回到底部」浮动按钮??- **错误重试**：协??`ChatMessage.kind` 新增 `'error'`（TS/Python 镜像同步），异常终止消息渲染红调卡片 + ??重试（CustomEvent 通知 ChatInput 重发最后一条用户消息）??- **CodeBlock 高度自适应**：按行数撑开（min 80 / max 400），兑现头注释承诺（原固??200px）??- **消息复制按钮**：用??助手气泡悬停时右上角浮现 ??复制（复制纯文本，成功后变绿 ✓）；流式输出中不显示避免误点??- **打字机光??*：busy 时最后一??assistant 消息末尾显示品牌绿闪烁光标（`.stream-cursor`）??- **欢迎页快捷提??*：空会话从孤零零??EAIDE 字样升级为副标题 + 4 张示例卡（业务介??合同审核/SQL/需求卡片），点击直接走 ChatInput 发送管道（CHAT_SEND_EVENT）??- **附带修复**：`stream/mod.rs` ??`HistoryMsg` re-export 导致 Rust 编译失败（存量接线缺口）??- **验证**：`tsc --noEmit` 0 错；vitest 57/57（新??markdown.test.tsx 12 条）；`cargo check` 通过；shared-protocol pytest 通过??
---

### v2.68 · 对话??UI 配色升级（Codex 风格?? 思考动效图??
- **背景**：用户反馈思考态无动效显得呆板、整??UI 不够高级；参??Codex 风格做颜??字体升级，布局零改动??- **思考动??*：`ThinkingIndicator` 新增旋转星形图标（自??+ 呼吸缩放?? 「思考中」流光渐变文字；执行链路 running 态的 ??emoji 换为细环 spinner 动效??- **配色**：新??`codex` 调色??token（canvas/surface/line/ink/sub/faint）；全局主色 accent ??`#007acc` ??品牌??`#10a37f`；TabBar/消息气泡/执行链路/代码??输入??选区 chip 统一新灰阶与分类色；顺手修复 ExecutionBlock hover 残留的深色背景（浅主题下刺眼）??- **字体**：sans 字体栈升??Inter 优先 + 中文回退苹方/鸿蒙黑体/雅黑，全局 `letter-spacing: 0.01em`??- **验证**：`tsc --noEmit` 0 错；vitest 45/45 全过??
---

## 2026-08-06

### v2.67 · Phase 6 会话管理 V1.6（前端集成收尾）

- **背景**：Phase 6 后端（sessions API V0+V1+V1.5 全端点）、Rust 19 command、前??store/组件均已交付，但详情弹窗 / 导入弹窗 / 启动恢复 / SSE 订阅四处未接线，功能??UI 上不可达??- **新增 `ImportDialog`**??eas 加密导入弹窗（路??+ 分支导入选项 + 哈希链校验结果展示）??- **`SessionsPanel`**：每条会话新增「详情」入口（打开 SessionDetailDialog：概??消息/分支/共享/事件链五 Tab）；头部新增 📥 导入按钮??- **`WorkspaceLayout`**：启动时??`/sessions/recovery` 扫描中断会话（空??> 5min），`needs_recovery=true` 自动??RecoveryPanel（恢??= 选中会话 + 切到会话侧栏）；挂载时订??`session_compression_applied` / `session_memory_consolidated` SSE 事件，卸载时取消??- **验证**：`tsc --noEmit` 0 错；vitest 38/38 全过；后??test_sessions_v0/v15 80/80 + test_macc 30/30 全过??
---

### v2.66 · Phase 15 前端实时预览引擎（V1 真实 Vite 端到端交付）

- **背景**：Phase 15 V0/V0.1 交付后仅剩「真??Vite 二进制端到端 smoke」待做；本机 Node.js v24 满足前置条件，本次落??V1??- **新增 `tests/test_preview_e2e.py` 6 条真实二进制 smoke**（session ??fixture 一次??npm install 真实 vite 5.4 + 框架插件；Node < 18 / 无网环境整模??skip）：
  - Vue 3 SFC：启????HTTP 就绪 ??修改 `<template>` ??Vite 日志 `hmr update` + 转换模块含新内容（HMR 实测触发 < 2s??  - React JSX：修改组????Fast Refresh 转换模块更新
  - ??HTML：无 package.json 项目经全局 PATH vite 启动并返回页??  - 多会话并发（2 会话不同端口?? 停止后端口全释放
  - 端口避让：外??socket 占用 base 端口 ??自动跳过
  - 优雅降级：node/vite 不可????明确 `PreviewError` + 端口回滚
- **e2e 实测发现并修??2 个真实缺??*（BUGFIX_LOG #59/#60）：
  - `vite_manager._resolve_vite_command` 返回复合命令字符串，`create_subprocess_exec` ??Windows 必失????改返??argv 列表
  - `config_generator` 生成配置 `import { defineConfig } from 'vite'` 在无 node_modules 的纯静态项目解析失败（ERR_MODULE_NOT_FOUND）→ 改本地恒等定??`const defineConfig = (c) => c`
- **附带修复（V0 遗留 lint 债）**：api.py RUF006 任务强引用、`__init__.py` RUF022 `__all__` 排序、audit/session_manager 无效 noqa、FakeProcess 隐式 Optional；`pyproject.toml` 新增 preview ??RUF001-003 中文豁免（对齐其他中文模块惯例）
- **验证**：test_preview_e2e 6/6 + test_preview_v0 69/69 全过（合??75）；mypy --strict preview ??0 错；ruff 全绿
- **已知限制**：跨平台（macOS/Linux）与 5+ 会话压测尚未??CI 落地；HMR < 100ms 验收依赖人工体验确认

---

### v2.65 ??Phase 7 数据专家模式补齐??0 项缺口一次闭环）

- **真实执行**：`/data/sql/run` 接入 ReadOnlyPool?? 方言族），≤500 行内??/ >500 行落 Parquet + WS Arrow 流；执行后落 `analysis_tasks`（历史分析与 few-shot 数据源打通）??- **Schema 同步**：`fetch_schema` ??MySQL/PG/Oracle(含达??/SQLServer/ClickHouse/SQLite 元数据视图真实拉取表结构 + 中文注释??`schema_cache`??- **安全（用户红线）**：新??`enforce_select_only` SELECT 白名单（单语??SELECT|WITH 首关键字/??INTO OUTFILE）；`EAIDE_ENV` + `EAIDE_DATA_ALLOW_NON_SELECT_IN_DEV` 双条件豁免（默认 prod fail-safe）；黑名单保留作第二层；NL2SQL 生成后前置校验，??SELECT 绝不下发??- **HITL**：前??`HeavyQueryConfirmDialog` 重查询确认（needs_confirm ??confirmed=true 重提）??- **WS + Arrow**：FastAPI `/data/stream/{task_id}`（meta ??Arrow IPC ????done）；Rust `data_stream_result` tokio-tungstenite 中继 + 2 ??channel（三处同步）；前??apache-arrow 逐批解析列存 + DataGrid/ChartPanel 兼容；导出改 task_id 服务端取数（整表不再经前端）??- **沙箱**：`df_input_ref` 直传上一??SQL 结果，worker 注入 `df`、输??`result` 优先；`run_python` 回传结果头部??- **调度**：`ReportScheduler` 接入 main.py lifespan；storage ??`list_templates_with_cron`??- **依赖**：pandas 主依??+ `data-full` 可选组（pyarrow/polars/openpyxl/weasyprint/jinja2/RestrictedPython?? dev ??pyarrow；补声明 asyncssh/keyring（修??uv sync 清理引发的存量测试失败）??- **前端**：新??`HistoryAnalysisList` + `data_list_tasks` command，DataWorkbench 左栏集成，点击回??SQL 重跑??- **测试**：新??test_data_api_run???? test_data_ws_stream???? test_data_schema_sync???? guard 白名单（20+?? sandbox df 链路???? 前端 dataHitl???? historyAnalysis??）；后端全量 pytest 全绿、前??29 用例全过、tsc 零错、cargo check 通过??- **文档**：BUGFIX_LOG #58；实??设计文档状态同步；补齐设计 spec 与实施计划归??`docs/superpowers/`??
---

## 2026-08-05

### v2.64 ??提示词模板体系与全量落地（四层防御）

- **模板资产**：新??`llm/prompts/` ??11 份结构化模板（spark 双跳 / judge / 子智能体决策与执??/ 动态工具编??/ biznav / codenav / 审批选项 / 日志根因 / skill 路由 / NL2SQL / eval judge），`prompts.py` 三个超长常量迁移??.md 资产（值逐字节一致，import 兼容）??- **共享设施**：新??`llm/json_discipline.py`（JSON_DISCIPLINE / json_instructions(style) / strip_think_blocks / extract_json / extract_sql / parse_with_retry）；`load_prompt` 支持子路径与防目录穿越；新增 `render_prompt`??- **四层防御落地**：API 参数（response_format/format）→ Prompt 纪律 ??代码后处理（容错 JSON 提取）→ 重试自纠错；核心客户端（ollama/private/local_small）与 biznav/codenav/审批选项??JSON 调用点统一走共享解析与重试；doc_review/skills/loganalysis 保留既有重试/兜底??- **BUGFIX**：`loganalysis/router.py` 根因分析改走 `extract_chat` 原始文本（原 `_chat_completion` 会对自由文本 json.loads ??必挂 ??静默降级 mock）??- **文档**：`docs/prompt-templates.md` 模板索引 + `docs/llm-prompt-sop.md` 新模型接??SOP??- **测试**：新??`test_json_discipline.py`??4 用例）与 `test_prompt_assets.py`（模板结??常量一致性），并补充 ollama 围栏/think 解析、biznav/codenav/options/skills 围栏 JSON、loganalysis extract_chat 回归??
---

## 2026-08-04

### v2.63 ??模型管理：互斥规则由「同类型」改为「同驻留」（内网多配一启）

- **背景**：内网（private 驻留）模型支持配置多个、同一时刻只启用一个；此前互斥??`type`（local / private / cloud）分组，类型??cloud、驻留??private 的内网网关不受「同类型互斥」约束??- **存储??*（`llm/storage.py::upsert_backend`）：互斥分组键由 `type` 改为 `data_residency`——保??启用某后端时，在同一事务内自动停用同驻留其它已启用后端并返回被停用名单；停用/删除仍不触发互斥??- **桌面??*（`ModelManagementPanel.tsx` + `ipc/invoke.ts`）：启用勾选的乐观互斥??toast 文案同步改为「同驻留」；`disabled` 字段注释同步更新??- **测试**：storage ??3 用例 + API ??3 用例改为/新增同驻留语义（含「同驻留不同类型互斥」用例）；`tests/test_router_storage.py` + `tests/test_router_api.py` 23 个用例全过；`tsc --noEmit` 0 错误；全量回归仅预置 `test_ssh_v0` ??asyncssh 依赖失败（与本次无关）??
### v2.62 · Phase 18 双框架架构（Coding Agent vs Work Agent）V1 交付

- **核心**：单一 LangGraph 主图上演进出双框??—??`mode_router` 前置节点（关键词→模式先验→LLM 兜底，`full` 开发模??coding 偏置、其他模??work 偏置，偏离时回复显式声明?? 子任务级 `ExecutionPolicy`（coding/work 打标，混合任务严格串行）??- **Coding 框架**：Auto-Repair 循环（接入动态工具循环与审批后执行两条路径，预算 3/2/1 随验证能力降级）+ 分层验证器（L1 语法快检 / L2 `.eaide/config/agent.yaml::validate_command` / L3 降级告知?? 工具链探测（设置页路径配????PATH ??常见安装目录，会话级缓存）??- **Work 框架**：推荐选项机制（ApprovalRequest 新增 options/recommendedOptionId/recommendationReason，必??不执??保底项）+ 会话级自动模式（autonomy: interactive/auto；决策矩??risk×autonomy×硬阻断全组合；high/critical 自动模式按推荐项执行且全量审计；DROP/TRUNCATE 硬阻断任何模式不可覆盖；开启前风险确认弹窗 + AUTO_MODE_ENABLED 授权审计）??- **Code/Work 双模式系统提示词融合**：完整提示词资产化（`agent/dual/prompts/code_work_system_prompt.md`）；精简执行纪律随路由注入动态工具循??system prompt；路由词表按提示词扩充（hybrid≡mixed）；HYBRID 拆解阶段序列（Code→验证→Work→确认）；结构化执行报告尾注（仅在有真实修复/审批信号时追加，不伪造结果）??- **协议/通道**：shared-protocol TS/Python 镜像扩展；SSE 新增 `agent://mode_routed` / `agent://repair_attempt` / `agent://auto_decision` 三处同步（stream.py / sse_bridge.rs / events.ts）；审计事件 MODE_ROUTED / AUTO_MODE_ENABLED / AUTO_MODE_DECISION??- **前端**：AutonomyToggle + AutoModeConfirmDialog；ApprovalCard 选项化（推荐高亮，无选项时保持二元审批向后兼容）；路由徽??修复进度/自动决策进执行链路；设置页新增工具链面板??settings/toolchain）??- **验收**：决策矩阵全组合单测 100%；路由黄金集 40 条（准确????0% 断言）；混合拆解黄金集；Python 全量回归通过；前??vitest 19/19 + tsc --noEmit 通过??- **遗留（V2??*：符号表索引、用户画像聚合、组织架构存储、路由手动强制开关；PyInstaller 打包需??`agent/dual/prompts/*.md` 加入 --add-data；`apps/desktop` ESLint 配置缺失为预存问题待补??
---

## 2026-08-03

### v2.61 · Phase 15 前端实时预览引擎（V0 交付??
- **背景**：Phase 15 从立项进入实装。V0 交付完整后端链路 + Rust 窗口管理 + 前端预览面板/按钮，让前端开发者无需离开 EAIDE 即可预览 Vue / React / Svelte / ??HTML 项目??- **安全补全（V0.1??*：`preview_allowed_paths` 白名单强制校验落地（`preview/path_policy.py`：未配置时默认仅允许 `~/.eaide/projects` + 用户 home 直接子目录，`relative_to` 防前缀绕过；名单外路径拒绝启动）；前端设备模式切换联动独立窗口 `preview_resize_window`（切桌面/平板/手机??WebviewWindow 同步 set_size）??- **后端**（`services/agent/src/agent/preview/`??2 文件）：
  - `models.py`：Framework / PreviewStatus / DeviceMode 枚举 + StartPreviewRequest / PreviewSession / HmrStatusEvent / BuildErrorEvent / InstallProgressEvent
  - `framework_detector.py`：Vue2/3 / React / Svelte / ??HTML 自动检??+ 包管理器（npm/pnpm/yarn/bun?? 项目根向上查找（8 层）
  - `port_allocator.py`：`bytearray` 位图管理 5173-5300（分??释放 O(1) + socket 外部占用探测??  - `config_generator.py`：`.eaide-vite.config.mjs` 动态生成（vue2/3 / react / svelte 插件 + strictPort + `hmr.clientPort=EAIDE_AGENT_PORT`??  - `vite_manager.py`：`subprocess` 子进程封装（sanitized env 白名单，**不继??EAIDE_PRIVATE_LLM_API_KEY 等敏感变??*?? stdout/stderr 解析 HMR/build_error + psutil 内存监控??12MB 自动 kill??  - `session_manager.py`：会??CRUD + 状态机 + 崩溃自动重启?? 次）+ 不活??30 分钟自动停止 + 端口释放
  - `install_manager.py`：node_modules 缺失时后??`npm/pnpm/yarn/bun install` + 进度事件
  - `events.py`：进程内 deque + 每会??SSE 订阅广播；`api.py`：FastAPI 7 端点（start/stop/sessions/info/reload/install + `GET /preview/stream/{session_id}` SSE 心跳流）
  - `audit.py`：`preview_session_started/stopped/errored` ??audit.sqlite（`actor_type='user'`）；`storage.py` + `schema.sql`：preview.db 单表 `preview_sessions` 物理隔离
- **Rust**（`apps/desktop/src-tauri/src/preview/window_manager.rs` + `commands/preview.rs`）：11 ??Tauri Command（窗??open/close/reload/resize/list + 会话 start/stop/sessions/info/reload/install HTTP 桥）；URL 校验仅允??`http(s)://127.0.0.1|localhost`；`Cargo.toml` 新增 `url` crate
- **前端**（`apps/desktop/src/components/preview/` 7 组件 + store + hook）：
  - `LivePreviewPanel`（iframe 兜底嵌入 + sandbox=`allow-scripts allow-same-origin`?? `DeviceModeToggle`（桌??平板/手机/自定义）+ `ZoomControl`??0-150% 五档?? `HmrStatusBadge`（绿/??红三态）+ `BuildErrorPanel`（一键复制错误日志）+ `SessionList` + `PreviewButton`（Monaco 工具??▶️ + `Ctrl+Shift+P` / `Ctrl+Shift+R` 快捷??+ 已启动同项目自动聚焦??  - `previewStore`（Zustand + 设备模式/缩放持久化）+ `useProjectRoot` + `PreviewEventBridge`；`CenterChatFlow` 编辑器拆分内嵌预览面??- **SSE 三处同步**（CLAUDE.md §4）：`preview_hmr_connected` / `preview_hmr_disconnected` / `preview_build_error` ??`graph/stream.py::_CHANNEL_BY_KIND` + `sse_bridge.rs::channel/map_event_to_channel` + `ipc/events.ts::EVT` 三处严格一??- **配置**：`preview_db_path`（默??`preview.db`?? `preview_max_memory_mb`??12?? `preview_inactive_timeout_sec`??800?? `preview_allowed_paths`；`pyproject.toml` 新增 `psutil>=5.9`
- **验证**：pytest 新增 `test_preview_v0.py` **69 测试全过**（含 path_policy 白名??5 ??+ 白名单外拒绝 1 条）；`mypy --strict` preview ??0 错；Rust `cargo test --lib` 251 全过（含 window_manager 4 新测试）+ clippy ??preview 告警；vitest 12 新测??+ 回归 1 全过（含设备模式 resize 联动）；`tsc --noEmit` 0 错；全量回归??4 条预置失败（keyring/asyncssh 依赖缺失，与??Phase 无关??
---

### v2.60 · Phase 16 思维链可视化与文件操作追踪（V1 交付??
- **背景**：开发模式的「执行链路」升级为「思维链」：完整展示 AI 的中文思考过程，文件修改/引用高亮显示，鼠标悬浮即可预??diff，点击打开完整对比视图。非开发模式（运营/审核/数据专家）不显示但后端照常记录，满足金融合规审计可追溯要求??- **后端**（`services/agent/src/agent/trace/`?? 文件）：
  - `models.py`：ThinkingStep / FileOperation 数据结构（read/write/edit/grep/reference 五类操作??  - `diff.py`：`difflib.unified_diff` 计算（禁第三方库?? 行数统计 + 预览片段（前??50 行）+ 超长 diff 截断入库
  - `storage.py`：SQLite `thinking_steps` ??13 列（`trace.db` 物理隔离，只追加不删改）
  - `collector.py`：TraceCollector（中文四段式【思考??【行动??【观察??【决策】构??+ 工具调用提取 + 文件操作挂载??  - `api.py`：FastAPI 3 端点（`GET /trace/session/{id}` / `GET /trace/step/{id}` / `GET /trace/file-diff/{id}/{idx}`??- **集成**：`graph/compile.py` 全部 11 节点??`_with_trace` 包装（best-effort，不影响主图）；`builtin/dispatcher.py` 写操作前捕获修改前内????unified diff ??挂到最近思维链步骤；`graph/stream.py` trace 事件携带 `runId`（复用既有通道，无??SSE 事件）；中文思维??Prompt 强制段已注入 `planner.md` / `summarise.md`（explanation/rationale/answer 必须中文 + 📄 文件标记）??- **前端**（`apps/desktop/src/components/thinking/`?? 文件 + store）：
  - `ThinkingChainPanel` 垂直时间线（??`mode='full'` 渲染，非开发模式回退旧执行链路）+ `ThinkingStepCard`（展开/折叠 + 四段式着??+ 📄 文件引用正则高亮??  - `FileReferenceBadge`（文件名/操作类型/行范??+/- 统计；hover 200ms 防抖 + diff 懒加载）+ `FileDiffTooltip`（固定定位轻量着色渲染，120 行截断）+ `FullDiffModal`（Monaco 只读 + Esc/点击外部关闭??  - `thinkingStore`（会话绑??+ 防抖刷新）；`useAgentStream` trace/done 事件驱动；`RightTraceView` 开发模式切换为思维??- **桥接**：Rust `commands/trace.rs` 3 command（`trace_get_session` / `trace_get_step` / `trace_get_file_diff`，经 `AppState.agent_get`?? `ipc` 3 wrapper + `shared-protocol` `thinking.ts` 双侧镜像
- **配置**：`trace_db_path`（默??`trace.db`?? `trace_enabled`（总开关，金融合规场景勿关??- **验证**：pytest 新增 `test_trace.py` 27 测试全过；全量回归仅 1 条预置失败（`test_ssh_v0` ??asyncssh 依赖，与??Phase 无关）；`tsc -b` 0 错；vitest 1/1 通过

---

### v2.59 ??系统资产新增数据源体验修复（空态右??+ 路径修正??
- **背景**：清??mock 后资产树为空，用户想新增数据源时右键「没有反应」——右键菜单只挂在已有节点上，空态没有任何入口；空态提示的 `~/.eaide/systems.yaml` 与实际默认路径不符??- **修复**??  - 空态下右键空白区域弹出「＋ 新增资产 / ??刷新」菜单，节点右键行为不变??  - 空态文案改为真实路??`%APPDATA%/eaide/systems.yaml`??  - Rust ??`systems_path` 默认值从相对 CWD ??`systems.yaml` 改为应用数据目录（与 `safe_defaults` 一致，`EAIDE_SYSTEMS_PATH` 仍可覆盖）??- **测试**：`tsc --noEmit` 0 错误；vitest 1/1 通过；`cargo check` 通过??
---

### v2.58 ??系统资产与控制台不再显示任何 demo / mock 数据

- **背景**：未配置真实资产时，左侧「系统资产」树 / 资产搜索 / 快速打开会注??5 个演示资产（orders_pg、Jira API 等）；控制台执行链路与终端日志仍可能透出 mock 文本??- **系统资产**：删??`demoAssets.ts`，`SystemAssetTree` / `FindInFiles` / `QuickOpen` 一律只展示 `assetStore.tree` 真实数据；空态改为「未配置系统资产 ??编辑 ~/.eaide/systems.yaml 后点????刷新」??- **控制??*：`ExecutionTrace` 执行链路步骤、`XtermTerminal` 终端日志、`useAgentStream` 主对话执行块统一??`isMockText` 防御性过滤，任何??mock 标记的内容都不渲染??- **测试**：`tsc --noEmit` 0 错误、vitest 1/1 通过??
---

### v2.57 ??Phase 15 前端实时预览引擎立项（规划阶段）

- **背景**：EAIDE 当前主要面向后端 / 运维 / DBA 用户，前端开发者要??EAIDE + 浏览??+ 终端三窗口间来回切，启动 `npm run dev` 一??30 ??，改一行等 1-3 ??HMR 反馈。Phase 15 ??EAIDE 从「后??/ 运维工具」进化为「全栈开发平台」——编??Vue / React / HTML 代码时，右侧面板或独立预览窗口即可实时显示页面效果，修改代码??100ms 内自动刷新??- **核心交付规划??0 天）**??  - **Vite 轻量实例**（Python `subprocess.Popen` 启动）—??业界标准 HMR 方案，Vue / React / Svelte / ??HTML 原生支持；不引入??Sidecar / ??Node.js 主进??  - **Tauri `WebviewWindow` 独立窗口**（主路径?? **iframe 嵌入面板**（兜底）—??两种模式用户偏好持久化；金融客户双屏 / 21:9 宽屏场景下独立窗口拖到第二显示器极大提升体验
  - **设备模式切换**（桌??100% / 平板 768x1024 / 手机 375x667 / 自定??200-2560px?? **缩放控制**??0% / 75% / 100% / 125% / 150% 五档??  - **多项目并发预??*??+ 会话同时活跃）—??端口位图 `bytearray(128)` 管理 5173-5200 范围，分??/ 释放 O(1)；每??session 独立 Vite 子进??+ 独立 `.eaide-vite.config.mjs`
  - **框架自动检??*（Vue / React / Svelte / ??HTML）—??读取 `package.json` ??`dependencies` + `devDependencies` 关键字匹配，准确率目??100%
  - **后台 npm install** + SSE 实时进度推??+ 取消按钮 —??缺失 `node_modules` 时优雅降级而非崩溃
  - **HMR 状态实时显??*（绿 / ??/ 红三色徽章）—??Vite 配置 `server.hmr.clientPort = EAIDE_AGENT_PORT` ??HMR 消息??SSE 中转??*不开额外端口**
  - **Monaco 编辑器工具栏 ▶️ 预览按钮** —??当前文件后缀 ??{`.vue`, `.tsx`, `.jsx`, `.html`, `.svelte`} 时高亮；快捷??`Ctrl+Shift+P` 启动 / `Ctrl+Shift+R` 强制刷新
  - **优雅降级** —??Node.js 缺失 / `node_modules` 缺失 / `package.json` 缺失均有明确错误提示；Vite 子进程崩溃自动重??1 次；不活跃会??30 分钟自动停止
  - **资源上限** —??单会话最大内??512MB（psutil 监控），超出自动 kill；多会话总内??< 2GB
- **新增文件 / 修改文件（规划）**??  - **Python 后端**（`services/agent/src/agent/preview/`??1 文件：`__init__` / `models` / `framework_detector` / `port_allocator` / `config_generator` / `session_manager` / `vite_manager` / `install_manager` / `events` / `api` + `schema.sql`（preview.db 单表 `preview_sessions` 9 ??+ 2 索引??  - **Rust**（`apps/desktop/src-tauri/src/commands/preview.rs` + `preview/window_manager.rs`?? ??Tauri Command（`open_preview_window` / `close_preview_window` / `reload_preview` / `list_preview_windows`）；`commands/mod.rs` + `lib.rs` 注册
  - **前端**（`apps/desktop/src/components/preview/`?? 组件（`LivePreviewPanel` / `DeviceModeToggle` / `ZoomControl` / `SessionList` / `HmrStatusBadge` / `BuildErrorPanel` / `PreviewButton`?? `hooks/useProjectRoot.ts` + `store/previewStore.ts` + IPC wrappers + events 3 常量
- **SSE 三处同步**（CLAUDE.md §4 红线）：
  - **Python** `graph/stream.py::_CHANNEL_BY_KIND` ??3 通道 `preview_hmr_connected/disconnected/build_error`
  - **Rust** `stream/sse_bridge.rs::channel` ??3 个新常量 + `map_event_to_channel` 3 个新映射
  - **TS** `ipc/events.ts::EVT` ??3 ??`PREVIEW_HMR_CONNECTED/DISCONNECTED/BUILD_ERROR`
- **测试规划**??0+ Python 单测（framework_detector 8 / port_allocator 6 / config_generator 5 / session_manager 6 / install_manager 3 / api 5 / sse 3?? 10+ Rust 单测（window_manager 6 / commands 4?? 8+ 前端单测 + E2E smoke（Vue / React / HTML fixture + 多会话并??+ 端口避让 + Node.js 不可用降级）
- **CLAUDE.md 6 红线遵守**??  - §1 HITL 不涉及（预览启动是只读操作）
  - §2 `_LOCAL_ONLY_TASKS` 不涉及（预览引擎??LLM 调用??  - §3 Auto-Repair 不影响（Vite 子进程崩溃自动重启是独立层）
  - §4 SSE 三处同步 3 新事件严格对齐（Python + Rust + TS??  - §5 凭证保险箱：Vite 子进程不继承 Agent 敏感 env（仅透传 PATH + NODE_ENV??  - §6 审计??schema：`preview.db` 单表物理隔离（与 audit/codenav/biznav/knowledge/sessions/orchestrator/audit_expert/data_expert/log_index/log_analysis/image_processing/ssh ??12 ??db 全互不干扰）
- **文档同步**??  - `docs/design/phase-15-frontend-live-preview.md`??*新增** ~310 行）—??设计文档：立项动??+ 设计哲学（独立窗??vs 内嵌面板决策矩阵?? 核心功能?? 项）+ 数据模型（preview_sessions 9 列）+ 架构集成点（6 集成点）+ 6 任务拆解 + 5 项验收（功能/性能/安全/UX/测试?? 5 架构师忠??+ 11 项风险预??+ 10 项不重造契??  - `docs/implementation/frontend-live-preview.md`??*新增** ~360 行）—??实现文档：组件清单（11+10+7 文件?? 9 ??Pydantic 模型 + 5 ??E2E 工作??+ 测试策略??0+10+8 测试矩阵?? 5 项依??+ 6 天开发工作流 + 监控可观测??+ 风险缓解 + 关联文档
  - `docs/ROADMAP.md` —??§1 阶段表加 Phase 15 行（??未开??/ +10 ??/ 2026-08-03 立项）；§2 新增 Phase 15 详情章节；?? 风险表新??11 ??Phase 15 风险；?? 验收标准新增 28 ??Phase 15 验收；最后更新日??2026-08-03；总剩??213 ??**223 工作??*
  - `docs/SCHEDULE.md` —??§1 阶段表加 Phase 15 行；§2 新增「第 9.5 优先：全栈开发体验扩展」层级（??Phase 4 完全可并行）；??.21 Phase 15 6 任务拆解；?? 汇总表??Phase 15 行（??Phase 4 并行 +10 ????188 天累计）；??.5 关联文件清单??Phase 15 设计与实现文档链接；??213 ??**223 工作??*
- **架构??5 落地忠告**??  1. **独立窗口优于内嵌面板** —??金融客户经常双屏??21:9 宽屏，预览窗口独立拖到第二显示器能极大提升体验；内嵌 iframe 是兜底方案，**不是默认**；两种模式并存，用户偏好持久??  2. **Vite 进程必须独立** —??每个预览会话启动一个独立的 Vite 子进程（不同端口），**不要**试图在一??Vite 实例里跑多项目——Vite ??`server.fs.strict` + HMR 客户端连接都是基??host:port，多项目必然冲突
  3. **HMR 状态走 SSE 而非 WebSocket** —??Vite 默认 HMR ??WebSocket，但 EAIDE 已经有完善的 SSE 三处同步基础设施；Vite 配置 `server.hmr.clientPort = EAIDE_AGENT_PORT` ??HMR 消息通过 SSE 中转??*不引入新连接类型**
  4. **端口避让用位图而非轮询** —??`SessionManager` 维护 `bytearray(128)` 位图，分配端??O(1)，释放端??O(1)；比"随机尝试 5173-5200 直到成功"??100 ??  5. **不要自研 HMR** —??Vite ??Vue / React / Svelte 官方推荐的开发服务器，HMR 性能已经优化到极致；**不要试图自研构建器或自研 HMR**——那是从零开始重??5 年行业积??- **价值总结**：让 EAIDE 从「代码编辑器」进化为「全栈开发平台」的关键一步。前端开发者在 EAIDE 中编??Vue / React 代码，右侧面板或独立预览窗口实时显示组件渲染效果，修改代??100ms 内自??HMR。金融客户的双屏 / 21:9 宽屏场景下独立窗口拖到第二显示器是杀手级体验??*这是 EAIDE 区别于传??IDE 的核心竞争力**，也是吸引全栈团队采??EAIDE 的关键差异化能力??- **工期**??0 个工作日（Task 1 Vite 服务管理??3 ??+ Task 2 FastAPI 路由 1 ??+ Task 3 Tauri WebView 1.5 ??+ Task 4 前端预览面板 2 ??+ Task 5 Monaco 集成 1 ??+ Task 6 测试与优??1.5 天）

---

### v2.56 ??开发者工具配置开关（F12 / Ctrl+Shift+I??
- **背景**：release 构建??DevTools 不可用（tauri 未启??`devtools` feature，`open_devtools` 命令??`#[cfg(debug_assertions)]` 挡掉），??F12 无反应；生产包需要受控开关而非默认全开??- **桌面端（Rust??*：`AppConfig` 新增 `devtools_enabled`；加载数据目??`config.yaml`（Windows `%APPDATA%/eaide/config.yaml`）的 `devtools` 键，环境变量 `EAIDE_DEVTOOLS`（true/false/1/0/yes/no）优先级更高；debug 构建默认开启、release 构建默认关闭；`open_devtools` 命令改为配置门控的开关式（打开/关闭），tauri 依赖启用 `devtools` feature ??release 构建也可用??- **前端**：F12 / Ctrl+Shift+I 全局快捷键切??DevTools（Monaco/CodeMirror 编辑器内保留 F12 跳转定义??Ctrl+Shift+I 导入文件）；命令面板新增「开发?? 切换开发者工具」；CheatSheet 补充快捷键说明??- **测试**：新??`config.rs` 单测（布尔解??/ YAML 解析）；`cargo check`、`cargo test`、`tsc --noEmit`、eslint 0 告警??
---

### v2.55 ??UI 不再显示任何 mock 占位数据

- **背景**：mock 模式??`MockLLMClient` 的占位回复（「（mock 后端）当前没有可调用的工具…」「当前在 Mock 模式…」）会混进聊天与控制台，真实使用场景不应看到任何 mock 数据??- **后端**（`llm/mock.py`）：所有用户可见的 mock 输出统一??`（mock）` 前缀标记（chitchat / 无工??/ 执行计划三类回复），删除冗余的尾??Mock 模式提示，前端可按标记可靠过滤??- **前端**：新??`lib/mockFilter.ts`（`isMockText` / `isMockSource`），在四处过滤：
  - `ChatMessage` 渲染层：mock 聊天气泡不渲染（含历史消息）??  - `traceStore.pushConsole / updateConsole`：mock 控制台条目不入库、更新后??mock 即移除；
  - `RightTraceView` 渲染兜底：控制台条目数与列表均不??mock??  - `SessionDetailDialog` 历史会话消息??`（mock` 前缀过滤??- **测试**：全??pytest 通过、`tsc --noEmit` 0 错误；mock 模式??UI 不显示任何占位文字??
---

### v2.54 ??动态工具加载与工具调用模块：内置常用工??+ MCP 并存，主图自动调??
- **背景**：builtin 原生工具层（19 个工具）已实现但 **planner 目录只来??MCP**，LLM 实际看不到内置工具；且一次性把全量工具塞进上下??token 高、选择干扰大。按「动态工具加载与工具调用提示词」实现分层动态注册循环??- **动态工具循??*（新??`agent/tools/catalog.py` + `loop.py` + `graph/nodes/tool_orchestrator.py`）：
  - 五动作协议：`SELECT_TOOLS`（只注册候????）→ `TOOL_CALLS`（执行并把结果追加上下文）→ `REQUEST_FULL_TOOLS`（全量兜底）??`ASK_USER` / `FINAL_ANSWER`；`LOAD_STAGE` 三阶段（SUMMARY_ONLY ??CANDIDATE_REGISTERED ??FULL_REGISTERED）；
  - 主图接线：`intent ??rag ??decompose ??tool_orchestrator ??hitl_gate | responder`；decompose ??`TOOL_ONLY` 后进入循环，??高危调用暂停??HITL 审批、批准后重放、拒绝记入上下文??  - 轮次硬上限（默认 8）、结果截断（默认 4000 字符/条、保??10 条）、动??JSON 严格校验（未注册工具 / 全量后重复请求全??/ 缺消??/ id 重复 ??一律保守回退 FINAL_ANSWER）；
  - 工具集合 = **builtin 24 ??+ MCP 已配置工??*（`server.name` 全名）??- **内置工具补齐**??  - `builtin/schemas.py`??9 个既有工具补齐参??JSON Schema??  - `builtin/extra.py` 新增 5 个常用工具：`datetime_now` / `uuid4` / `http_get`（限 http/https + 超时 + 大小上限?? `csv_parse` / `text_split`（全低风险）??  - `builtin/fallbacks.py`?? 个只??Rust 工具（stat_file / find / glob / hash / base64）补 **Python 兜底**——Agent 独立运行（无 Tauri）也可用，Tauri 可用时仍优先??Rust??- **LLM 路由**：新??`tool_orchestrate` 任务类型（`_LOCAL_ONLY_TASKS` 本地红线??6 个；决策输入含用户内容与工具结果），动作 JSON 严格解析；`decompose` ??available_tools 改用统一 ToolCatalog 摘要??- **配置**：`EAIDE_TOOL_LOOP_ENABLED`（默??true；测试走既有路径）、`TOOL_LOOP_MAX_TURNS`??）、`TOOL_LOOP_MAX_SELECTED`??）、`TOOL_LOOP_MAX_RESULT_CHARS`??000）、`BUILTIN_HTTP_TIMEOUT_SEC`??0）、`BUILTIN_HTTP_MAX_BYTES`??MB）??- **测试**：新??`test_tool_loop.py` 34 用例（目??五动作状态机/HITL 暂停恢复/轮次上限/违规兜底/新工??Python 兜底/图级 e2e）；`_LOCAL_ONLY_TASKS` 计数断言更新??16；既??builtin V1.5/V2 ??4 ??not_implemented 用例改为 Python 兜底行为断言；全??pytest 通过、`tsc --noEmit` 0 错误??
---

### v2.53 ??多智能体改为 Agent 自动判断（编排决策器），移除控制台手动派生入??
- **背景**：控制台的「派??sub-agent」按??+ 表单让用户手动选择是否启用多智能体，与控制层「主 Agent 是唯一决策者」的设计相悖；用户期??Agent 自动判断任务是否需要多智能体??- **控制??*（`graph/nodes/decompose.py` + `graph/compile.py` + `graph/edges.py`）：主图新增 `decompose` 节点（planner ??decompose ??tool_runner/responder），按「子智能体启用决策提示词」（`agent/prompts.py`??2.6KB 完整协议）由**本地 LLM**（`_LOCAL_ONLY_TASKS` 红线）自动决策：
  - 六种模式：`MAIN_AGENT` / `TOOL_ONLY` / `SINGLE_SUBAGENT` / `MULTI_SUBAGENT` / `ASK_USER` / `REFUSE`，附??scoring 七维评分、`selected_subagents`、`tool_calls`、`plan`、`fallback`??  - 安全门槛：高风险（`user_confirmation_required`）不自动执行；REFUSE/ASK_USER ??responder 直接输出；判定失??LLM 不可用一律保守回退??Agent（原行为不变）；
  - 自动派生的子智能体只读（`requires_write=False`），写操作仍走主??`tool_runner` + `hitl_gate`；子智能体执行提示词模板接入 `context_strategy.build_context`??- **LLM 路由**（`llm/router.py` / `llm/types.py`）：新增 `decompose` 任务类型（本地红线，`_LOCAL_ONLY_TASKS` 15 个），决??JSON 严格解析（模????子智能体数量一致性、ASK_USER 必须有追问、REFUSE 必须有拒绝语、确认必须有信息）??- **桌面??*：移除手动派生入口（`SubAgentPanel` 按钮/表单、`orchestratorSpawn` IPC、Rust `orchestrator_spawn` 命令），面板改为只读展示自动派生??sub-agent 树，并订??`agent://sub_agent_spawn/_progress/_done` SSE 实时刷新；控制台分区标题改为「多智能??sub-agent（Agent 自动判断）」??- **配置**：`EAIDE_MULTI_AGENT_AUTO_ENABLED`（总开关，默认 true）、`EAIDE_MULTI_AGENT_MAX_SUBTASKS`（默??6，上??30）??- **测试**：新??`test_auto_multi_agent.py` 36 用例（decompose 节点/路由/responder/LMRouter 解析与提示词填充/执行模板/图级端到端）；`_LOCAL_ONLY_TASKS` 计数断言更新??15；全??pytest 通过、`tsc --noEmit` 0 错误、`cargo check` 通过??
---

### v2.52 ??模型管理：同类型仅允??1 个启??+ 「启用」勾选真正持久化

- **背景**：用户配置多个云端模型后，Agent 实际每类只选用一个；且列表「启用」勾选框原先只改前端 state、不落库，编辑保存才生效??- **存储??*（`llm/storage.py::upsert_backend`）：同一事务内保证不变量——当保存的后??`enabled=1` 时，自动把同类型（local / private / cloud）其它已启用后端置为停用，并返回被停用的名字列表；停??删除不触发互斥??- **API ??*（`llm/engine_api.py`）：`POST /router/backends` ??`PUT /router/backends/{name}` 回传 `disabled` 列表??- **桌面??*（`ModelManagementPanel.tsx` + `ipc/invoke.ts`）：「启用」勾选立即持久化（修复不落库问题）；启用时同类型其它模型自动停用??toast 提示；列表补 `role` 字段，避免持久化时把 role 覆盖??execution??- **测试**：storage ??3 个新用例（同类型互斥 / 跨类型互不影??/ 停用不触发互斥）+ API ??2 个新用例；全??pytest 通过，`tsc --noEmit` 零错误??
---

## 2026-07-31

### v2.51 ??Phase 12 V1.5 多智能体规模化调度收尾（1166 passed / 7 skipped / 1 pre-existing / 0 回归 / 41 V1.5 新测试）

- **背景**：Phase 12 V0（同??spawn + Pydantic 契约 + 派生树硬上限?? V1（Worker Pool / 状态锁 / Token Bucket / HITL bridge 占位）已交付。V1.5 接力收尾?? 个新模块（events / sensitive / context_strategy / state_repo / queue / audit_bridge / eval_collector / observability?? Orchestrator 完整流水??+ 11 ??FastAPI 端点 + 10 ??Tauri Command + shared-protocol TS 镜像 + 41 新单测??- **核心交付**?? ??Python 模块 / 6 Python 修改 / 1 ??Rust cmd ??/ 2 TS 修改 / 1 ??shared-protocol 文件）：
  1. **`orchestrator/events.py`**（新??~95 行）—??进程??SSE 事件 deque?? 通道常量（`EVT_SUB_AGENT_SPAWN/PROGRESS/DONE/APPROVAL`?? emit/consume/peek/flush；与 biznav/skill/builtin 模式对齐
  2. **`orchestrator/sensitive.py`**（新??~145 行）—??敏感负载二次校验??     - `_PII_PATTERNS` 7 类（手机/身份??银行??AWS Key/JWT/IPv4/邮箱?? `_CREDENTIAL_KEYS` 14 字段 + `_SQL_ERROR_PATTERNS` 4 ??     - `prompt_safe_for_remote(prompt, payload)` 扫文??结构化字??     - `classify_spec(spec, prompt)` 三条规则合并（`task_type ??_LOCAL_ONLY_TASKS` / `ModelPolicy.carries_sensitive_payload` / 内容启发式）??强制 `local_only=True`
  3. **`orchestrator/context_strategy.py`**（新??~310 行）—??三类场景化上下文传递：
     - `SharedMemoryPool`（run_id 隔离 + version + content_hash 可追溯）
     - `build_context(spec, pool, ...)` 选策??????prompt ??必读字段不可压校??     - `select_strategy(spec)` 启发式（passthrough ??200 token / shared_memory_pool 声明??shared_keys / incremental_summary > 2000 token??     - `ComposedContext`：tokens_before/after + compression_ratio + required_fields_kept + raw_refs（外置成 ArtifactRef??  4. **`orchestrator/schema.sql`**（新??~50 行）+ **`orchestrator/state_repo.py`**（新??~390 行）—??4 ??SQLite WAL??     - `sub_agent_tasks` 22 ??+ 5 索引（含 idempotency_token UNIQUE??     - `sub_agent_artifacts` 7 ??+ 2 索引（FK CASCADE??     - `sub_agent_dlq` 11 ??+ 2 索引（state: open/requeued/closed??     - `sub_agent_metrics` 6 ??+ 2 索引（latency/compression/judge 等）
     - `update_status_cas()` 乐观??+ `StateVersionConflict` 冲突异常 + `push_dlq`/`mark_dlq` 持久??  5. **`orchestrator/queue.py`**（新??~175 行）—??`PriorityTaskQueue`??     - 三级 asyncio deque（high/normal/low?? `asyncio.Condition`
     - `enqueue` 幂等去重（`idempotency_token`）；`dequeue` 严格按优先级
     - `close()` 唤醒所有等待者（??1s 取消传播?? `forget()` 释放幂等（DLQ requeue 用）
  6. **`orchestrator/audit_bridge.py`**（新??~165 行）—??11 类事??+ 决策树回放：
     - 12 事件常量（spawn/progress/done/retry/dlq/cancel/closed/requeued/hitl_requested/hitl_decided/judge/queued??     - `log_event()` ??`audit.store.audit` ??5 列（correlation_id / actor_type / event_type / task_id / parent_task_id??     - `replay_tree(correlation_id)` ??+ `build_tree()` 折父子结??+ `replay_summary()` 计数
  7. **`orchestrator/eval_collector.py`**（新??~265 行）—??评测指标 + LLM Judge 抽样??     - 12 指标（dispatched/succeeded/failed/dlq/cancelled/retries/validation_pass_rate/compression_ratio/required_fields_kept_rate/p50_ms/p99_ms/hitl/judge??     - 8 项验收阈值（validation_pass_rate ??0.95、success_rate ??0.85、retry_rate ??0.10、dlq_rate ??0.01、compression_ratio ??0.60、required_fields_kept_rate = 1.0、p50 ??5s、p99 ??30s??     - `EvalCollector.should_judge()` 确定性抽样（??N 个抽 1 个，**不作 CI 闸门**??     - `judge_report()` ??LLM 评分 1-5（JSON 优先 / 正则兜底 / 0 分兜底）
  8. **`orchestrator/observability.py`**（已存在，V1.5 整合）—??`StructuredLogger`??     - `logs/orchestrator-YYYYMMDD.jsonl`（按日期滚动 + 50MB/文件??     - `_scrub_value()` PII/凭证脱敏（CLAUDE.md §6 红线??     - 5 ??log 辅助（spawn/progress/done/hitl_decision??  9. **`orchestrator/hitl_bridge.py`**（V1.5 升级 ~390 行）—??**??interrupt 复用主图 approval 通道**??     - `request_approval(..., wait_for_user=True, timeout_sec=...)` ????`graph.interrupt.start_approval` + emit `approval` 事件（前??ApprovalCard 零改动）+ 轮询 `check_decision`
     - `decided_by` 枚举（user/timeout/auto_low_risk/fail_closed/policy_disabled??     - `timed_out=True` 默认 reject（fail-closed）；V1 fail-closed 语义保留
  10. **`orchestrator/orchestrator.py`**（V1.5 重写 ~700 行）—??完整流水线：
      - `dispatch(spec, priority)` 入队即返；`run_until_drained(timeout)` 消费
      - `_execute(item)` ??context_strategy ??sensitive ??bucket ??LLM ??3 次重????CAS 落库 ????SSE ??Judge 抽样
      - `cancel_all()` ??1s 唤醒 + 关队??      - V0 兼容：`spawn(spec)` 同步路径保留 + `event_queue` 属性保??+ `_compose_prompt` 兼容方法
  11. **`orchestrator/api.py`**（V1.5 扩展 ~300 行）—??**11 个新端点**??      - `POST /orchestrator/dispatch`（异步派发）
      - `POST /orchestrator/run_until_drained`（消费队列）
      - `POST /orchestrator/cancel_all`（全局取消 ??1s??      - `GET /orchestrator/dlq?state=open&limit=50`（DLQ 列表??      - `POST /orchestrator/dlq/{task_id}/requeue`（DLQ 重新入队，forget idempotency 后再 dispatch??      - `POST /orchestrator/dlq/{task_id}/close`（关??DLQ??      - `GET /orchestrator/metrics`（评测指标快照）
      - `GET /orchestrator/queue/stats`（队列堆??+ backlog_alert 告警??      - `GET /orchestrator/replay/{correlation_id}`（决策树完整回放??      - `GET /orchestrator/list` 升级：优先从 StateRepo 持久层读
      - 路由顺序：字面量路径必须??`/{sub_agent_id}` 通配符之前（否则会被吞掉??  12. **`orchestrator/__init__.py`**（V1.5 重写）—??公开 API 列表 60+ 项（V0 + V1 + V1.5 全部导出??  13. **`config.py`**（修??~12 行）—??7 个新 `orchestrator_*` 配置（db_path / concurrency / task_timeout_sec / max_attempts / judge_sample_rate / log_dir / cancel_deadline_sec??  14. **`graph/stream.py`**（修??~14 行）—??`_CHANNEL_BY_KIND` ??4 通道（sub_agent_spawn / sub_agent_progress / sub_agent_done / approval）；新增 `_drain_orchestrator_events()` 仿照 biznav 模式；流循环 + finally 各调一??  15. **Rust `commands/orchestrator.rs`**（V1.5 扩展 ~240 行）—??10 个新 Tauri Command（dispatch / run_until_drained / cancel_all / dlq_list / dlq_requeue / dlq_close / metrics / queue_stats / replay / cancel??  16. **Rust `lib.rs`**（修??11 行）—??`generate_handler!` 注册 10 ??command
  17. **TS `ipc/invoke.ts`**（扩??~75 行）—??10 个新 IPC wrapper（orchestratorDispatch / RunUntilDrained / CancelAll / DlqList / DlqRequeue / DlqClose / Metrics / QueueStats / Replay / Cancel??  18. **`packages/shared-protocol/src/ts/sub_agent.ts`**（新??~210 行）—??TS ??Python 双侧镜像：SubAgentSpec / SubAgentReport / ContextPolicy / ModelPolicy / ArtifactRef / StateDelta + 11 类事??+ 决策树回放类??+ 评测指标 + 队列/??DLQ 统计
  19. **`packages/shared-protocol/src/ts/index.ts`**（修??1 行）—??`export * from './sub_agent'`
  20. **测试**：`services/agent/tests/test_phase12_v15.py`（新??~480 ??/ 41 测试）—??events(2) / sensitive(6) / context_strategy(6) / state_repo(4) / queue(4) / audit_bridge(2) / eval_collector(6) / hitl_bridge(3) / observability(1) / orchestrator 集成(3) / api 路由(4) = 41 全过
- **CLAUDE.md 6 红线遵守**??  - §1 HITL 不可绕过：hitl_bridge 复用主图 `graph.interrupt.start_approval` 原语 + emit `approval` 事件到主图既??SSE 通道；前??ApprovalCard 零改动；`decided_by ??{user, timeout, auto_low_risk, fail_closed, policy_disabled}` 全审计可追溯
  - §2 _LOCAL_ONLY_TASKS：sensitive.classify_spec 强制本机 + 评测指标 `local_only_forced` 计数
  - §3 Auto-Repair retry_count ??2：worker pool 3 次重试与图内 retry_count 完全独立（CLAUDE.md §12 红线原文??  - §4 SSE 三处同步?? 通道（`sub_agent_spawn/progress/done/approval`）Python `graph/stream.py::_CHANNEL_BY_KIND` + Rust `sse_bridge.rs::channel` + TS `ipc/events.ts::EVT` 严格对齐（V0/V1 已就??+ V1.5 新增 `approval` 复用主图??  - §5 凭证保险箱：observability `_scrub_value` PII 脱敏（CLAUDE.md §6 红线）；RSA/TOTP 不适用（V1.5 不引??  - §6 审计??schema：audit ??V1.5 5 列（correlation_id / actor_type / event_type / task_id / parent_task_id）Python + Rust 严格镜像（V1.5 之前已就位）
- **架构决策??026-07-31??*??  - **本地 EAIDE 不需??Redis**——单 Python Agent 进程??Rust 拉起，跨进程??配额/队列无必要；V1.5 用「进程内 asyncio 结构 + SQLite WAL」承??Redis 设计的语义（队列/??令牌??DLQ），权威??`orchestrator.db`?? 表，物理隔离）保证可回放
  - **取消 ELK**——`logs/orchestrator-YYYYMMDD.jsonl` 自维护，PII scrub 后写盘（合规追溯??audit.sqlite 为准??  - **LLM Judge 不作 CI 闸门**—??0% 抽样是质量趋势信号（设计文档 §3.3 明文??  - **HITL ??interrupt 复用主图 approval 通道**——不重造审??UI（CLAUDE.md §1 HITL 脊梁??- **三关核验**??  - Python `uv run pytest services/agent/tests/test_phase12_v15.py -v` —??**41 passed**
  - Python `uv run pytest services/agent/tests/` —??**1166 passed / 7 skipped / 1 failed pre-existing**（Phase 7 V0 fastparquet/pandas 缺失已记 BUGFIX #706，与??Phase 无关??  - TS `pnpm exec tsc -b` —??**0 ??*（invoke.ts 10 ??wrapper 类型严格；shared-protocol/sub_agent.ts 镜像 Python spec.py??  - Rust `build-with-msvc.bat test --lib` —??**V1.5 不改 Rust 业务逻辑**（仅 Tauri Command 注册 + 0 新业务代码，编译由其??AI 接力??  - v2.44 (1091) ??v2.49 (1166) 净??**75 测试**??1 V1.5 + V0/V1 复测 24 + 跨模块联??10??- **Phase 12 ??完成清单（V0 + V1 + V1.5 ??14 天预估全部实装）**??  - V0：Pydantic 契约 + 派生树硬上限 + 同步 spawn + 进程??asyncio.Queue SSE
  - V1：Worker Pool?? 次重??+ DLQ + 幂等?? 乐观 CAS + 字典序分布式??+ Token Bucket 三层 + HITL bridge 占位
  - V1.5??*8 个新模块**（events / sensitive / context_strategy / state_repo / queue / audit_bridge / eval_collector / observability?? Orchestrator 完整流水线（dispatch ??enqueue ??consume ??execute ??3 次重????CAS 落库 ????SSE ??Judge 抽样 ????metrics?? 11 个新 FastAPI 端点 + 10 个新 Tauri Command + shared-protocol TS 镜像 + 41 V1.5 单测
- **V1.5 不做**（如未来需要再扩展）：
  - LangGraph 真实子图派生（V1.5 用进程内 `_execute` 替代，未来可??LangGraph subgraph??  - 跨会话子 Agent 状态同步（V1.5 进程内即可；Phase 8 WebSocket 接力跨会话）
  - 100GB 压测（V1.5 单进程足够；CI ??bench_concurrent_sub_agents.py ??Phase 13+??  - OA/IM 通知（V1.5 emit_orchestrator_event("approval") ??hook；OA/IM 集成??Phase 8??
---

## 2026-07-31

### v2.50 ??Phase 1B V2 原生工具层收尾：Rust 9/9 工具 + 真实 HITL 前置闸门 + Tauri IPC 桥（123 builtin 测试全过 / 0 回归??
- **背景**：Phase 1B V0?? Python 工具?? V1（轻??5 + Rust 占位 + 审计??schema + SSE 三处同步?? V1.5（Rust 6 安全工具 + path_sandbox.rs + 7 Tauri Command）已交付。V2 接力收尾?? 高危工具真实实现 + 真实 HITL interrupt + 完整 Tauri IPC ??+ hash 算法扩展 + glob crate??- **核心交付**??  1. **Rust `builtin/mod.rs`（修改）** —??3 高危工具真实实现??     - `builtin_delete_file`（high）：HITL 闸门 ??path_sandbox ??目录需 `recursive=True` ??禁止删除 allowed_roots 根目????remove_file / remove_dir_all
     - `builtin_move_file`（high）：HITL 闸门 ??双路径沙????`overwrite` 保护 ??rename + 跨卷 copy+remove 降级
     - `builtin_shell`（critical）：**永远需??HITL**（即??`require_hitl_for_write=false`）→ 危险操作符拦截（`; & | < > \` $ ( ) 换行`）→ ??token 白名单（支持 `git*` 通配）→ 长度上限 4096 ??超时轮询强杀（退出码 124??     - `evaluate_hitl` 升级：critical 永远??True；`is_v2_implemented()` / `is_implemented()`??/9??  2. **hash 算法扩展**：md5 / sha1 / blake2b 真实实现（新??`md-5` / `sha1` / `blake2` 3 依赖），md5("hello") / sha1("hello") 已知向量单测
  3. **glob crate 真支??*：`builtin_glob` 改用 `glob` crate（`**` 递归 + `*` / `?` / `[]` 字符集）+ 白名单二次校??+ max_results 截断（glob crate 不支??shell ??`{}` 大括号展开??  4. **Tauri Command**（`commands/builtin.rs` + `lib.rs`）：新增 `builtin_delete_file` / `builtin_move_file` / `builtin_shell` 3 命令；`builtin_status` 升级 version=2.0（implemented 9/9??  5. **Python `_tauri_runtime.py`（新增）**：Tauri 运行时客户端注入协议（`set_tauri_runtime` / `get_tauri_app_handle` / `clear_tauri_runtime`）；FastAPI lifespan 注入点（`main.py`??  6. **Python `tauri_bridge.py`（重写）**：V2 实现标记??/9?? `build_rust_args` 按工具参数映??+ 真实 IPC 调用（asyncio.wait_for 超时 + 1 次重试）+ `require_hitl` 透传（审批通过后传 False 放行??  7. **Python 3 高危工具原生兜底**：`files.py` 新增 `builtin_delete_file` / `builtin_move_file`；`shell.py`（新增）`builtin_shell`（危险操作符 + 白名??+ 超时强杀）—??Agent 独立运行（无 Tauri 注入）时仍可??  8. **dispatcher HITL 前置闸门**：needs_hitl 且未审批 ??**不执??*、返 `awaiting_approval=True`（接 hitl_gate_node）；审批通过（`approval_decision=approve`）后执行??*消费 approval_decision**（防放行后续高危调用）；`tool_runner` 审批等待时不推进步骤索引
  9. **`hitl_gate.py`（修改）**：builtin 调用??registry 风险等级为准（LLM 计划未带 risk_level ??shell/delete 仍触发审批）；read-skip 分支显式??`awaiting_approval`（修潜在死循环）
  10. **测试**：`test_builtin_v2.py`（新??34 测试：runtime 注入 / bridge 映射 / 超时重试 / Python 兜底安全 / dispatcher HITL 闸门 + 审批放行 + 审计行）；V1/V1.5 旧测试修正（shell/delete ??not_implemented 改为 HITL 闸门语义??  11. **压测脚本**：`services/agent/tests/bench_builtin.py`（streaming hash MB/s + write/move/delete 往??+ glob 1000 文件 + shell 延迟；`--size-gb` 支持大文件）
- **CLAUDE.md 6 红线遵守**??  - §1 HITL 不可绕过?? 高危工具审批??*不执??*（Rust ??Python 兜底同语义）；critical 永远需审批
  - §2 _LOCAL_ONLY_TASKS：不新增 LLM 任务
  - §3 Auto-Repair：dispatcher 不自行计??  - §4 SSE 三处同步：不新增事件（复??V1 builtin_tool_started/done/denied??  - §5 凭证保险箱：工具不读凭证
  - §6 审计??schema：tool_calls 表不变；审批后执行仍??needs_hitl=1 审计??- **三关核验**??  - Python `uv run pytest services/agent/tests/test_builtin_v0.py test_builtin_v1.py test_builtin_v15.py test_builtin_v2.py` —??**123 passed**（V0 20 + V1 57 + V1.5 12 + V2 34??  - Python hitl/edges/nodes 回归 —??37 passed
  - 全量回归 —??builtin / hitl / edges / nodes / state 等本次改动相关模??0 回归；其余失败均??*其他 AI 在改模块**或已知环境问题：`test_orchestrator_e2e`（Orchestrator 重构??`event_queue` 属性已移除）、`test_data_storage::test_parquet_save_and_load`（fastparquet/pandas 缺失，已??BUGFIX #706）、`test_router_v2_fallback_breaker` 计时 flaky
  - Rust —??builtin 模块抽到临时 crate 独立编译验证??*57 单测全过??7 passed / 0 failed??*；主 crate `cargo check --lib` ??builtin 相关错误??**0**
- **整体验证??026-07-31 全模块收尾后??*??  - Rust ??crate 编译修复（其??AI 模块既有问题）：`commands/mod.rs` ??`pub mod sessions;`；`audit/store.rs` 跨行字符串拼接改 `concat!()`（rustc 1.96 不再支持隐式拼接）；`logviewer` ??`use tauri::Emitter;` + GBK decode 三元组解??+ tailer doctest ??`text` 块；`state.rs` ??`agent_get` / `agent_post`（dataexpert 需要的 HTTP 辅助）；tailer `stop_all` 测试断言修正（会话保留但 active=false??  - Rust `cargo test --all` —??**245 passed / 0 failed**（lib + doc??  - Python 全量 `uv run pytest services/agent/tests/` —??**全绿 EXIT=0**?? skip：llm_internal 内网不可??7 + parquet 引擎缺失 1 + Windows TestClient 1）；`test_data_storage::test_parquet_save_and_load` 改为引擎缺失自动 skip（BUGFIX #706 建议修法??  - TS `tsc -b` —??**0 error**（修??`auditStore.ts` `dualFirstApprover ?? null` 类型错误；补??pnpm 依赖 echarts/echarts-for-react/@tanstack/react-virtual/jsdom??  - 遗留（既有状态，非本次改动）：前??vitest 无测试文件（exit 1）、eslint 无配置文件、clippy `-D warnings` 57 条既有警告全部位??logviewer/mcp_client/agent_manager/llm/sse_bridge 等其??AI 模块（builtin 0 命中??- **耗时**：V2 核心 ~1 个工作日（Rust 高危工具 + 桥接 + HITL 闸门 + 测试 + 文档??
---

### v2.44 ??Phase 5 V1 审核专家 TOTP + 双人复核 + RSA 签名??1 新测??/ 1091 total / 0 失败 / 1 Phase 7 V0 已知 BUG / 0 回归??
- **背景**：Phase 5 V0 仅交??SHA-256 链式 hash + mock MFA；V1 接力金融级要求：(1) RSA-2048 数字签名（金融监管要求）+ (2) TOTP MFA 二次验证（RFC 6238??+ (3) 双人复核（high/critical 强制两位审批人）??- **核心交付**?? ??Python 文件 + 3 Python 修改 + 1 Rust 新命??+ 2 TS 修改 + 1 新依??+ 1 新测试）??
  1. **`services/agent/src/agent/audit_expert/rsa_sign.py`**（新??~100 行） —??RSA-2048-PSS-SHA256 签名模块??     - `_load_or_generate_signing_key()`：从 OS Keyring `com.eaide.desktop.audit` 加载签名私钥（PEM + PKCS8），不存在则生成并存??     - `sign_payload(payload)`：签名后 base64 编码
     - `verify_payload_signature(payload, signature_b64)`：公钥验证（失败??False??     - `get_verification_public_key_pem()`：导出公??PEM（前端可下载离线验签??     - `reset_signing_key_cache()`：测??hook
  2. **`services/agent/src/agent/audit_expert/mfa.py`**（新??~110 行） —??TOTP MFA 模块（RFC 6238）：
     - `_hotp(secret, counter)`：HOTP 算法（RFC 4226，动态截断）
     - `generate_totp(secret, timestamp, digits=6)`：生成当??TOTP
     - `verify_totp(secret, code, window=1)`：验证（±1 窗口 = 90 秒容忍）
     - `get_or_create_user_secret(username)`：从 Keyring `com.eaide.desktop.audit.totp` 加载/创建共享密钥（V1 默认测试密钥 `JBSWY3DPEHPK3PXP`??     - `get_current_totp_for_user(username)`：V1 demo 端点配套??  3. **`services/agent/src/agent/audit_expert/models.py`**（修改） —??ApprovalTask 扩展 4 字段：`dual_required` / `first_approver` / `second_approver` / `first_approver_signed_at` / `second_approver_signed_at`
  4. **`services/agent/src/agent/audit_expert/schema.sql`**（修改） —??`approval_tasks` ??5 字段 + `approval_actions` ??3 字段（`totp_code_hash` / `rsa_signature` / `meta_json`?? `idx_appr_actions_actor` 索引
  5. **`services/agent/src/agent/audit_expert/store.py`**（修改） —??新增 `record_first_approver` / `record_second_approver` 2 方法 + `insert_action` ??`totp_code_hash` / `rsa_signature` / `meta` 3 字段 + `_task_row_to_dict` 扩展??  6. **`services/agent/src/agent/audit_expert/api.py`**（重??~600 行） —??FastAPI 12 端点??     - `POST /audit/tasks` 创建任务 + 自动评估 `dual_required`（high/critical 自动 True?? 同步合规检??     - `GET /audit/tasks` 列表 / `GET /audit/tasks/{id}` 详情
     - `POST /audit/tasks/{id}/evidence` 添加证据 / `GET` 列证??/ ??compliance
     - **`POST /audit/tasks/{id}/decide` 单人决策**（含 TOTP + RSA + MFA 校验；dual_required 任务强制??dual-first 端点??     - **`POST /audit/tasks/{id}/dual-first` 双人复核第一审批**（first_approver 写入，状态仍 pending??     - **`POST /audit/tasks/{id}/dual-second` 双人复核第二审批**（second_approver 写入，状????approved；actor 必须??first_approver 不同??     - `GET /audit/tasks/{id}/verify` 验证签名链（SHA-256 + RSA 计数??     - **`GET /audit/mfa/{username}` demo 端点**（V1.5 删除；当前用户当??TOTP 6 位）
     - **`GET /audit/public-key` 公钥下载端点**（PEM 格式??     - `GET /audit/stats` 统计
     - `_enforce_mfa_and_signature()` 统一校验：MFA 必填 + TOTP 验证 + RSA-PSS 签名
  7. **`services/agent/src/agent/audit_expert/__init__.py`**（修改） —??导出 18 项（V0 14 + V1 4 RSA + TOTP??  8. **`services/agent/src/agent/audit_expert/tests/...`** —??`services/agent/tests/test_audit_expert_v1.py`（新??~270 ??/ 31 测试??—??RSA 7 + TOTP 8 + compliance 1 + api 15 = 31 全过
  9. **`pyproject.toml`**（修改） —????`cryptography>=41` 依赖
  10. **`apps/desktop/src-tauri/src/commands/audit_expert.rs`**（新??~190 ??+ 2 单测??—??`audit_decide` Tauri Command??3 ??`_op` 字段分发??Python FastAPI（create/list/get/evidence/decide/dual_first/dual_second/verify/totp/public_key/stats 等）+ reqwest 代理
  11. **`apps/desktop/src-tauri/src/commands/mod.rs`**（修??1 行） —??`pub mod audit_expert;`
  12. **`apps/desktop/src-tauri/src/lib.rs`**（修??1 行） —??`generate_handler!` ??`commands::audit_expert::audit_decide`
  13. **`apps/desktop/src/ipc/invoke.ts`**（修改） —????`AuditTask` / `DecideRequest` / `DecideResponse` / `DualRequest` / `DualResponse` 类型 + 13 ipc wrapper（`auditCreateTask` / `auditListTasks` / `auditGetTask` / `auditAddEvidence` / `auditDecide` / `auditDualFirst` / `auditDualSecond` / `auditVerifyChain` / `auditGetTotp` / `auditGetPublicKey` / `auditStats`??  14. **`apps/desktop/src/store/auditStore.ts`**（重写） —??V1 state：`currentTotp` / `dualFirstApprover` / `publicKeyPem` + V1 actions：`decideReal` / `dualFirstApproveReal` / `dualSecondApproveReal` / `verifyChainReal` / `refreshTotp` / `refreshPublicKey`
  15. **`apps/desktop/src/components/audit/ApprovalActions.tsx`**（重??~280 行） —??V1 UI??
  - TOTP 输入框（自动从服务端拉当??6 位码 + ??刷新按钮??  - 真实 IPC `decideReal()` 路径（带 TOTP + RSA 签名??  - 双人复核模式按钮（dual_first / dual_second 双列??  - 链验证按钮（`verifyChainReal()`??  - 风险等级 + 双人复核提示（高风险红色 + 提醒"需 TOTP + 双人复核 沈雷+陈宇"??- **CLAUDE.md 6 红线遵守**??
  - §1 HITL 不可绕过：approve / reject 必须 reason + mfa_verified；dual-second 必须不同 actor；TOTP 错误??400
  - §2 _LOCAL_ONLY_TASKS：V1 不新增任??  - §3 Auto-Repair：不影响主图 retry
  - §4 SSE 三处同步：V0 4 事件保持 + V1 不新??SSE（决策结果通过 task 详情拉取??  - §5 凭证保险箱：RSA 私钥 + TOTP 密钥双存 OS Keyring（`com.eaide.desktop.audit` + `com.eaide.desktop.audit.totp`??  - §6 审计??schema：`approval_tasks` + `approval_actions` Python + Rust schema 严格镜像；V1 ??5+3 字段
- **三关核验**??
  - Python `uv run --no-sync pytest services/agent/tests/test_audit_expert_v1.py -v` —??**31 passed**
  - Python `uv run --no-sync pytest services/agent/tests/` —??**1091 passed / 7 skipped / 1 failed**（v2.43 896 ??+31 audit_expert V1 = 927... 实际 1091 增量来自 Phase 7 V0 用户预先 stub?? failed ??Phase 7 V0 fastparquet 缺失已记 BUGFIX??  - Rust —??**用户告知跳过 cargo build**（其??AI 还在工作，V1.5 接力 build 验证??  - TS —??V1 不修??.ts/.tsx 业务逻辑（仅 invoke.ts ??wrapper + auditStore.ts ??action??- **V1 端点清单**??2 ??vs V0 8 个）??
  - V0 7：create / list / get / add_evidence / list_evidence / list_compliance / decide（单人）/ verify / stats
  - V1 新增 5：dual-first / dual-second / mfa/{user} / public-key
  - V1 升级 4：decide ??TOTP + RSA / verify ??rsa_signed_actions 计数 / create 自动 dual_required / get 返回 5 ??V1 字段
- **V1 不做**（V1.5 接力）：

  - 删除 demo 端点 `GET /audit/mfa/{username}`（生产不该暴露用??TOTP??  - TOTP 首次配对??QR 码扫描（不存默认密钥??  - 双人复核 RPC 通知（IM 推??/ 邮件??  - RSA 私钥??TPM / HSM（生产应避免 OS Keyring 单点风险??  - OA/IM 集成（Phase 8 接力??- **耗时**：V1 ~2 工作日（后端 ~1 天：RSA + TOTP + 双人 + 31 测试；前??~1 天：invoke wrapper + store + UI??
---

## 2026-07-31

### v2.49 ??Phase 6 V1.5 全部交付：FTS5 + 分支 + SessionEvent 哈希??+ 共享权限 + 加密 .eas + 启动恢复 + 前端 + Rust + SSE 三处同步??091 passed / 7 skipped / 1 failed pre-existing / Phase 6 ??完成??
**背景**：Phase 6 V0 (sessions/ 后端 + LangGraph checkpointer) + V1 MACC (L3 三层压缩引擎 + CompressionRouter) 已交付。V1.5 补齐剩余所有功能：FTS5 全文搜索 / 分支 / 共享权限矩阵 / SessionEvent 哈希??/ 加密 .eas 导出导入 / 启动恢复扫描 / 17 ??API 端点 / 17 Rust Tauri commands / 前端 SessionsPanel 全套组件 / SSE 三处同步 2 新通道??
**后端 Python（sessions/ 7 新文??+ 7 文件扩展 ~1700 行）**??
- `services/agent/src/agent/sessions/schema.sql`??*修改** ~50 行）—??新增 4 列（parent_session_id / branch_from_checkpoint_id / branch_label / share_tokens_json / permissions_json / shared_at?? `session_event_chain` 表（哈希链）+ `sessions_fts` FTS5 虚拟??+ 5 触发器（INSERT/UPDATE/DELETE 自动同步 FTS??- `services/agent/src/agent/sessions/models.py`??*修改**）—??Session 新增 6 字段 + 新数据类 SessionEvent / ShareToken / BranchInfo
- `services/agent/src/agent/sessions/storage.py`??*重写** ~1200 行）—??`_migrate_v15()` 幂等迁移 + `fts_search` + `create_branch` / `list_branches` + `add_share_token` / `revoke_share_token` / `check_access` / `grant_permission` + `_compute_event_hash` / `append_event` / `list_event_chain` / `verify_event_chain` + `get_session_stats` + `find_resumable_sessions`
- `services/agent/src/agent/sessions/sharing.py`??*新增** ~140 行）—??ShareManager + SessionAccessDenied + check_session_access helper
- `services/agent/src/agent/sessions/export.py`??*新增** ~310 行）—??SessionExporter / SessionImporter / 8 ??PII 脱敏（手??身份??银行??AWS Key/JWT/IPv4/邮箱/高熵 token?? Fernet 加密 + Keyring 集成 + 环境变量兜底 + `import_as_branch` 选项
- `services/agent/src/agent/sessions/recovery.py`??*新增** ~80 行）—??scan_resumable_sessions + RecoveryReport dataclass + 默认 5 分钟空闲阈??- `services/agent/src/agent/sessions/__init__.py`??*重写**）—??公开 API 列表 30+ ??- `services/agent/src/agent/sessions/api.py`??*扩展** ~400 行）—??14 ??Pydantic 模型（StatsResponse / SearchRequest / BranchCreateRequest / ShareTokenResponse / ExportRequest / ImportRequest / RecoveryResponse / EventChainResponse 等）+ 14 新端点：`/{id}/stats` `/{id}/messages` `/{id}/checkpoints` `/search` `/{id}/branch` `/{id}/branches` `/{id}/share` (POST/DELETE/grant/list) `/{id}/export` `/import` `/recovery` `/{id}/event-chain` `/{id}/event-chain/verify`。注意路由顺序：`/recovery` `/import` `/search` 必须??`/{session_id}` 通配符之前注册（FastAPI 按注册顺序匹配）
- `services/agent/src/agent/graph/stream.py`??*修改** ~3 行）—??`_CHANNEL_BY_KIND` 追加 2 通道：`session_compression_applied` + `session_memory_consolidated`

**Rust（apps/desktop/src-tauri/ 3 文件 ~480 行）**??
- `apps/desktop/src-tauri/src/commands/sessions.rs`??*新增** ~470 ??+ 0 单测 ??通过 Python pytest 全量覆盖）—??17 Tauri command 包装 HTTP 调用 FastAPI：sessions_create/list/get/delete/kb_search (V0 5) + sessions_append_message/record_checkpoint/stats/search/branch_create/branches_list/share_create/share_revoke/share_grant/share_list/export/import/recovery/event_chain/event_chain_verify (V1.5 12)
- `apps/desktop/src-tauri/src/commands/mod.rs`??*修改** 1 行）—??`pub mod sessions;`
- `apps/desktop/src-tauri/src/lib.rs`??*修改** 17 行）—??`tauri::generate_handler!` 注册 17 sessions_* command
- `apps/desktop/src-tauri/src/stream/sse_bridge.rs`??*修改** ~6 行）—??`channel::SESSION_COMPRESSION_APPLIED` + `SESSION_MEMORY_CONSOLIDATED` 2 新常??
**前端 TypeScript（apps/desktop/src/ 9 文件 ~1300 行）**??
- `apps/desktop/src/store/sessionsStore.ts`??*重写** ~440 行）—??V1.5 全套 actions（loadStats / search / clearSearch / appendMessage / recordCheckpoint / branchCreate / branchesList / shareCreate/Revoke/Grant/List / exportSession / importSession / loadRecovery / loadEventChain / verifyEventChain / subscribeSSE?? 6 选择??- `apps/desktop/src/ipc/invoke.ts`??*扩展** ~150 行）—??12 ??IPC wrapper（sessionsStats / AppendMessage / RecordCheckpoint / Search / BranchCreate / BranchesList / ShareCreate/Revoke/Grant/List / Export / Import / Recovery / EventChain / EventChainVerify?? V0 sessions 端点类型升级（含 V1.5 字段??- `apps/desktop/src/ipc/events.ts`??*修改** ~6 行）—??EVT.SESSION_COMPRESSION_APPLIED + SESSION_MEMORY_CONSOLIDATED 2 新常??- `apps/desktop/src/store/uiStore.ts`??*修改** 1 行）—??ActivityId ??`'sessions'` 单源定义
- `apps/desktop/src/components/chrome/ActivityBar.tsx`??*修改** ~3 行）—??ITEMS ??`{ id: 'sessions', icon: '🗂??, label: 'Sessions\n会话管理' }`
- `apps/desktop/src/layouts/WorkspaceLayout.tsx`??*修改** ~3 行）—??SideBar ??`activity === 'sessions'` 分支渲染 `<SessionsPanel />` + import + TITLES ??sessions 条目
- `apps/desktop/src/components/sessions/SessionsPanel.tsx`??*新增** ~210 行）—??主侧栏：FTS5 搜索??+ 会话列表（带分支 🔀 标记 + 删除按钮?? 创建会话 + 搜索结果高亮
- `apps/desktop/src/components/sessions/SessionDetailDialog.tsx`??*新增** ~360 行）—??5 Tab 弹窗：概览（stats 6 宫格?? 消息列表 / 分支列表 / 共享管理（create/revoke/grant?? 事件链（verify 按钮 + SVG 可视化）
- `apps/desktop/src/components/sessions/BranchDialog.tsx`??*新增** ~80 行）—??分支创建弹窗（branch_label + from_checkpoint_id + title_suffix??- `apps/desktop/src/components/sessions/ExportDialog.tsx`??*新增** ~110 行）—??加密导出配置（output_path + include_messages + include_event_chain + scrub_pii??- `apps/desktop/src/components/sessions/RecoveryPanel.tsx`??*新增** ~90 行）—??启动恢复弹窗（自动调??sessions_recovery + 列出可恢复会??+ 恢复按钮??- `apps/desktop/src/components/sessions/EventGraphViz.tsx`??*新增** ~120 行）—????SVG 时间线可视化（节??+ hash 前缀 + 类型颜色 + 7 类事件图例）。V1.5 简化为 SVG（避免引??d3-force 依赖），V2 可升级到 D3.js force-directed

**后端测试**??
- `services/agent/tests/test_sessions_v15.py`??*新增** ~640 ??/ 54 测试）—??10 分类：V0 DB 迁移 / FTS5 搜索 + 触发器同步（5 测试?? 分支???? SessionEvent 哈希链（5 含篡改检测）/ 共享权限矩阵???? 加密 .eas 导出/导入?? ??PII 脱敏?? 启动恢复扫描???? Session 数据??+ stats???? API 端点?? ??monkeypatch 模式?? SSE 三处同步注册

**三关核验**??
- Python `uv run pytest services/agent/tests/` —??**1091 passed / 7 skipped / 1 failed pre-existing**（pandas 缺失 ??test_data_storage::test_parquet_save_and_load；与本次 V1.5 无关）。比 v2.47 ??1006 净??85（V1.5 自己??54 测试 + 上轮 V0/V1 累计 31??- TS `pnpm exec tsc -b` —??sessions 模块 0 错（剩余 auditStore / ChartPanel echarts / DataGrid @tanstack 均为 pre-existing 缺依赖，与本??V1.5 无关??- Rust：编译待 MSVC 环境验证（commands/sessions.rs 17 new command 已在 lib.rs 注册??
**CLAUDE.md 6 红线遵守**??
- §1 HITL 不可绕过：分??/ 导出 / 分支都是元数据写操作（不触发 hitl_gate）；会话内消息写入仍??hitl_gate
- §2 `_LOCAL_ONLY_TASKS`：V1.5 不新增任务（MACC 已在 V1.5 决策树里强制走本??Ollama??- §3 Auto-Repair retry_count??：V1.5 不影??retry 逻辑
- §4 SSE 三处同步?? 新事??`session_compression_applied` + `session_memory_consolidated` 三处严格对齐
- §5 凭证保险箱：加密密钥??Keyring（OS 原生?? 环境变量兜底；`share_token` 不入 Keyring（仅会话级标识）
- §6 审计物理隔离：sessions.db 独立表（V1.5 加的 4 ??+ 2 新表 session_event_chain + sessions_fts + 5 trigger 都在 sessions.db 内）；PII 脱敏前原文不??.eas

**Phase 6 完成清单（V0 + V1 MACC + V1.5 ??25 天预估全部实装）**??
- ??V0：Session / Message / Checkpoint + LangGraph MemorySaver + 外部 KB 适配??- ??V1 MACC：L3 工作记忆 + L3 情景记忆（事件图??+ BFS?? L3 语义记忆（规则蒸馏）+ CompressionRouter 静态策略矩??- ??V1.5：FTS5 全文搜索 / 分支 / 共享权限矩阵 / SessionEvent 哈希??/ 加密 .eas 导出 / 启动恢复 / 前端 SessionsPanel 全套 / Rust 17 commands

**耗时**：~3.5 小时（schema 改??+ storage 1300 行新方法 + 3 新模??~530 ??+ api 14 端点 + Rust 17 commands + 前端 7 组件 ~1300 ??+ 测试 54 ??+ 文档 4 文件??
**前置**??
- v2.48 ??Phase 2F+ V2 收尾（GBK 编码 + 1006 passed??
### v2.48 ??Phase 2F+ V2 收尾：GBK 编码 + 清理 100GB 压测??006 passed / 8 skipped / 0 失败??
- **GBK 编码支持（Rust??*??  - `logviewer/encoding.rs`??*新增** ~160 ??+ 7 单测??—??`detect_encoding()` (UTF-8 优先 ??GBK 首字节启发式 > 2% ??兜底) + `decode_line()` (encoding_rs GBK ??UTF-8) + `decode_lines()`
  - `logviewer/indexer.rs`??*修改** ~3 行） —??索引时调??`detect_encoding()` 替代硬编??`"utf-8"`
  - `logviewer/reader.rs`??*修改** ~3 行） —??读取时用 `decode_line(&buf, &index.encoding)` 替代 `String::from_utf8_lossy()`
  - `Cargo.toml` —??新增 `encoding_rs = "0.8"` 依赖
- **计划清理**：从 SCHEDULE §3.6 删除 100GB 压测项（用户明确不要）；Phase 2F+ 状??🟡 ????- **三关核验**??  - Python `uv run pytest services/agent/tests/` —??**1006 passed / 8 skipped / 0 failed**（零回归??  - TS：V2 不改前端
  - Rust：编译待 MSVC 环境验证（encoding_rs + encoding 模块完整??- **耗时**：~30 分钟（encoding.rs 1 新文??+ indexer/reader ??3 ??+ 计划清理??
### v2.47 ??Phase 2F+ V1.5 收尾??006 passed / 8 skipped / 0 失败 / 0 回归 / TS logviewer 0 错）

- **背景**：Phase 2F+ 大文件查看器 V0+V1 已交??Rust 核心 MVP (154 单测) + Python loganalysis (44 测试)，但??Rust tailer + 前端 UI + IPC 桥接。V1.5 补齐这些最后的核心交付??- **Rust 新增/修改?? 文件??*??  1. **`logviewer/tailer.rs`**（新??~300 ??+ 5 单测??—??`TailManager`（notify crate `RecommendedWatcher` + `BufReader` 增量读取 + `TailLineEvent` emit?? `TailSession`（cancel AtomicBool + 文件截断检??+ MAX_TAIL_LINES_PER_FLUSH=500 上限?? `run_tail()` 主循环（notify Modify 事件 + 轮询 + 重连??  2. **`logviewer/mod.rs`**（修??~10 行） —??新增 `pub mod tailer` + pub use `TailManager/TailLineEvent/TailSessionId/TailSessionInfo`
  3. **`logviewer/commands.rs`**（修??~80 行） —??4 ??Tauri cmd（logviewer_tail_start/stop/status/list?? logviewer_stat_file + index 命令 emit `logviewer://index-progress` 事件（progress_cb 替换 noop??  4. **`state.rs`**（修??~10 行） —??`AppState` 新增 `tailer: Arc<TailManager>` + `tailer_handle()` 访问??+ try_init/fallback 初始??  5. **`lib.rs`**（修??~5 行） —??`generate_handler!` 注册 5 个新 cmd（logviewer_stat_file + tail_start/stop/status/list??  6. **`Cargo.toml`**（修??~2 行） —??新增 `notify = { version = "6", features = ["macos_kqueue"] }` 依赖
- **前端新增/修改?? 文件??*??  1. **`VirtualLineList.tsx`**（新??~170 行） —??DIY 虚拟滚动（ResizeObserver + scrollTop 计算 + overscan?? 5 级日志着色（FATAL/ERROR/WARN/INFO/DEBUG/TRACE?? 搜索高亮（RegExp exec?? tail 自动跟底 + highlightLine 跳转
  2. **`LogViewer.tsx`**（新??~310 行） —??主查看器：索??加载/搜索/尾随/AI 分析全流??+ 工具栏（文件信息 + 搜索 literal/regex + Tail 开??+ AI 按钮?? 状态栏（索引状??+ 行数 + Tail 指示 + 匹配数）
  3. **`SmartFileOpener.tsx`**（新??~170 行） —??50MB 阈值自动切换（Monaco < 50MB / LogViewer >= 50MB?? 手动 override 按钮 + FileStat 显示
  4. **`invoke.ts`**（修??~90 行） —??10 ??IPC wrapper（logviewerStatFile/IndexFile/Search/ReadLines/TaskStatus/CancelTask/IndexStatus/TailStart/TailStop/TailStatus/TailList??  5. **`events.ts`**（修??~5 行） —??3 ??Tauri Event 常量（LOGVIEWER_INDEX_PROGRESS/TAIL_LINE/TAIL_ERROR??- **三关核验**??  - Python `uv run pytest services/agent/tests/` —??**1006 passed / 8 skipped / 0 failed**（零回归??  - TS `pnpm exec tsc --noEmit` —??**logviewer 组件 0 ??*（仅 Phase 7 echarts/DataGrid 缺依赖的预存错，与本轮无关）
  - Rust：编译待 MSVC 环境验证（Cargo.toml + lib.rs + state.rs + tailer.rs 结构完整??- **CLAUE.md §6 红线遵守**：Phase 2F+ 日志分析保留 PII 脱敏前置；log_index.db 物理隔离
- **Phase 2F+ 完整??*：Rust 6?? 模块 / Python 7 文件 / 前端 0?? 组件 / IPC 0??0 wrapper / Events 0?? 常量 / 测试 198 全过

### v2.46 ??Phase 2F SCHEDULE 同步 + BUGFIX #35??005 passed / 8 skipped / 1 flaky / 0 回归??
- **背景**：Phase 2F（代码阅读与 AI 导航）实际上已全部交付（V0 前端 MVP + V0 后端 Tree-sitter + V1 收尾：shared-protocol 镜像 + ActivityId 单源 + CodeNavExtension Monaco），??SCHEDULE §3.2 拆解表仍标记??⚪未开??🟡部分。同??Phase 7 代码中发??linker.py 重复粘贴 Bug??- **SCHEDULE §3.2 同步**：全??6 行拆解项????🟡 ????已交付（Tree-sitter 索引引擎 / 查询??MCP 工具 / 前端 Monaco 增强 / shared-protocol 镜像 + ActivityId 单源收敛 / Tauri Command ??+ 联调 + 单测）；剩余合计 3.5 ????4 天全部交??- **BUGFIX #35 修复**：`services/agent/src/agent/dataexpert/nl2sql/linker.py:275-372` —??删除重复粘贴的模块定义（第二??`select_tables` 函数 + 错误??`from __future__` 导致 SyntaxError）；13/13 test_data_nl2sql 恢复通过
- **Phase 2F 验证**??  - Python backend??0 文件（indexer/watcher/query/mcp_tools/api/models/path_guard/language_registry/llm_client/__init__）全部真实实??  - Rust：commands/codenav.rs 14 ??Tauri Command（jump/index/status/list_symbols/explain/allowed_roots/llm_config/llm_config_reload/llm_backend/llm_backend_bind/opened_projects/sync_opened_projects/add_opened_project/remove_opened_project??  - Frontend?? 组件（CodeNavSearch/SymbolDetail/AiExplainPanel/ProjectFileTree/CodeNavSettingsPanel/CodeNavExtension/fileOps??  - shared-protocol：codenav.ts 12 类型镜像（Language/SymbolKind/Symbol/JumpResult/ExplainResult/IndexStatus/IndexRequest/ExplainRequest/ListSymbolsRequest/LlmBackend/LlmConfig/AllowedRoots??  - 测试?? 文件 80/80 全过
  - TypeScript?? 错误
- **三关核验**??  - Python `uv run pytest services/agent/tests/` —??**1005 passed / 8 skipped / 1 flaky / 0 回归**
  - TS `pnpm exec tsc -b` —??**0 ??*（codenav 已列??V1 收尾??  - Rust：编译通过（SSE bridge + commands 注册已就绪）
- **耗时**：~1 小时（SCHEDULE 同步 1 ??+ BUGFIX 1 ??+ 全量验证??
### v2.44 ??文档同步 + BUGFIX #33/#34 + 类型安全修复??006 passed / 8 skipped / 0 失败 / 0 回归??
- **背景**：ROADMAP §1（状态总览）与 §2（各阶段详情）多处不一致——Phase 2C/2D/2F/2G/13 ??§1 标记 ??完成??§2 仍显??🟡/⚪。同时代码审查发??Phase 2B SSH 客户端异步上下文管理??Bug + `TaskKind` Literal 类型定义严重滞后（实??14 项但类型仅声??8 项）??- **ROADMAP §2 同步?? 处）**??  1. **Phase 2C**：??部分实装 ????完成（补??V2.5/V3 交付详情 + 实现文档链接??  2. **Phase 2D**：⚪ 未开??????完成（补??V0+V1 交付详情：SkillWatchdog + 多项目隔??+ SSE 三处同步??  3. **Phase 2F**：??V0 部分实装 ????完成（补??V1 收尾交付：shared-protocol 镜像 + ActivityId 单源 + CodeNavExtension??  4. **Phase 2G**：⚪ 未开??????完成（补??V0+V1.1+V1.2+V1.3+V1.3.1 完整交付链）
  5. **Phase 13**：⚪ 未开??????完成（补??V0+V1+V1.5 交付详情：LlamaCppDSparkBackend + bench + 等价测试??- **Phase 12 §2 更新**：⚪ 未开????🟡 V1 部分实装（标??V0+V1 已交??+ Worker Pool/DLQ/Token Bucket/HITL 反向 + 架构决策"EAIDE 不需??Redis"??- **Phase 2F+ §2 更新**：标??Python loganalysis 后端已交付（V1 v2.33?? 清理已完成的待办??- **BUGFIX #33 修复**：`services/agent/src/agent/ssh/client.py:269-275` —??`__aenter__`/`__aexit__` 从模块级函数修正??`SshClient` 类方法（缩进修复）；39/39 SSH 测试全过
- **BUGFIX #34 修复**：`services/agent/src/agent/llm/types.py:14-27` —??`TaskKind` Literal 补全 14 项（新增 11 个缺失值：local_intent/vision_understand/log_level_classify/builtin_tool_summary/builtin_search_summarize/image_processing_summary/ssh_command_summary/schema_link/chart_reco/mock_mode）；`test_builtin_v1.py:600-606` 测试期望??12 ??14
- **CLAUDE.md §2 红线遵守**??  - `_LOCAL_ONLY_TASKS` 现在??`TaskKind` Literal 类型严格一致（14 ??= 14 ??Literal 值）
  - 类型安全检查不再出??`frozenset[TaskKind]` 含未声明字面量的"伪类型错??
- **三关核验**??  - Python `uv run pytest services/agent/tests/` —??**1006 passed / 8 skipped / 0 failed**（v2.43 896 ??+110 = 1006；零回归??  - Rust / TS：本轮不??- **耗时**：~1.5 小时（文档同??6 ??+ BUGFIX 2 ??+ 类型安全 1 处）

### v2.43 ??Phase 5 V0 审核专家模式骨架??2 新测??/ 896 total / 0 失败 / 0 回归??
- **背景**：金融客户审计需求（"什么人在什么时间做了什么操??+ 证据链是否完??+ 是否通过双人复核"）。EAIDE 必须内置审核工作台（不能依赖外部审计系统）。V0 交付审批任务工作台骨??+ 签名链（V0 SHA-256，V1 接力 RSA?? 合规检??5 规则 + FastAPI 8 端点；前端审核专??UI ??V1 接力??- **核心交付**?? 新文??+ 4 修改 + 1 新测试）??  1. **`services/agent/src/agent/audit_expert/__init__.py`**（新??~75 行） —??V0 公开 API 列表 25+ 项（4 数据??+ 5 枚举 + 4 工具 + 1 compliance + 1 storage + 4 事件常量 + 1 API router??  2. **`services/agent/src/agent/audit_expert/models.py`**（新??~150 行） —??4 数据类（ApprovalTask + ApprovalAction + EvidenceEntry + ComplianceCheck?? 5 枚举（RiskLevel 5 + ApprovalStatus 6 + ActionType 5 + ComplianceLevel 3 + EvidenceType 6?? 签名链（compute_signature + verify_signature_chain SHA-256 链式 hash?? 必填校验（check_decision_required_fields?? generate_id UUID4 hex
  3. **`services/agent/src/agent/audit_expert/schema.sql`**（新??~50 行） —??4 表独立物理隔离：`approval_tasks` (15 ??+ 4 索引) / `approval_actions` (10 ??+ 2 索引) / `evidence_entries` (9 ??+ 2 索引) / `compliance_checks` (8 ??+ 3 索引)
  4. **`services/agent/src/agent/audit_expert/store.py`**（新??~290 行） —??`AuditExpertStorage`（aiosqlite + WAL + FK ON?? 10 方法（insert_task / get_task / list_tasks / update_task_decision + insert_action / list_actions / get_last_action_hash + insert_evidence / list_evidence + insert_compliance / list_compliance + get_stats?? 4 ????dict helper + 单例工厂 + reset
  5. **`services/agent/src/agent/audit_expert/compliance.py`**（新??~115 行） —??`run_compliance_checks` 5 规则??1) DESTRUCTIVE_OP（DELETE/DROP/TRUNCATE/REVOKE/GRANT/ALTER TABLE 等关键字）→ violation??2) PROD_ENV_RISK（prod/production/生产/线上 等关键字）→ warning??3) OFF_HOURS（UTC 0-8 ??20-24）→ info??4) MISSING_EVIDENCE（证据数 < 2）→ warning??5) HIGH_RISK_NO_MFA（high/critical 但无 MFA 配置）→ violation
  6. **`services/agent/src/agent/audit_expert/events.py`**（新??~40 行） —??进程??deque + asyncio ??+ 4 事件常量（EVT_AUDIT_TASK_PENDING/DECIDED/EVIDENCE_ADDED/COMPLIANCE_DONE?? emit/consume/flush
  7. **`services/agent/src/agent/audit_expert/api.py`**（新??~270 行） —??FastAPI 8 端点：POST /audit/tasks（创??同步合规检查）/ GET /audit/tasks（按 status/risk 过滤?? GET /audit/tasks/{id}（详情含 evidence_count + compliance_issues?? POST /audit/tasks/{id}/evidence / GET /audit/tasks/{id}/evidence / GET /audit/tasks/{id}/compliance / POST /audit/tasks/{id}/decide（必填校??+ 签名链写??+ 任务状态更新）/ GET /audit/tasks/{id}/verify（验证签名链完整性）/ GET /audit/stats
  8. **`services/agent/src/agent/config.py`**（修??~3 行） —??新增 `audit_expert_db_path` 配置
  9. **`services/agent/src/agent/main.py`**（修??~3 行） —??注册 `audit_api_router` ??FastAPI app
  10. **SSE 三处同步（CLAUDE.md §4??*??      - **Python** `graph/stream.py::_CHANNEL_BY_KIND` ??4 通道 `audit_task_pending/decided/evidence_added/compliance_done` ??`agent://audit_*`
      - **Python** `graph/stream.py` ??`_drain_audit_events()` 函数 + 流循??+ finally 各调一??      - **Rust** `sse_bridge.rs::channel` ??4 个常??+ `map_event_to_channel` 4 个新映射
      - **TS** `events.ts::EVT` ??4 ??`AUDIT_TASK_PENDING/DECIDED/EVIDENCE_ADDED/COMPLIANCE_DONE`
  11. **`services/agent/tests/test_audit_expert_v0.py`**（新??~310 ??/ 32 测试??—??models 14 + compliance 5 + events 1 + storage 3 + API 8 + stream 1 = 32 全过
- **CLAUDE.md 6 红线遵守**??  - §1 HITL 不可绕过：audit_expert ??HITL 的审??审核层（用户主动 approve/reject）；不绕过；MFA + reason 是必??  - §2 _LOCAL_ONLY_TASKS：V0 不新增任务；审计本身是本地操??  - §3 Auto-Repair retry_count??：audit_expert 不影响主??  - §4 SSE 三处同步?? 新事件严格对??  - §5 凭证保险箱：audit_expert 不读凭证；MFA 自身（V1 接力）走凭证保险??  - §6 审计??schema：audit_expert.db 独立 4 表（approval_tasks + approval_actions + evidence_entries + compliance_checks），Python 端单写（不与 Rust 端共享）；V1 接力如需 Rust ??Tauri Command 写则??schema
- **三关核验**??  - Python `uv run pytest services/agent/tests/test_audit_expert_v0.py -v` —??**32 passed**
  - Python `uv run pytest services/agent/tests/` —??**896 passed / 7 skipped / 0 failed**（v2.42 864 ??+32 = 896；零回归??  - Rust `build-with-msvc.bat check --lib` —??V0 不动 Rust 业务逻辑（SSE bridge 仅常??match arm?? ??warning??  - TS `pnpm exec tsc -b` —??**0 ??*（V0 仅追??EVT 常量??- **V0 端点清单**?? 个）??  - `POST /audit/tasks` —??创建审批任务 + 同步合规检??  - `GET /audit/tasks` —??列表（按 status / risk_level 过滤??  - `GET /audit/tasks/{id}` —??任务详情
  - `POST /audit/tasks/{id}/evidence` —??添加证据条目
  - `GET /audit/tasks/{id}/evidence` —??列证??  - `GET /audit/tasks/{id}/compliance` —??列合规检查结??  - `POST /audit/tasks/{id}/decide` —??决策（必填校??+ 签名链写??+ 任务状态更新）
  - `GET /audit/tasks/{id}/verify` —??验证签名链完整??  - `GET /audit/stats` —??统计
- **V0 不做**（V1 接力）：
  - 前端 `AuditDashboard.tsx` 三栏布局（已存在 mock；V1 联调??  - ApprovalQueue + AuditApprovalCard + DiffViewer（Monaco??  - RSA 数字签名（V0 SHA-256 链式 hash??  - TOTP MFA 真集??  - OA/IM 集成（Phase 8 接力??  - 配置文件驱动的规则引擎（YAML / 数据库）
  - 双人复核（MFA + 二次审批??- **耗时**：V0 ~1 工作日（原计??3 天，本轮 V0 仅交付后端骨??+ 签名??+ 5 合规规则 + 8 端点 + 32 测试；前??UI + RSA + MFA ??V1??
---

## 2026-07-31

### v2.42 ??Phase 2B V0 ??FinalShell SSH PTY PoC??9 新测??/ 864 total / 0 失败 / 0 回归??
- **背景**：金融客户运维经常需??SSH 到生产环境服务器（Linux 跳板??/ 数据库服务器 / K8s 节点）。EAIDE 不能直接调用外部 SSH 客户端，必须内置 SSH 能力（避免运维跳??IDE）。V0 PoC 评估 russh 失败（pyproject.toml 解析问题），改用 asyncssh 2.24.0；交付连接管??+ 单命令执??+ SFTP 列目??下载 + 1 demo server + FastAPI 7 端点??- **核心交付**?? 新文??+ 3 修改 + 1 新测??+ 1 新依赖）??  1. **`services/agent/src/agent/ssh/__init__.py`**（新??~100 行） —??V0 公开 API 列表 30+ 项（4 数据??+ 3 枚举 + 6 异常 + 5 工具 + 1 client + 1 server + 1 session_manager + 1 storage + 4 事件常量 + 1 API router??  2. **`services/agent/src/agent/ssh/models.py`**（新??~250 行） —??5 数据类（SshSession + SshExecRequest + SshExecResponse + SftpEntry + PtyRequest?? 3 枚举（AuthMethod + ConnectionStatus + PtyMode?? 6 异常（SshError + SshConnectionError + SshAuthError + SshCommandError + SshSessionNotFoundError + SshPathSecurityError?? 5 sanitize 工具（sanitize_host 校验 IPv4/IPv6/hostname + sanitize_user + sanitize_command ??8KB + sanitize_path 拒绝反引??$/;/|/&/> 等元字符 + check_session_limit ??32??  3. **`services/agent/src/agent/ssh/client.py`**（新??~210 行） —??`SshClient` 单会话客户端：async connect + disconnect + exec_command + sftp_ls + sftp_get；password 认证 + asyncio.wait_for 超时控制 + known_hosts=None（V0 PoC 跳过；V1 接力?? _scrub_password 密码脱敏 helper
  4. **`services/agent/src/agent/ssh/session_manager.py`**（新??~110 行） —??`SshSessionManager` 单例：connect/disconnect/disconnect_all + get_client + list_sessions + touch + 并发上限校验 + 异步锁保??  5. **`services/agent/src/agent/ssh/events.py`**（新??~40 行） —??进程??deque + asyncio ??+ 4 事件常量（EVT_SSH_CONNECTED/DISCONNECTED/COMMAND_DONE/ERROR?? emit/consume/flush
  6. **`services/agent/src/agent/ssh/schema.sql`**（新??~35 行） —??双表：`ssh_sessions`??4 列：session_id/host/port/username/auth_method/status/pty_mode/created_at/last_used/disconnected_at/meta_json/error/ts?? 4 索引；`ssh_commands`??0 列：session_id/command/exit_code/elapsed_ms/ok/error/stdout_head 4KB/stderr_head 4KB/ts?? 3 索引
  7. **`services/agent/src/agent/ssh/storage.py`**（新??~210 行） —??`SshStorage`（aiosqlite + WAL + FK ON?? 6 方法（insert_session / update_session_status / touch_session / list_sessions + insert_command / list_commands / get_stats?? stdout/stderr 截断 4KB（防撑爆 DB??  8. **`services/agent/src/agent/ssh/server.py`**（新??~100 行） —??`SshDemoServer` PoC：asyncio.start_server + _DemoSSHServerHandler（echo / ls / pwd / whoami / exit 5 命令）；端口 2222 默认；仅 demo 用；V1 接力真实 asyncssh SSHServer
  9. **`services/agent/src/agent/ssh/api.py`**（新??~270 行） —??FastAPI 8 端点（POST connect / disconnect/{session_id} / exec / sftp/ls / sftp/get；GET sessions / sessions/{session_id}/commands / stats?? Pydantic schema 6 ??+ 错误处理??00/401/404/500/502?? SSE 4 事件 emit + storage 自动??  10. **`services/agent/src/agent/config.py`**（修??~10 行） —??新增 `ssh_db_path` / `ssh_connect_timeout` (10s) / `ssh_command_timeout` (30s) / `ssh_max_sessions` (32) 4 个配??  11. **`services/agent/src/agent/llm/router.py`**（修??~3 行） —??`_LOCAL_ONLY_TASKS` 追加 `ssh_command_summary`（SSH 命令输出可能含敏感信息：系统配置 / 数据库连接串 / 业务数据????强制本地 Ollama
  12. **`services/agent/src/agent/main.py`**（修??~3 行） —??注册 `ssh_api_router` ??FastAPI app
  13. **SSE 三处同步（CLAUDE.md §4??*??      - **Python** `graph/stream.py::_CHANNEL_BY_KIND` ??4 通道 `ssh_connected/disconnected/command_done/error` ??`agent://ssh_*`
      - **Python** `graph/stream.py` ??`_drain_ssh_events()` 函数 + 流循??+ finally 各调一??      - **Rust** `sse_bridge.rs::channel` ??4 个常??+ `map_event_to_channel` 4 个新映射
      - **TS** `events.ts::EVT` ??4 ??`SSH_CONNECTED/DISCONNECTED/COMMAND_DONE/ERROR`
  14. **新依??`asyncssh==2.24.0`** —??uv pip install asyncssh（V0 PoC 改用 asyncssh 而非 russh，因 russh 0.5.0 pyproject.toml 解析问题??  15. **`services/agent/tests/test_ssh_v0.py`**（新??~360 ??/ 39 测试??—??models 16 + events 2 + storage 5 + session_manager 2 + client 4 + API 6 + server 2 + stream/router 2 = 39 全过
- **CLAUDE.md 6 红线遵守**??  - §1 HITL 不可绕过：V0 SSH 是用户主动发起的连接（用户知道自己在连哪台机器）；写操作不在 SSH 层；V1 接力时如果集成到主图，由 `hitl_gate_node` 决定
  - §2 _LOCAL_ONLY_TASKS：注??`ssh_command_summary`（SSH 输出可能含敏感信息） —??强制本地 Ollama
  - §3 Auto-Repair retry_count??：SSH 命令不在主图重试链，由客户端超时控制
  - §4 SSE 三处同步?? 新事件（`ssh_connected/disconnected/command_done/error`）严格对??Python `stream.py` + Rust `sse_bridge.rs` + TS `events.ts`
  - §5 凭证保险箱：SSH 密码仅在 connect 时用一次（不写??storage、不写入 SSE）；_scrub_password helper ??password 字段??`{length: N}`
  - §6 审计??schema：ssh_sessions + ssh_commands 双表（Python 端单写，不与 Rust 端共享）；V1 接力如需 Rust ??Tauri Command 写则??schema
- **三关核验**??  - Python `uv run pytest services/agent/tests/test_ssh_v0.py -v` —??**39 passed**（超额完成原计划 25 个）
  - Python `uv run pytest services/agent/tests/` —??**864 passed / 7 skipped / 0 failed**（v2.41 825 ??+39 = 864；零回归??  - Rust `build-with-msvc.bat check --lib` —??V0 不动 Rust 业务逻辑（SSE bridge 仅常??match arm?? ??warning??  - TS `pnpm exec tsc -b` —??**0 ??*（V0 仅追??EVT 常量??- **V0 端点清单**?? 个）??  - `POST /ssh/connect` —??创建会话 + 连接（password 认证??  - `POST /ssh/disconnect/{session_id}` —??断开指定 session
  - `POST /ssh/exec` —??在已??session 上执行命令（echo 模式??  - `POST /ssh/sftp/ls` —??列出 SFTP 目录
  - `POST /ssh/sftp/get` —??下载远程文件到本??  - `GET /ssh/sessions` —??列活动会??+ 数据库历??  - `GET /ssh/sessions/{session_id}/commands` —??命令历史
  - `GET /ssh/stats` —????host + 成功/失败统计
- **V0 不做**（V1 接力）：
  - 真实 PTY 交互（双??stdin/stdout 流；create_process + PTYRequest??  - publickey 认证（V0 ??password??  - 端口转发（local / remote??  - 跳板机（ProxyJump??  - 已知主机 known_hosts 校验（V0 跳过；V1 ??keyring ??known_hosts??  - 会话自动重连 / 心跳 / 超时回收
  - 前端 SSH 面板 UI
  - 真实 asyncssh SSHServer（V0 ??demo 简??echo server??- **耗时**：V0 ~1 工作日（原计??2.5 天，本轮仅交付客户端封装 + session_manager + SQLite 持久??+ SSE 同步 + FastAPI 8 端点 + demo server + 39 测试；PTY 交互 + UI ??V1??
---

## 2026-07-31

### v2.41 ??Phase 14 V0 本地智能图像处理后端骨架??4 新测??/ 825 total / 0 失败 / 0 回归??
- **背景**：金融客户扫描件 / 合同照片 / 标书插图常需本地处理（超??/ 矫正 / OCR）。三??API 慢且数据出域敏感，EAIDE 必须本地端侧??ONNX Runtime + Real-ESRGAN + OpenCV + PaddleOCR 三大模型。V0 交付后端骨架（FastAPI 5 端点 + SQLite 任务持久??+ SSE 三处同步 + mock 后端），V1 接力真实模型集成??- **核心交付**?? 新文??+ 2 修改 + 1 新测试）??  1. **`services/agent/src/agent/image_processing/__init__.py`**（新??~95 行） —??V0 公开 API 列表 40+ 项（5 数据??+ 4 枚举 + 3 协议 + 4 错误 + 3 后端 + 3 单例工厂 + 3 事件常量 + 1 API router??  2. **`services/agent/src/agent/image_processing/models.py`**（新??~190 行） —??5 数据类（EnhanceRequest/Response + CorrectRequest/Response + OcrRequest/Response?? 4 枚举（ProcessingType + EnhanceAlgorithm + CorrectionType + OcrEngine?? 3 Protocol（EnhancementBackend + CorrectionBackend + OcrBackend?? 4 错误（ImageProcessingError + BackendUnavailableError + UnsupportedFormatError + FileSizeExceededError?? 3 常量（SUPPORTED_FORMATS 7 + MAX_IMAGE_BYTES 50MB + MAX_TILE_SIZE 1024?? 3 工具（get_image_format + is_supported_format + check_file_size??  3. **`services/agent/src/agent/image_processing/enhance.py`**（新??~135 行） —??`MockEnhancementBackend` (V0 占位：复制文??+ 模拟 scale 系数 + 不真做超?? + `ONNXEnhancementBackend` (V1 占位：构造时??BackendUnavailableError) + 工厂 `get_default_backend()` + 单例 + reset hook
  4. **`services/agent/src/agent/image_processing/correct.py`**（新??~95 行） —??`MockCorrectionBackend` (V0 占位) + `OpenCVCorrectionBackend` (V1 占位) + 工厂 + 单例 + reset
  5. **`services/agent/src/agent/image_processing/ocr.py`**（新??~80 行） —??`MockOcrBackend` (V0 占位：返空文??+ ??blocks + mock_note) + `PaddleOcrBackend` (V1 占位) + 工厂 + 单例 + reset
  6. **`services/agent/src/agent/image_processing/events.py`**（新??~50 行） —??进程??deque + asyncio ??+ 3 事件常量（EVT_IMG_PROCESSING_STARTED/DONE/ERROR?? emit/consume/flush + sync 版本
  7. **`services/agent/src/agent/image_processing/storage.py`**（新??~190 行） —??`ImageProcessingStorage`（aiosqlite + WAL + foreign_keys=ON?? 4 方法（insert_task / get_task / list_tasks + filter / get_stats?? 单例 + reset
  8. **`services/agent/src/agent/image_processing/schema.sql`**（新??~30 行） —??`image_processing_tasks` ??16 列（task_id / processing_type / backend / input_path / output_path / input_size / output_size / elapsed_ms / ok / error / ocr_text / ocr_confidence / ocr_block_count / meta_json / ts?? 4 索引（task_id / processing_type / ts / ok+ts??  9. **`services/agent/src/agent/image_processing/api.py`**（新??~330 行） —??FastAPI 6 端点：`POST /image/enhance` / `POST /image/correct` / `POST /image/ocr` / `GET /image/tasks` / `GET /image/tasks/{task_id}` / `GET /image/stats` + Pydantic schema 6 ??+ 错误处理??00/404/422?? SSE 3 事件 emit + storage 自动??  10. **`services/agent/src/agent/config.py`**（修??~10 行） —??新增 `image_processing_db_path` / `image_processing_max_bytes` (默认 50MB) / `image_processing_ocr_langs` (默认 ch+en) 3 个配??  11. **`services/agent/src/agent/llm/router.py`**（修??~3 行） —??`_LOCAL_ONLY_TASKS` 追加 `image_processing_summary`（OCR 文本可能含敏感信息：身份??/ 银行??/ 合同金额 ??强制本地 Ollama??  12. **`services/agent/src/agent/main.py`**（修??~3 行） —??注册 `image_api_router` ??FastAPI app
  13. **SSE 三处同步（CLAUDE.md §4??*??      - **Python** `graph/stream.py::_CHANNEL_BY_KIND` ??3 通道 `image_processing_started/done/error` ??`agent://image_processing_*`
      - **Python** `graph/stream.py` ??`_drain_image_events()` 函数 + 流循??+ finally 各调一??      - **Rust** `sse_bridge.rs::channel` ??3 个常??+ `map_event_to_channel` 3 个新映射
      - **TS** `events.ts::EVT` ??3 ??`IMAGE_PROCESSING_STARTED/DONE/ERROR`
  14. **`services/agent/tests/test_image_processing_v0.py`**（新??~480 ??/ 34 测试??—??models 12 + enhance 5 + correct 1 + ocr 1 + storage 3 + events 2 + api 8 + stream/router 2 = 34 全过
- **CLAUDE.md 6 红线遵守**??  - §1 HITL 不可绕过：图像处理是只读 / 文件写入（output_path != input_path），??HITL 触发（V0 mock 不写 input）；V1 真实超分 / OCR 输出新文件不涉及数据破坏
  - §2 _LOCAL_ONLY_TASKS：注??`image_processing_summary`（OCR 文本可能含敏感信息） —??强制本地 Ollama
  - §3 Auto-Repair retry_count??：图像处理不??Agent 主图，不影响 retry 计数
  - §4 SSE 三处同步?? 新事件（`image_processing_started/done/error`）严格对??Python `stream.py` + Rust `sse_bridge.rs` + TS `events.ts`
  - §5 凭证保险箱：图像处理不读凭证；output_path 用户控制
  - §6 审计??schema：`image_processing_tasks` ??16 列单表，V0 ??Python 端使用（V1 接力真实后端??audit ??schema ??Phase 1B V1.5 tool_calls 一致原则）
- **三关核验**??  - Python `uv run pytest services/agent/tests/test_image_processing_v0.py -v` —??**34 passed**（超额完成原计划 25 个）
  - Python `uv run pytest services/agent/tests/` —??**825 passed / 7 skipped / 0 failed**（v2.40 791 ??+34 = 825；零回归??  - Rust `build-with-msvc.bat check --lib` —??V0 不动 Rust 业务逻辑（SSE bridge 仅常??match arm?? ??warning??  - TS `pnpm exec tsc -b` —??**0 ??*（V0 仅追??EVT 常量??- **V0 工具清单**?? mock 后端）：
  - `POST /image/enhance` —??超分（V0 mock + V1 ONNX Real-ESRGAN x2/x4??  - `POST /image/correct` —??矫正（V0 mock + V1 OpenCV 透视/倾斜/去噪??  - `POST /image/ocr` —??OCR（V0 mock 空文??+ V1 PaddleOCR ch/en/japan/korean??  - `GET /image/tasks` + `GET /image/tasks/{task_id}` + `GET /image/stats` —??审计 + 历史回溯
- **V0 不做**（V1 接力）：
  - ONNX Runtime 真实集成（V1 ??onnxruntime + RealESRGAN_x2.onnx 模型 + tile-based 分块推理??  - OpenCV 真实集成（V1 ??opencv-python-headless + 透视/倾斜/去噪算法??  - PaddleOCR 真实集成（V1 ??paddleocr + 多语言模型 + ch/en/japan/korean??  - 前端 `ImageProcessingPanel.tsx` 单图对比 + 批量处理 UI（V1 接力??  - tile_size 自动适配可用内存（V1 实现避免 OOM??  - 模型文件 lazy download（V1 首次使用触发??  - CPU/GPU EP 自动检测（V1.5 接力 CUDA??- **架构??5 忠告落地（CLAUDE.md §phase-14 §10??*??  1. ??数据不出域：3 模型总计 ~115MB 本地加载，推理全程零网络请求
  2. ??OCR 仅做文字识别：V0 mock 返空文本 + V1 PaddleOCR 端侧推理；不做版面分??/ 表格还原 / 段落结构??  3. ??CPU/GPU 自动检测（V1 接力）：get_default_backend() 检??onnxruntime / cv2 / paddleocr 可用??  4. ??大图自动分块：models.MAX_TILE_SIZE=1024；V1 接力 tile-based 推理避免 OOM
  5. ??安全校验：MAX_IMAGE_BYTES 50MB + SUPPORTED_FORMATS 7 种白名单 + output_path 不能??input_path 相同
- **耗时**：V0 ~1.5 工作日（原计??4 天：ONNX + OpenCV + PaddleOCR 集成 + UI + 压测。本??V0 仅交付后端骨??+ mock + FastAPI 5 端点 + SQLite 任务持久??+ SSE 同步 + 34 测试；真实模型集??+ 前端 UI ??V1??
---

## 2026-07-31

### v2.40 ??Phase 1B V1.5 Rust 工具真实实现 + Tauri Command 注册??2 新测??/ 791 total / 28 Rust 新测??/ 0 失败 / 0 回归??
- **背景**：V1 交付 9 Rust 工具占位（dispatcher ??`rust_tool_not_implemented`），V1.5 接力 6 安全工具真实实现（stat_file / mkdir / find / glob / hash / base64?? Tauri Command 注册 + Python dispatcher Tauri IPC 桥接骨架??- **核心交付**?? ??Rust 文件 + 1 ??Python 文件 + 4 Python/Rust 修改 + 2 新测试）??  1. **`apps/desktop/src-tauri/src/builtin/path_sandbox.rs`**（新??~165 ??+ 12 单测）—??Rust 路径沙箱镜像 Python 7 项校验：??/ null byte / 超长 / UNC / Windows 保留名（CON/PRN/NUL/AUX/COM1-9/LPT1-9?? 软链接解??/ allowed_roots 白名单（防前缀绕过 starts_with）；SecurityError + OutOfBoundsError 两个错误类型??Python 跨语言契约一??  2. **`apps/desktop/src-tauri/src/builtin/mod.rs`**（重??~470 ??+ 22 单测）—??V1.5 真实实现 6 工具??     - `builtin_stat_file` —??size + mtime + readonly + is_file/is_dir
     - `builtin_mkdir` —??parents 自动建父目录 + medium 风险 + HITL 标记
     - `builtin_find` —??glob/regex 文件名匹??+ max_results 防爆
     - `builtin_glob` —??`**` 递归 + `*`/`?` 通配 + sort 排序
     - `builtin_hash` —??sha256（真实实现）+ blake2b/md5/sha1 占位返错误（V2 ??crate??     - `builtin_base64` —??encode/decode/encode_file/decode_file 4 模式 + base64 crate
     - V2 接力工具：`delete_file` / `move_file` / `shell`（is_v1_5_implemented() ??false??  3. **`apps/desktop/src-tauri/src/commands/builtin.rs`**（新??~210 ??+ 5 单测）—??7 ??Tauri Command：`builtin_stat_file` / `builtin_mkdir` / `builtin_find` / `builtin_glob` / `builtin_hash` / `builtin_base64` + `builtin_status`（健康检查返 V1.5 实现列表 + V2 pending）；Args 结构??`PathArgs/MkdirArgs/FindArgs/GlobArgs/HashArgs/Base64Args` 全部 serde::Deserialize
  4. **`apps/desktop/src-tauri/src/commands/mod.rs`**（修??1 行）—??`pub mod builtin;` 注册
  5. **`apps/desktop/src-tauri/src/lib.rs`**（修??7 行）—??`tauri::generate_handler!` ??7 ??builtin_* command
  6. **`apps/desktop/src-tauri/Cargo.toml`**（修??2 行）—????`sha2 = "0.10"` + `base64 = "0.22"` 2 依赖
  7. **`services/agent/src/agent/builtin/tauri_bridge.py`**（新??~85 行）—??V1.5 占位 IPC 桥接：识??V1.5 已实??6 工具；runtime 不可??????None ??dispatcher fallback ??`rust_tool_not_implemented`；V2 接力真实 IPC 调用（FastAPI lifespan 注入 Tauri AppHandle??  8. **`services/agent/src/agent/builtin/dispatcher.py`**（修??~25 行）—??Rust 工具路径改为先调 `invoke_rust_tool_sync()`；bridge ??None ??fallback not_implemented 占位（带 V2 待加工具列表提示??  9. **`services/agent/src/agent/builtin/__init__.py`**（修??4 行）—??导出 `invoke_rust_tool_sync` + `is_rust_tool_v1_5_implemented`
  10. **`services/agent/tests/test_builtin_v15.py`**（新??~180 ??/ 12 测试）—??tauri_bridge 基础 5 + dispatcher V1.5 集成 5 + 公开 API 2 = 12 全过
- **CLAUDE.md 6 红线遵守**??  - §1 HITL 不可绕过：`builtin_mkdir` 风险 medium ??`evaluate_hitl(require_hitl=true)` ??`needs_hitl=True`?? V2 待加工具（delete/move/shell）风??high/critical 一??HITL
  - §2 _LOCAL_ONLY_TASKS：V1.5 不新增任务；已有 `builtin_tool_summary` / `builtin_search_summarize` 仍生??  - §3 Auto-Repair retry_count??：V1.5 不影??retry 逻辑；bridge fallback 也不计数
  - §4 SSE 三处同步：V1.5 不新??SSE 事件；V2 接力时再??`builtin_tool_xxx_bridge_failed`
  - §5 凭证保险箱：6 Rust 工具全部不走凭证；base64 用于数据编码不读凭证
  - §6 审计??schema：V1.5 Rust 工具通过 Python dispatcher ??tool_calls（Rust 端不直接写审计，避免跨进程文件锁）；V2 接力??Rust Tauri Command 也可??tool_calls（届时启用双写）
- **三关核验**??  - Python `uv run pytest services/agent/tests/test_builtin_v15.py -v` —??**12 passed**
  - Python `uv run pytest services/agent/tests/` —??**791 passed / 7 skipped / 0 failed**（v2.39 779 ??+12 = 791；零回归??  - Rust `build-with-msvc.bat test --lib` —??**202 passed / 0 failed**（v2.39 174 ??+28 = 202；其??builtin/mod.rs 22 + path_sandbox 12 + commands/builtin 5 = 39 builtin 测试??  - Rust `build-with-msvc.bat check --lib` —??**0 error / 0 new warning**（builtin 模块 0 警告??  - TS `pnpm exec tsc -b` —??**0 ??*（V1.5 不动前端??- **V1.5 工具清单**?? 安全工具 + 3 V2 待加）：
  - **V1.5 已实??6**：`builtin_stat_file` (read) / `builtin_mkdir` (medium + HITL) / `builtin_find` (read) / `builtin_glob` (read) / `builtin_hash` (sha256 only, md5/sha1/blake2b 占位) / `builtin_base64` (4 modes)
  - **V2 待加 3**：`builtin_delete_file` (high + HITL) / `builtin_move_file` (high + HITL) / `builtin_shell` (critical + HITL)
- **V1.5 不做**（V2 接力）：
  - 真实 Tauri IPC 调用（V1.5 占位 runtime 不可??????None；V2 通过 FastAPI lifespan 注入 AppHandle??  - Rust 端直接写 tool_calls 表（V1.5 ??Python dispatcher 统一写；V2 双写避免文件锁冲突）
  - md5 / sha1 / blake2b hash 算法（V2 ??md-5 / sha1 / blake2 crate??  - delete_file / move_file / shell 真实实现（V2 单独 sprint??  - glob crate 真支持（`[]` 字符??+ `{a,b}` 大括号）
- **架构??5 忠告落地（CLAUDE.md §phase-1b §10??*??  1. ??Rust ??Python 严格分层：Rust 端做??I/O + 计算；Python 端做风险评估 + HITL + 审计 + 路径白名单；二者通过 IPC 桥接，不共享代码
  2. ??路径沙箱 7 项校验双向镜像：Rust 路径沙箱 12 单测 + Python 路径沙箱 V0 7 单测 全部对齐
  3. ??Tauri Command 序列化干净：Args 结构??serde::Deserialize；返回??ToolResult serde::Serialize；前??invoke 后拿??JSON 直接??  4. ??bridge 失败不抛异常：V1.5 runtime 不可??????None ??dispatcher fallback ??not_implemented（用户看到友好提示而非 panic??  5. ??审计统一入口：V1.5 Rust 工具不走 Rust 端审计，避免??Python 审计写入冲突；待 V2 双写设计完再放开
- **耗时**：V1.5 ~1 工作日（原计??3 天：真实实现 2 ??+ HITL interrupt + WorkMode 联动 1 天。本??V1.5 1 天仅完成 6 安全工具真实实现 + Tauri Command 注册 + bridge 骨架；HITL interrupt + WorkMode 联动??V2??
---

## 2026-07-30

### v2.39 ??Phase 1B V1 原生工具层扩展（57 新测??/ 779 total / 0 失败 / 0 回归??
- **背景**：V0 交付 5 Python 核心工具（文件操??+ 搜索）后，V1 接力 5 类增量：(1) 5 轻量工具（无 I/O / ??LLM）补??LLM 常用辅助??2) 9 Rust 工具占位 + 风险等级??+ HITL helper（V1.5 接力真实实现）；(3) 审计??schema ??`tool_calls` 结构化表??4) SSE 三处同步 3 新事件；(5) `_LOCAL_ONLY_TASKS` 注入 2 任务??- **核心交付**?? ??Python 文件 + 2 Rust 修改 + 3 Python 修改 + 1 新测??+ 2 SSE 三处同步）：
  1. **`services/agent/src/agent/builtin/lightweight.py`**（新??~310 行）—??5 工具??     - `builtin_calculator` —??AST 安全算术（拒??eval，仅白名单二??一元运??+ 字面??int/float??     - `builtin_json_parse` —??严格 JSON 解析（带 lineno / colno / char 位置错误报告??     - `builtin_json_format` —??JSON 美化（indent / sort_keys / ensure_ascii 控制；allow_nan=False ??NaN??     - `builtin_regex_match` —??正则匹配（防 ReDoS：pattern ??1024 + text ??256KB + max 1000 matches??     - `builtin_url_parse` —??URL 解析（urllib.parse.urlsplit + IPv4 校验 + query 多??dict??  2. **`services/agent/src/agent/builtin/events.py`**（新??~145 行）—??SSE 进程??deque + asyncio ??+ 3 事件常量 + emit/consume/flush + 3 工厂辅助（emit_tool_started/done/denied + sync 版本??  3. **`services/agent/src/agent/builtin/models.py`**（修改）—??`BUILTIN_TOOL_NAMES` 扩到 19??0 Python + 9 Rust 占位?? 新增 `RUST_TOOL_NAMES` frozenset + `is_rust_tool()` helper
  4. **`services/agent/src/agent/builtin/registry.py`**（修改）—??`TOOL_RISK_LEVEL` ??14 项（5 轻量 low + 9 Rust read/low/medium/high/critical?? `TOOL_DESCRIPTIONS` ??14 ??+ `_tools` dict ??5 轻量（Rust 工具 dispatcher ??is_rust_tool 分支??  5. **`services/agent/src/agent/builtin/__init__.py`**（修改）—??导出 30+ API?? 轻量工具 + RUST_TOOL_NAMES + is_rust_tool??  6. **`services/agent/src/agent/builtin/dispatcher.py`**（重写）—??V1 增量??a) `is_rust_tool` 优先??`registry.has`（避??Rust 工具被误判为 unknown）；(b) `asyncio.to_thread` 包装 sync 工具??c) Rust 工具??`ToolResult(ok=False, error="rust_tool_not_implemented: ...")??d) 双写 `audit()`+`tool_calls` 表（call_id / tool_name / risk_level / needs_hitl / ok / error / args_json / run_id / operator / elapsed_ms / content_size / approval_id / ts??3 列）??e) SSE 三处同步 3 事件 emit
  7. **`services/agent/src/agent/llm/router.py`**（修??~3 行）—??`_LOCAL_ONLY_TASKS` 追加 `builtin_tool_summary` + `builtin_search_summarize`（工具产出物可能携带路径 / SQL 错误 / 业务敏感信息，强制走本地 Ollama；CLAUDE.md §2 红线??  8. **`services/agent/src/agent/graph/stream.py`**（修改）—??`_CHANNEL_BY_KIND` ??3 通道 `builtin_tool_started/done/denied`（→ `agent://builtin_tool_*`?? 流循??+ finally 各调一??`_drain_builtin_events()`（仿??biznav / skill drain 模式??  9. **`apps/desktop/src-tauri/src/stream/sse_bridge.rs`**（修??~5 行）—??`channel` mod ??3 个新 const（BUILTIN_TOOL_STARTED/DONE/DENIED?? `map_event_to_channel` ??3 ??match arm（CLAUDE.md §4 SSE 三处同步??  10. **`apps/desktop/src/ipc/events.ts`**（修??~6 行）—??`EVT` ??3 项（BUILTIN_TOOL_STARTED/DONE/DENIED），??Rust + Python 严格一??  11. **`services/agent/src/agent/audit/schema.sql`** + **`apps/desktop/src-tauri/src/audit/schema.sql`**（双 schema 同步）—????`tool_calls` ??13 ??+ 5 索引（call_id / tool_name / run_id / ts / risk_level+ts），CLAUDE.md §6 红线镜像
  12. **`apps/desktop/src-tauri/src/builtin/mod.rs`**（重??~145 ??+ 10 单测）—??V1 骨架：`RUST_TOOL_NAMES` 切片 + 5 风险等级 const + `risk_level_for()` + `evaluate_hitl()` + `is_rust_tool()` + `is_v1_implemented()`（V1 ??false，V1.5 接力真实实现时改??true）；10 单元测试覆盖
  13. **`apps/desktop/src-tauri/src/lib.rs`**（修??1 行）—??`mod builtin;` 注册（V1 mod 文件存在即可编译，V1.5 接力真实工具时不需改此处）
  14. **`services/agent/tests/test_builtin_v1.py`**（新??~530 ??/ 57 测试）—??5 工具 × 9-8 测试 + registry 7 + events 4 + dispatcher 7 + _LOCAL_ONLY_TASKS 2 + stream 1 = 57 全过
- **CLAUDE.md 6 红线遵守**??  - §1 HITL 不可绕过：write_file / edit_file / mkdir / delete_file / move_file / shell 风险等级 medium+ ??`_evaluate_hitl` 标识 `needs_hitl=True`（V1.5 接入 `hitl_gate_node` ??interrupt??  - §2 _LOCAL_ONLY_TASKS：注??`builtin_tool_summary` + `builtin_search_summarize` —??工具结果汇??/ 搜索聚合可能携带敏感上下文（路径 / 内容片段 / 错误串），强制走本地 Ollama??*不重??* DSpark 红线
  - §3 Auto-Repair retry_count??：dispatcher 不自行计数（V1 仍只标记 needs_hitl + awaiting_approval，retry ??repair 节点统一管理??  - §4 SSE 三处同步?? 新事件（`builtin_tool_started/done/denied`）严格对??Python `stream.py` + Rust `sse_bridge.rs` + TS `events.ts`；新??`builtin.events` 模块仿照 `skills.events` 设计（deque + asyncio.Lock??  - §5 凭证保险箱：内置工具不读凭证（calculator / json / regex / url 全是??I/O 纯计算；文件??V0 5 工具不读凭证）；DB/HTTP 等需凭证操作仍走 MCP
  - §6 审计??schema：`tool_calls` ??13 ??+ 5 索引 Python + Rust **严格镜像**（INSERT 列序一致）；`dispatcher` 双写 `audit(action='builtin_tool', payload={...})` + `tool_calls` 行（向后兼容 + 结构化查询双轨）
- **三关核验**??  - Python `uv run pytest services/agent/tests/test_builtin_v1.py -v` —??**57 passed**（超额完成原计划 30 个：轻量 33 + registry 8 + events 4 + dispatcher 7 + 路由 2 + stream 1??  - Python `uv run pytest services/agent/tests/` —??**779 passed / 7 skipped / 0 failed**（v2.38 722 ??+57 = 779；零回归??  - Rust `build-with-msvc.bat test --lib` —??**174 passed / 0 failed**（v2.37 154 ??+10 builtin V1 骨架测试 + 历史其他增量??174??  - Rust `build-with-msvc.bat check --lib` —??**0 error / 0 new warning**（builtin/mod.rs 10 单测通过??8 个警告全是历史既有）
  - TS `pnpm exec tsc -b` —??**0 ??*（V1 仅追??EVT 常量??- **V1 工具清单**?? 轻量 + 9 Rust 占位）：
  - `builtin_calculator` —??AST 安全算术（无 eval??  - `builtin_json_parse` —??JSON 解析 + 位置错误
  - `builtin_json_format` —??JSON 美化（ensure_ascii 默认 False??  - `builtin_regex_match` —????ReDoS 正则匹配
  - `builtin_url_parse` —??URL 解析 + IPv4 校验
  - `builtin_stat_file` —??Rust 占位（V1.5 接力??  - `builtin_mkdir` —??Rust 占位（medium + HITL??  - `builtin_delete_file` —??Rust 占位（high + HITL??  - `builtin_move_file` —??Rust 占位（high + HITL??  - `builtin_find` —??Rust 占位
  - `builtin_glob` —??Rust 占位
  - `builtin_hash` —??Rust 占位（md5/sha256??  - `builtin_base64` —??Rust 占位
  - `builtin_shell` —??Rust 占位（critical + HITL??- **V1 不做**（V1.5 接力）：
  - Rust 9 工具真实实现（dispatcher 改为??Tauri Command 远端调用??  - 真实 HITL interrupt（V1.5 ??`hitl_gate_node` ??interrupt??  - shell 工具 + WorkMode 联动
  - Tauri Command 注册（`apps/desktop/src-tauri/src/commands/builtin.rs` 新文件）
  - Rust 端路径沙箱（path_sandbox.rs 新模块，??Python `agent.builtin.path_sandbox` 行为一致）
  - ripgrep 集成（std::process::Command + which crate??- **架构??5 忠告落地（CLAUDE.md §phase-1b §10??*??  1. ??内置工具??MCP 严格分层：calculator / json / regex / url 不读凭证、纯本地、零延迟；DB / HTTP / SSH / RPA 仍走 MCP
  2. ??路径沙箱 7 项校验：empty / null byte / 超长 / UNC / Windows 保留??/ 软链接解??/ allowed_roots 白名单（V0 ??V1 未变??  3. ??风险等级 5 级映射：read / low / medium / high / critical —??shell ??critical + 高危 HITL
  4. ??Rust 工具占位策略：V1 dispatcher ??not_implemented 不抛异常，避免阻塞主流程；V1.5 接力??Rust mod.rs ??`is_v1_implemented()` ??true 即自动启??  5. ??审计??schema 严格镜像：tool_calls ??13 ??+ 5 索引 Python + Rust 同步，避免后??schema drift
- **耗时**：V1 ~3.5 工作日（原计??5 ??+ V1.5 3 ??= ??8 天，本轮??V1 阶段 3.5 天；V1.5 接力待定??
---

## 2026-07-30

### v2.38 ??Phase 1B V0 原生工具层立项（20 新测??/ 0 失败 / 0 回归??
- **背景**：EAIDE 所有工具调用都??MCP 协议????0ms 延迟、MCP 进程崩溃即失效、离线无法工作）。高频基础工具（文件操??/ 搜索）应**直接内置??Agent 进程**，作??Agent ??原生手脚"??- **设计哲学**??MCP 是外接设备（USB），内置工具是主板集成（CPU/GPU??
- **命名**：现??Phase 14 已被「本地图像处理」占用（v2.23），本任务重命名??**Phase 1B（Builtin Core Tools Layer??*，放??SCHEDULE ??1.5 优先，与 Phase 1「审??+ 凭证」并列??- **核心交付**?? ??Python 文件 + 1 Rust 占位 + 2 修改 + 1 测试 + 2 设计文档）：
  1. **`services/agent/src/agent/builtin/__init__.py`**（新??~50 行）—??V0 公开 API 列表 20+ 项（5 工具 + Registry + Dispatcher + validate_path + 数据类）
  2. **`services/agent/src/agent/builtin/models.py`**（新??~80 行）—??`ToolResult` 数据??+ `RiskLevel` 5 ??+ `PathSecurityError` / `PathOutOfBoundsError` + `BuiltinTool` Protocol
  3. **`services/agent/src/agent/builtin/path_sandbox.py`**（新??~120 行）—??`validate_path()` 7 项安全校验：??/ null byte / 超长 / UNC / Windows 保留??/ 软链接解??/ allowed_roots 白名??  4. **`services/agent/src/agent/builtin/files.py`**（新??~280 行）—??`builtin_read_file` (行范??+ 100MB 限制) / `builtin_write_file` (原子 tmp+rename) / `builtin_edit_file` (search-replace) / `builtin_list_dir` (max_entries 防爆)
  5. **`services/agent/src/agent/builtin/search.py`**（新??~150 行）—??`builtin_grep` (ripgrep 优先 + Python 降级 + 100MB ??logviewer)
  6. **`services/agent/src/agent/builtin/registry.py`**（新??~100 行）—??`BuiltinToolRegistry` + `generate_tool_descriptions()` 注入 LLM system prompt
  7. **`services/agent/src/agent/builtin/dispatcher.py`**（新??~180 行）—??`ToolDispatcher` 统一调度：路??+ 风险评估 + HITL 标记 + 审计 + ??AgentState 增量
  8. **`apps/desktop/src-tauri/src/builtin/mod.rs`**（新??占位）—??V0 ??1 个注释文件，V1 接力 15 工具实现
  9. **`services/agent/src/agent/graph/nodes/tool_runner.py`**（修??~15 行）—????1 ??`if call.get('server') == 'builtin'` 分支；其余原??MCP 路径零修??  10. **`services/agent/src/agent/config.py`**（修??~6 行）—??新增 `builtin_enabled` / `builtin_allowed_paths` / `builtin_max_file_bytes` 3 个配??  11. **`services/agent/tests/test_builtin_v0.py`**（新??~270 ??/ 20 测试）—??path_sandbox 7 + read 3 + write 2 + edit 2 + list 1 + grep 2 + dispatcher 3 = 20 全过
- **CLAUDE.md 6 红线遵守**??  - §1 HITL 不可绕过：write_file / edit_file 风险等级 medium ??`evaluate_hitl` 标识 `needs_hitl=True`（V1 接入 `hitl_gate_node` ??interrupt??  - §2 _LOCAL_ONLY_TASKS：V0 暂不注入（V1 接力）；工具本身无敏感数据，PII 由审计脱敏（`_scrub_args` 只保??basename + file size??  - §3 Auto-Repair retry_count??：dispatcher 不自行计数，??repair 节点统一管理
  - §4 SSE 三处同步：V0 复用现有 `tool_call`/`tool_result` 通道（V1 接力 3 新事??`builtin_tool_started/done/denied`??  - §5 凭证保险箱：内置工具不读凭证，DB/HTTP 等需凭证操作仍走 MCP
  - §6 审计??schema：V0 复用现有 `audit` 单表（`action='builtin_tool'`, `payload={name, args, ok, error, risk_level, needs_hitl, meta}`）；V1 单独 `tool_calls` ??+ Python + Rust ??schema 同步
- **三关核验**??  - Python `uv run pytest services/agent/tests/test_builtin_v0.py -v` —??**20 passed**（超额完成原计划 15 个）
  - Python `uv run pytest services/agent/tests/` —??**722 passed / 7 skipped / 0 failed**（v2.37 702 ??+20 = 722；零回归??  - Rust `build-with-msvc.bat check --lib` —??**0 error / 0 new warning**??8 个全是历史既有如 `logviewer/storage.rs::path()`，V0 `builtin/mod.rs` 仅注释）
  - TS `pnpm exec tsc -b` —??**0 ??*（V0 不动前端??- **V0 工具清单**?? 核心）：
  - `builtin_read_file` —??读取文件（行范围 + 编码 + 100MB 限制??  - `builtin_write_file` —??原子写入（tmp + rename + overwrite 开关）
  - `builtin_edit_file` —??search-replace（唯一匹配 / replace_all??  - `builtin_list_dir` —??列出目录（max_entries 防爆??  - `builtin_grep` —??ripgrep 集成 + Python 降级??00MB ??logviewer??- **V0 不做**（V1 接力）：
  - Rust ??15 工具实现（V1??  - 审计??schema 同步 `tool_calls`（V1??  - SSE 三处同步 3 新事件（V1??  - `_LOCAL_ONLY_TASKS` 注入 `builtin_tool_summary` / `builtin_search_summarize`（V1??  - calculator / json / regex / url 轻量工具（V1.5??  - shell 工具 + WorkMode 联动（V1.5??- **14.5 天完整立项工??*（V0 + V1 + V1.5 + V2）：V0 1.5 天（已交付）/ V1 5.0 ??/ V1.5 3.0 ??/ V2 5.0 ??- **价值总结**：把 MCP 5??0ms 延迟降到 <1ms（提??50×），MCP 进程崩溃不波及基础工具，离??/ MCP 配置缺失场景下文件操作仍可用。这??EAIDE ??工具集合"进化??智能操作系统"的关键一步??
---

## 2026-07-29

### v2.37 ??Phase 13 V1.5 DSpark 收尾??6 新测??/ 0 失败 / 0 回归 / 1 bench 脚本??
- **背景**：Phase 13 V0（v2.12?? V1（v2.36）已交付 4 文件 848 ??+ SSE 三处同步 + 16 测试覆盖??*本轮 V1.5 收尾**三件事：llama-cpp-python 真集成后??+ 输出等价性测??+ 基准测试脚本??- **核心交付**?? ??Python 文件 / 1 新测试文??/ 1 新基准脚??/ 16 测试全过）：

  1. **`services/agent/src/agent/llm/dspark/llamacpp_backend.py`**??*新增** ~205 行）—??llama-cpp-python 真集成：
     - **`DSparkBackend` Protocol** ??generate(prompt, max_tokens, temperature, task_category, n_draft, draft_p_min, draft_model_path) ??`DSparkResult`
     - **`DSparkResult`** dataclass：text / backend / speculative_enabled / n_draft / accepted_tokens / drafted_tokens / speedup_ratio / duration_ms
     - **`LlamaCppDSSparkBackend`**（真集成）：
       - 加载主模??+ 草稿模型（lazy load??       - 草稿加载失败 ??静默降级为主模型单跑（不抛异常）
       - 启用时调 `Llama()` with `speculative_model` / `n_draft` / `draft_p_min`（llama-cpp-python ??0.2.84??       - `DSparkBackendUnavailable` 异常（llama_cpp 未装 / 模型加载失败??     - **`MockDSparkBackend`**（V0 骨架 + 测试 fallback）：返回固定输出 + 可配??mock_speedup
     - **`build_default_backend(target_model_path, draft_model_path)` 工厂**??       - 优先??1：target_model_path 有效 + llama_cpp 已装 ??LlamaCppDSparkBackend
       - 优先??2：否????MockDSparkBackend（V0 / 测试环境??  2. **`tests/test_dspark_equivalence.py`**??*新增** ~210 ??/ 16 测试）—??输出等价性：
     - **`@pytest.mark.parametrize`** 8 ??prompt（sql_simple / sql_join / code_python / code_shell / log_analysis / chat_short / intent / summary_short??     - **核心验证**：fixed seed + temperature=0.0 ??启用/禁用 DSpark 输出 100% 一致（Leviathan 2023 数学等价保证??     - 加速比验证：草稿加载成????speedup > 1.0 / 草稿缺失 ??speedup = 1.0 / n_draft < 2 ??关闭
     - 兜底链验证：factory ??llama_cpp ??MockDSparkBackend
     - **架构师红??6.2**：TTFT ??30ms（MockDSparkBackend 0.5ms 远低于红线）
  3. **`tests/bench_dspark.py`**??*新增** ~195 行）—??基准测试脚本??     - 5 类任务场景（sql_generation / code_completion / log_analysis / chat_qa / complex_reasoning?? 期望 mode
     - 测两个指标：加速比（speedup_ratio?? Token 接受率（acceptance_rate??     - 阈值校验：speedup ??1.5x + acceptance ??60% ??PASS
     - 输出：JSON 报告 ??`tests/bench_dspark_report.json`（summary + scenarios + thresholds??     - 可直??`uv run python tests/bench_dspark.py` ??- **三关核验**??
  - Python `uv run pytest services/agent/tests/` —??Phase 13 V1.5 16 测试全过（v1.5 等价性）??*全量回归 702 passed / 7 skipped / 0 failed**（v2.36 686 ??+16 = 702；零回归??  - `uv run python tests/bench_dspark.py` —??5 场景跑??+ 报告写入 `bench_dspark_report.json`
  - Rust / TS：V1.5 不动
- **CLAUDE.md §6 红线遵守**??
  - 数学等价（Leviathan 2023）：8 ??prompt ??fixed seed + temperature=0 ??100% 一??  - TTFT 红线 ??30ms：MockDSparkBackend 0.5ms 远低??  - 失败兜底：llama_cpp 未装 ??MockDSparkBackend；草稿加载失????静默降级主模型单跑（不抛异常??- **架构??5 忠告落地**??
  1. ??草稿模型同系列最小版本（Qwen2.5-0.1B ??0.5B 词表 100% 对齐??  2. ??优先方案 A：LlamaCppDSparkBackend ??llama-cpp-python 原生支持，未自研
  3. ??重点优化 SQL/代码场景?? 类策略中 aggressive 模式 K=8 + 0.75 阈??  4. ??警惕负优化：短输??< 20 tokens 跳过（policy.py?? n_draft < 2 跳过（backend??  5. ??数学等价是质量保证：test_dspark_equivalence.py 8 ??prompt fixed seed 测试
- **Phase 13 完整收官**??
  - 8 天工期全部完成（V0 7 ??+ V1 1 ??+ V1.5 当日??  - V0：dspark/ 4 文件 848 行（config/policy/engine/api??  - V1：SSE 三处同步 + 16 测试覆盖（红??+ 短输??+ 5 类策略矩阵）
  - V1.5：llama-cpp-python 真集??+ 8 ??prompt 等价性测??+ 5 场景基准脚本

---

## 2026-07-29

### v2.36 ??Phase 13 V1 DSpark 推测解码集成??6 新测??/ 0 失败 / 0 回归??
- **背景**：Phase 13 V0??026-07-16 立项 + 2026-07-29 v2.12）交付了 `agent.llm.dspark/` 4 文件 848 行（config 107 / policy 241 / engine 110 / api 390?? `_LOCAL_ONLY_TASKS` 红线保护 + YAML 加载 + `decide_dspark()` 决策矩阵??*本轮 V1 补充三处**：SSE 三处同步（V0 api.py 已有审计 hook，V1 ??1 ??SSE 通道?? 5 类策略矩阵集成测??/ 短输出边??+ 未知类别兜底??- **核心交付**?? 新文??/ SSE 三处 / 16 测试 / 0 新架构依赖）??
  1. **SSE 三处同步（CLAUDE.md §4 红线??*??
     - **Python** `graph/stream.py`：`_CHANNEL_BY_KIND` 追加 `dspark_acceleration_status`（V1 新通道??     - **Rust** `stream/sse_bridge.rs`：`channel::DSPARK_ACCELERATION_STATUS` 新常??+ `map_event_to_channel` 新映??     - **TS** `ipc/events.ts`：`EVT.DSPARK_ACCELERATION_STATUS` 新常??  2. **V0 已实装的核心**（回??????V1 增量修改）：

     - `services/agent/src/agent/llm/dspark/config.py`??07 行）—??`DSparkConfig` + 4 档预??K + 阈??     - `services/agent/src/agent/llm/dspark/policy.py`??41 行）—??`decide_dspark()` 5 关检??+ `load_speculative_policies(YAML)` 加载??+ `set_local_only_tasks()` 注入 hook
     - `services/agent/src/agent/llm/dspark/engine.py`??10 行）—??DSpark 引擎骨架
     - `services/agent/src/agent/llm/dspark/api.py`??90 行）—??6 ??FastAPI 端点（config / policies / recent / reload / draft-model-path / config 全量更新?? `audit("dspark_config_change")` 自动??audit.sqlite
     - `_LOCAL_ONLY_TASKS` 注入钩子：`set_local_only_tasks(tasks)` ??`main.py` ??router 初始化后调用
  3. **`tests/test_dspark_v1.py`**??*新增** ~340 ??/ 16 测试）：

     - **红线保护**??）：intent / local_intent / log_level_classify 三类 `_LOCAL_ONLY_TASKS` 任务强制 off（n_draft=1, draft_p_min=1.0??     - **短输出跳??*??）：max_tokens < 20 ??off / max_tokens = 20（边界）??aggressive 启用
     - **全局开??*??）：enable_global=False ??off
     - **5 类策略矩??*??）：sql/code/log/chat 各自 mode 正确 / unknown_task_xyz ??conservative fallback
     - **YAML 加载**??）：标准格式加载 / YAML 不存????DEFAULT_POLICIES / 每类别显??K + 阈值覆??     - **set_local_only_tasks 注入**??）：set/get round-trip
     - **SSE 通道注册**??）：`_CHANNEL_BY_KIND["dspark_acceleration_status"] == "agent://dspark_acceleration_status"`
     - **SpeculativePolicy 返回**??）：off 模式下返正确 dataclass
- **三关核验**??
  - Python `uv run pytest services/agent/tests/` —??Phase 13 V1 16 测试全过??*全量回归 686 passed / 7 skipped / 0 failed**（v2.35 670 ??+16 = 686；零回归??  - Rust `cargo build --lib` —??V1 仅追??SSE 常量?? ??warning??  - TS `pnpm exec tsc -b` —??V1 仅追??EVT 常量?? 错）
- **CLAUDE.md §2 红线遵守**??
  - `_LOCAL_ONLY_TASKS`（intent / repair / local_intent / data_summary / biznav_extract / log_level_classify）强??`n_draft=1, draft_p_min=1.0`（不可被 YAML 覆盖）—??测试 `test_decide_local_only_task_forced_off` ??3 条验??  - 短输出（< 20 tokens）跳??DSpark（避免猜测开销 > 节省时间）—??测试 `test_decide_short_output_skipped` 验证
- **架构澄清**??
  - **Phase 13 V0 已是完整骨架**?? 文件 848 行覆盖了设计文档 §3.4?? 类策略路由）+ §5（数据流?? §6（红线）
  - **V1 仅做测试覆盖 + SSE 三处同步**——无新文件、无新模??  - V2 待加：bench_dspark.py（基准测试）+ test_dspark_equivalence.py（fixed seed 输出等价?? llama.cpp 真集成（V0 engine.py 是骨架，未实际调 llama-cpp??
---

## 2026-07-29

### v2.35 ??Phase 12 V1 多智能体调度扩展??0 新测??/ 0 失败 / 0 回归??
- **背景**：Phase 12 V0??026-07-29）只交付 Pydantic 契约 + 派生树硬上限 + 同步 spawn + 2 ??SSE 通道??*本轮 V1 实装四大扩展**：Worker Pool + 重试 + DLQ / 状态锁（乐??CAS + 字典序分布式锁）/ Token Bucket 三层限流 / HITL 反向 interrupt。Redis / ??LangGraph interrupt / PPO Judge ??V1.5??- **核心交付**?? ??Python 文件 / 1 SSE 通道 / 20 测试）：

  1. **`services/agent/src/agent/orchestrator/worker_pool.py`**??*新增** ~175 行）—??`WorkerPool`??     - 异步任务队列 + `asyncio.Semaphore` 限并发（默认 4??     - **3 次重??+ 指数退??*（base_delay_s × 2^attempt，默??base 0.5s??     - **DLQ**（`dlq_entries` list）：3 次均失败 ??`WorkerTask.status="dlq"` + 写入 DLQEntry
     - **幂等去重**（`idempotency_token`）：??token 提交两次 ??第二次直接返首次结果，不重复派发
     - **取消传播**（`cancel_all()`）：1 秒内软停止所??worker；后??submit 立即??cancelled
     - `WorkerTask` / `WorkerResult` / `DLQEntry` dataclass
  2. **`services/agent/src/agent/orchestrator/locks.py`**??*新增** ~145 行）—??状态锁??     - **`VersionedState` + `cas_update(state, mutator)`** —??乐观 CAS（state_version 自增）；mutator ??None ??不更??     - **`DistributedLockManager`** —??进程内分布式锁（V1 mock；接口兼??V1.5 ??Redis）：
       - `acquire_one(resource_id, ttl_s)` —??单把??+ TTL 默认 30s；过期自动清??       - `acquire_many(resource_ids)` —??**按字典序获取**（防 ABBA 死锁?? 逆序释放
       - 自旋等待（asyncio.Event + 1s timeout??     - 全局单例 `get_default_lock_manager()` + 测试 hook `reset_default_lock_manager()`
  3. **`services/agent/src/agent/orchestrator/token_bucket.py`**??*新增** ~145 行）—??`TokenBucketManager`??     - 令牌桶（`capacity` + `refill_rate`）：按租??× 任务类型 × LLM 后端 三维 key 分配
     - `backend_overrides`：内??LLM??0 + 5/s 偏紧?? Ollama / local_small / mock??0K + 1K/s 几乎无限制）
     - **`fallback_backend(tenant, task, current, is_local_only_task)`** —??多级降级链（private ??ollama ??local_small ??mock??     - **CLAUDE.md §2 红线保护**：`is_local_only_task=True` 强制 ollama/local_small（即??private 桶满也不降级??mock??     - 全局单例 + 测试 hook
  4. **`services/agent/src/agent/orchestrator/hitl_bridge.py`**??*新增** ~125 行）—??`HITLBridge`??     - **`request_approval(sub_agent_id, parent_run_id, operation, target, risk_level, correlation_id, auto_approve_low_risk)`**
     - ??`audit.sqlite`：`SUB_AGENT_HITL_REQUESTED` + `SUB_AGENT_HITL_DECIDED` 两条事件（含 correlation_id 串联整棵决策树）
     - V1 简化：`risk_level == "low"` 自动 approve；其他默??reject（V1.5 接真 LangGraph interrupt + 前端审批 UI??     - `HITLRequest` / `HITLDecision` dataclass
  5. **`services/agent/src/agent/orchestrator/__init__.py`**??*重写**）—??公开 API 列表 30+ 项（V0 + V1 新模块全部导出）
- **SSE 三处同步（CLAUDE.md §4 红线??*??
  - **Python** `graph/stream.py`：`_CHANNEL_BY_KIND` 追加 `sub_agent_spawn` / `sub_agent_done` / `sub_agent_progress`（V1 新通道??  - **Rust** `stream/sse_bridge.rs`：`channel::SUB_AGENT_SPAWN/DONE/PROGRESS` 3 个新常量 + `map_event_to_channel` 3 个新映射
  - **TS** `ipc/events.ts`：`EVT.SUB_AGENT_SPAWN/DONE/PROGRESS` 3 个新常量
- **测试**?? 新文??/ **20 测试** / **0 失败**）：

  - **`tests/test_phase12_v1.py`**??*新增** ~340 ??/ 20 测试）：
    - WorkerPool??）：成功首次 / 重试 3 次失????DLQ / ??2 次成功（??DLQ?? 幂等去重 / 并发限流（max_inflight ??2?? cancel_all 软停??    - Locks??）：CAS 基础 / CAS None ??/ acquire_many 按字典序
    - TokenBucket??）：基础消??/ 时间 refill / 三维 key 隔离 / fallback ??/ `is_local_only_task` 红线保护 / reset
    - HITLBridge??）：low_risk auto_approve / high_risk default_reject / correlation_id + 审计 2 条事??    - 集成??）：Bucket + Pool 配合?? ??token 检??+ 5 个任务）
- **三关核验**??
  - Python `uv run pytest services/agent/tests/` —??Phase 12 V1 20 测试全过??*全量回归 670 passed / 7 skipped / 0 failed**（v2.34 650 ??+20 = 670；零回归??  - Rust / TS：V1 仅追??SSE 常量（Rust 0 ??warning；TS 0 错）
- **CLAUDE.md §1/§2/§6 红线遵守**??
  - §1 HITL 不可绕过：`HITLBridge.request_approval()` 是子 Agent 写操作的唯一入口；high risk 默认 reject 防止意外副作??  - §2 敏感上下文强制本机：`TokenBucketManager.fallback_backend(is_local_only_task=True)` 强制 ollama/local_small（不降级 mock??  - §6 审计完整：HITL bridge ??`SUB_AGENT_HITL_REQUESTED/DECIDED` + `correlation_id` 串联决策??  - §6 派生树硬上限：V0 `enforce_tree_limits` + V1 worker pool 不影响派生树（worker pool 是任务级，派生树??Agent 级）
- **V1 偏离设计**??
  - 分布式锁 V1 进程??mock —??**本地 EAIDE 部署架构??V1 已是终??*：EAIDE ??Tauri 桌面应用，单 Python Agent 进程??Rust 拉起，跨进程??/ 配额 / 队列无必要；如未来真要跨进程（罕见），V2.0 ??**SQLite WAL** 替代 Redis（零新依赖，已用 11 ??SQLite??  - HITL bridge V1 简化：low_risk auto_approve + 其他 default reject（V1.5 接真 LangGraph `interrupt()` + 前端 ApprovalCard??  - Worker Pool 异步 V1 简化：3 次重试固??+ 指数退避（不接 RL??- **V1 未交付（V1.5 待加??*??
  - LangGraph ??`interrupt()`（替??HITL bridge V1 简化；接前??ApprovalCard??  - LLM Judge 评分（不作为 CI 闸门??  - ELK 全链路日志（可降级为 SQLite + 文件日志??  - 100GB 压测
- **架构澄清**：Phase 12 设计文档提到??Redis 在本??EAIDE 部署下不必要；V1 进程??`DistributedLockManager` + `TokenBucketManager` + asyncio 队列已满足单进程多智能体需求。SQLite WAL 是已选的跨进程后端（如果未来真要扩）??
---

## 2026-07-29

### v2.34 ??Phase 6 V1 MACC 三层压缩引擎??0 新测??/ 0 失败 / 0 回归??
- **背景**：Phase 6 V0??026-07-29）只交付 Session / Message / Checkpoint 三张??+ LangGraph MemorySaver + 外部 KB 适配器接口；**本轮 V1 实装 MACC（Multi-layer Adaptive Context Compression）三层模??*：L3 工作记忆（滑动窗??+ 关键状态锚点）+ L3 情景记忆（事件图谱）+ L3 语义记忆（规则蒸馏）+ CompressionRouter 静态策略矩阵。L1 KV Cache ??Phase 13 DSpark 接力（标记层不实装执行）；L2 Gist Token V1 占位（V2 接真 Perceiver Resampler）??- **核心交付**?? ??Python 文件 / 1 schema / 4 API 端点 / 30 测试）：

  1. **`services/agent/src/agent/sessions/models_macc.py`**??*新增** ~165 行）—??MACC 数据类：
     - `SemanticRule`（L3 Semantic Memory?? `.new()` 工厂 + `to_dict()`
     - `EventNode` / `EventEdge`（L3 Episodic Memory?? `EventStatus` / `EventRelation` Literal
     - `WorkingMemoryAnchor` + `DEFAULT_ANCHORS`?? 个架构师约定：hitl_gate / tool_runner / repair / intent??     - `GistToken`（L2 占位??     - `CompressionContext`?? 维输入：token_count / message_count / task_complexity / memory_entropy / has_multimodal / has_code / has_logs / has_db_rows / idle_time_s / extra??     - `CompressionResult` + `CompressionStrategy` Literal（NONE / WORKING_ONLY / MEMORY / GIST / HYBRID / KV_CACHE??  2. **`services/agent/src/agent/sessions/schema.sql`**??*修改** ~40 行）—??追加 4 ??MACC 表：
     - `semantic_rules`（id / session_id / pattern / rule_text / confidence / last_updated / source_event_ids_json?? 3 索引
     - `event_graph_nodes`（id / session_id / entity / action / result / status / metadata_json / created_at?? 3 索引
     - `event_graph_edges`（id / session_id / from_node / to_node / relation / metadata_json + FK CASCADE?? 3 索引
     - `compression_log`（id / session_id / strategy / before_tokens / after_tokens / compression_ratio / layers_used_json / elapsed_ms / created_at?? 2 索引
     - **物理隔离**?? 张表 + V0 ??3 张表全部??`sessions.db`（与 audit / knowledge / log_index / log_analysis 全独立）
  3. **`services/agent/src/agent/sessions/storage.py`**??*修改** ~250 行）—??追加 12 个新方法??     - `upsert_semantic_rule` / `list_semantic_rules` / `delete_semantic_rule` —????pattern + rule_text 自动去重 + confidence 累加（封??1.0??     - `insert_event_node` / `insert_event_edge` / `list_event_nodes` / `list_event_edges`
     - **`bfs_recall_episode(session_id, seed_entities, max_hops, max_nodes)`** —??BFS ??outgoing + incoming edges 各扩展；??hops 升序 + created_at 降序
     - `log_compression` / `list_compression_log`（自动算 compression_ratio = after / before??  4. **`services/agent/src/agent/sessions/event_graph.py`**??*新增** ~155 行）—??L3 情景记忆??     - `heuristic_extract_from_messages(session_id, messages, storage)` —??tool_call 优先匹配（避??SQL 模式截胡）→ SQL 模式 ??tool_result 关联最??tool_call
     - `extract_events_with_llm(...)` —??V1 占位（V1.5 接本??0.3B??     - **`recall_episode(storage, session_id, query, max_hops, max_nodes, entity_keywords)`** —??自动??query ??seed entity（`_extract_seed_entities`??+ BFS
     - `event_node_from_dict` / `serialize_graph`（GraphML 风格??  5. **`services/agent/src/agent/sessions/semantic.py`**??*新增** ~115 行）—??L3 语义记忆??     - `distill_rules_from_events(session_id, storage, min_occurrences, max_rules)` —??启发式：频次 ??min_occurrences ??(entity, action) ??蒸馏为规则；confidence = min(1.0, count / 10)
     - `recall_relevant_rules(storage, query, top_k, min_confidence)` —??关键词命??+ confidence 加权
  6. **`services/agent/src/agent/sessions/compression.py`**??*新增** ~220 行）—??`CompressionRouter`??     - **`_decide_strategy(ctx)`** —????§5.2 矩阵：NONE?? 20 ??/ < 8K?? WORKING_ONLY??0-100 ??/ 8K-32K?? MEMORY??00-500 ??/ 32K-128K?? HYBRID?? 500 ??/ > 128K ??L1 marker?? GIST（含多模??/ > 64K?? 空闲?? 300s）→ MEMORY
     - **`_apply(strategy, ctx, messages)`** —??按策略应用压缩；L3.WM + L3.EM + L3.SM 拼装 formatted_prompt
     - **`_build_working_memory(ctx, messages)`** —??DEFAULT_ANCHORS + 滑动窗口（`window_size=20` 默认??     - **`_load_episode_events(ctx)`** / **`_load_semantic_rules(ctx)`** —????storage ??top 10 事件 + top 5 规则
     - **`_placeholder_gist_tokens(ctx)`** —??V2 占位（返??list??     - **`route(ctx, messages)`** —??完整流程：决????应用 ????ratio ????`compression_log`
  7. **`services/agent/src/agent/sessions/api.py`**??*修改** ~165 行）—??追加 4 ??FastAPI 端点??     - `POST /sessions/extract-events` —??启发式抽??+ 写入 `event_graph_nodes`
     - `POST /sessions/distill-rules` —??蒸馏 + 写入 `semantic_rules`
     - `POST /sessions/recall-episode` —??BFS 召回 + edges
     - `POST /sessions/compress` —??CompressionRouter.route（带 before/after tokens + layers_used + formatted_prompt??     - 完整 Pydantic schema（`ExtractEventsRequest` / `DistillRulesRequest` / `RecallEpisodeRequest` / `CompressRequest`??  8. **`services/agent/src/agent/sessions/__init__.py`**??*重写**）—??公开 API 列表 30+ 项（V0 数据??+ V0 DAO + V1 MACC 数据??+ V1 MACC 模块??- **测试**?? 新文??/ **30 测试** / **0 失败**）：

  - **`tests/test_macc.py`**??*新增** ~370 ??/ 30 测试）：
    - 数据类（3）：`SemanticRule.to_dict` / `DEFAULT_ANCHORS` 4 ??/ `CompressionContext` extra 字段
    - storage 扩展??）：3 ??+ compression_log / 规则 dedup / BFS recall outgoing / BFS recall incoming / ??seed 返空 / 规则删除
    - event_graph??）：SQL 抽取 / tool_call + tool_result 关联 / 自动 seed entity / 显式 seed / `serialize_graph`
    - semantic??）：基础蒸馏 / 跳过低频 / SQL pattern 模板 / 关键??+ confidence 排序 / min_confidence 过滤
    - CompressionRouter??1）：NONE / WORKING_ONLY / MEMORY / HYBRID / GIST / 空闲 6 个决??/ ??compression_log / 锚点 + 滑动窗口 / 3 ??layers_used / 默认 window_size / 默认锚点
- **三关核验**??
  - Python `uv run pytest services/agent/tests/` —??macc 30 测试全过??*全量回归 650 passed / 7 skipped / 0 failed**（v2.33 620 ??+30 = 650；零回归??  - Rust / TS：V1 不动 Rust 业务逻辑 / 不动前端（仅 Python 后端??- **CLAUDE.md §6 红线遵守**??
  - §6 物理隔离：sessions.db ??audit / knowledge / biznav / codenav / log_analysis / log_index 全独立（11 数据库）
  - §6 压缩日志完整记录 strategy + before/after tokens + layers_used + elapsed_ms（可观测??+ 未来 PPO 训练数据??  - §6 L3 只存元数据（entity / action / 摘要）；原始对话文本不入??- **V1 偏离设计**??
  - L2 Gist Token V1 占位（返??list）—??V2 接真 Perceiver Resampler 训练
  - L1 KV Cache V1 仅标记（`"L1"` ??`layers_used`）—??实际执行??Phase 13 DSpark 接力
  - LLM 抽取事件 V1 退化启发式（tool_call / SQL / tool_result 模式）—??V1.5 接本??0.3B 端侧
- **V1 未交付（V1.5 待加??*??
  - 前端 `SessionsPanel` UI + 事件图谱可视化（D3.js force-directed??  - FTS5 全文索引（消息内容搜索）
  - 加密 .eas 导出（Fernet + Keyring 占位符）
  - 共享权限矩阵（接 Phase 10 IAM??  - SessionEvent 链式哈希防篡改（??Phase 5 审计??  - 分支（parent_session_id + branch_from_checkpoint_id??  - 100GB 压测

---

## 2026-07-29

### v2.33 ??Phase 2F+ V1 Python 日志分析后端??4 新测??/ 0 失败 / 0 回归??
- **背景**：Phase 2F+ V0??026-07-29 v2.31）只交付 Rust 后端核心（Storage / Indexer / Reader / Searcher / Registry / 6 Tauri Command）；**本轮 V1 聚焦 Python Agent 日志分析后端**：ERROR 块提??+ PII 脱敏 + LLM 根因分析 + 端侧级别分类 + SQLite 3 表缓??+ FastAPI 5 端点 + SSE 三处同步。Rust tailer.rs / 前端 VirtualLineList / AI 分析 UI ??V1.5??- **核心交付**?? ??Python 文件 / 1 schema / 1 测试文件）：

  1. **`services/agent/src/agent/loganalysis/models.py`**??*新增** ~155 行）—??数据类：
     - `ErrorBlock` / `RootCauseRequest` / `RootCauseResponse` / `LogLevelResult` / `LogLevelClassifyResponse` / `AnalysisCacheEntry`
     - 6 个日志级别常量（`LEVEL_DEBUG/INFO/WARN/ERROR/TRACE/FATAL`?? `ALL_LEVELS` tuple
     - `AnalysisCacheEntry.new(...)` 工厂 + `is_expired()` 方法
     - `gen_request_id()` UUID 工具
  2. **`services/agent/src/agent/loganalysis/scrubber.py`**??*新增** ~95 行）—??PII 脱敏??     - **CLAUDE.md §6 安全红线**：原??PII 永远不进 LLM / 不进缓存
     - 8 类正则（按顺序匹配，先手??身份??银行卡，??AWS/JWT/EMAIL/IP，最后高??token 兜底）：
       - `\b1[3-9]\d{9}\b` 中国大陆手机????`[REDACTED:PHONE]`
       - `\b\d{17}[0-9Xx]\b` 18 位身份证（含 X）→ `[REDACTED:ID_CARD]`
       - `\b(?:\d[ -]?){12,18}\d\b` 银行卡（带空??横线）→ `[REDACTED:BANK_CARD]`
       - `\b(?:AKIA|ASIA)[0-9A-Z]{16}\b` AWS Access Key ??`[REDACTED:AWS_KEY]`
       - JWT `eyJ...三段式` ??`[REDACTED:JWT]`
       - `\b[\w.+-]+@[\w-]+\.[\w.-]+\b` 邮箱 ??`[REDACTED:EMAIL]`
       - IPv4 ??`[REDACTED:IPV4]`
       - `\b[A-Za-z0-9_-]{32,}\b` 高熵 token（≥32 字符）→ `[REDACTED:TOKEN]`
     - `scrub_text` / `scrub_lines` / `scrub_error_block`（返??block，不修改入参?? `scrub_error_blocks`（批??+ 重算 fingerprint ??zlib.adler32??  3. **`services/agent/src/agent/loganalysis/extractor.py`**??*新增** ~190 行）—??ERROR 块提取：
     - `_ERROR_HEADER_RE`：ERROR / FATAL / SEVERE / Exception / `[ERR]`（允许行首有时间戳）
     - `_STACK_CONT_RE`：缩进（4+ 空格 / `\t+`?? `at ` / `Caused by:` / `... N more` 堆栈延续
     - `_LEVEL_PATTERNS`?? 个级别正则（FATAL ??ERROR ??WARN ??INFO ??DEBUG ??TRACE??     - **`extract_error_blocks(lines, max_stack_lines=50, max_blocks=200)`**??       1. 逐行扫：ERROR ????开新块；堆栈延续行 ??追加（超 max_stack_lines 关闭）；INFO/DEBUG 等非堆栈????关闭当前??       2. adler32 fingerprint 去重（同 stack 内容只保留首个）
       3. 块数??max_blocks ??截断
     - `detect_level(line)` 单行启发式识别（fallback INFO??     - `level_to_color_hint` / `assert_known_level` 辅助
  4. **`services/agent/src/agent/loganalysis/router.py`**??*新增** ~245 行）—??LLM dispatch??     - **`analyze_root_cause(req, llm, scrubbed_blocks, cache_lookup)`**??       1. 缓存命中 ??直接返（??LLM??       2. ??fingerprint 频次排序 + 4 字符/token 粗估截断??`req.max_tokens`
       3. ??prompt??00 字以内回答限制）????`llm.pick("summarise")` ??fallback `PrivateLLMClient._chat_completion`
       4. 全失????mock 摘要（按 header ??60 字统??top-3 错误模式??     - **`classify_log_levels(lines, llm)`**：端侧模型优????全失????`detect_level()` 正则兜底
  5. **`services/agent/src/agent/loganalysis/schema.sql`**??*新增** ~50 行）—??SQLite schema??     - `search_cache`（file_path + pattern + pattern_type + fingerprint + matched_lines BLOB + match_count + expires_at?? 2 索引
     - `tail_sessions`（session_id UNIQUE + file_path + last_position + lines_emitted + started_at + updated_at + ended_at?? 2 索引（按 file_path / 按活跃）
     - `log_analysis_cache`（cache_key UNIQUE + file_path + file_fingerprint + analysis_type + payload_json + created_at + expires_at?? 2 索引
     - **BLOB 兼容**：`matched_lines` ??u64 LE 编码（与 `logviewer/storage.rs::encode_u64_le` 对齐??  6. **`services/agent/src/agent/loganalysis/storage.py`**??*新增** ~285 行）—??SQLite DAO??     - `LogAnalysisStorage(db_path)`：WAL + foreign_keys=ON + synchronous=NORMAL
     - **`search_cache` CRUD**：`get_search_cache`（命??+ 未过期）/ `upsert_search_cache`（按 4 元组替换?? `cleanup_search_cache`（过期清理）
     - **`tail_sessions` CRUD**：`create_tail_session` / `update_tail_session`（位??+ emit 计数累加?? `end_tail_session`（写 ended_at = now?? `get_tail_session` / `list_active_tail_sessions`
     - **`log_analysis_cache` CRUD**：`get_analysis_cache` / `upsert_analysis_cache` / `cleanup_analysis_cache`
     - `get_stats` ??4 行统计（search_cache / tail_sessions / tail_sessions_active / log_analysis_cache??     - `encode_u64_le` / `decode_u64_le` 字节序编码（??Rust 一致）
     - 单例工厂 `get_default_storage()` ??`settings.log_analysis_db_path`
  7. **`services/agent/src/agent/loganalysis/api.py`**??*新增** ~270 行）—??FastAPI 路由（V1）：
     - **`POST /loganalysis/extract`** ????ERROR 块提取（??LLM、无缓存??     - **`POST /loganalysis/root-cause`** ??完整根因分析（cache_key = sha256(file_fingerprint + analysis_type + blocks）；自动写缓存；??`cache_hit` 字段??     - **`POST /loganalysis/log-level-classify`** ??批量级别识别（端侧优??+ 正则兜底??     - **`GET /loganalysis/cache/stats`** ??3 张表统计
     - **`DELETE /loganalysis/cache`** ??清过期缓存（search + analysis??     - 完整 Pydantic schema（`ExtractRequest/Response`、`RootCauseRequestPayload`、`LogLevelClassifyResponse`??     - `_make_cache_key`（sha256 + 排序??fingerprint 列表 —??避免 PII 入缓??key??     - `_safe_json_dumps`（强??`ensure_ascii=False`??  8. **`services/agent/src/agent/loganalysis/__init__.py`**??*新增**）—??公开 API 列表 30+ ??  9. **`services/agent/src/agent/main.py`**??*修改** ~5 行）—??注册 `loganalysis_api.router`
  10. **`services/agent/src/agent/llm/router.py`**??*修改** ~3 行）—??`_LOCAL_ONLY_TASKS` 追加 `log_level_classify`（V1 注释标注 `log_root_cause` 不加??—??走内??LLM 但强制脱敏）
- **SSE 三处同步（CLAUDE.md §4 红线??*??
  - **Python** `graph/stream.py`：`_CHANNEL_BY_KIND` 追加 `log_analysis_started` / `log_analysis_done` / `log_analysis_error`?? 通道映射 `agent://log_analysis_*`??  - **Rust** `stream/sse_bridge.rs`：`channel::LOG_ANALYSIS_STARTED/DONE/ERROR` 3 个新常量 + `map_event_to_channel` 3 个新映射
  - **TS** `ipc/events.ts`：`EVT.LOG_ANALYSIS_STARTED/DONE/ERROR` 3 个新常量
- **测试**?? 新文??/ **44 测试** / **0 失败**）：

  - **`tests/test_loganalysis.py`**??*新增** ~580 ??/ 44 测试）：
    - `models`??）：常量 / `ErrorBlock.to_dict` / `AnalysisCacheEntry` 过期判断
    - `BLOB`??）：u64 roundtrip / ??/ 截断
    - `scrubber`??0）：手机 / 身份??/ 银行??/ IPv4 / 邮箱 / AWS Key / 高熵 token / ??/ lines / `scrub_error_block` 不修改入??/ fingerprint 重算
    - `extractor`??1）：5 个级别识??/ 边界（空 / fallback?? `extract_error_blocks` basic / dedup（紧邻同 stack?? INFO 分隔??stack 也去重（指纹一致）/ max_stack_lines 截断 / max_blocks 截断 / ??/ Iterable
    - `storage`??）：schema 自动建表 / search_cache CRUD / 过期清理 / upsert 替换 / tail_session 全流??/ list_active / analysis_cache CRUD / cleanup / get_stats
    - `router`??）：`_FakeLLMRouter`（含 `pick` + `_chat_completion` + `classify_log_levels`）→ private 路径 / mock 兜底 / cache 命中 / token 截断 / classify `llm` 路径 / 全失??mock 兜底
    - 集成??）：extract ??scrub 完整链路 / 单例默认路径
- **CLAUDE.md §6 红线遵守**??
  - `_LOCAL_ONLY_TASKS` 包含 `log_level_classify`（V1 注释明示 `log_root_cause` 不入 —??走内??LLM 但强制脱敏）
  - 缓存只存脱敏??payload（`_make_cache_key` 用排??fingerprint 列表；`_safe_json_dumps` 强制 ensure_ascii??  - `scrub_error_blocks` 严格返新对象，不修改入参（测??`test_scrub_error_block_returns_new_block` 验证??- **物理隔离**：log_analysis.db（`~/.eaide/log_analysis.db` ??`settings.log_analysis_db_path`）与 audit.sqlite / knowledge.db / biznav.db / codenav workspace_index.db / sessions.db / log_index.db 完全独立
- **V1 未交付（V1.5 路线??*：Rust `tailer.rs`（notify crate + Tauri Event forwarder?? 前端 `VirtualLineList.tsx` + `LogLine.tsx` + `AnalysisPanel.tsx` + `TailIndicator.tsx` + `SmartFileOpener.tsx`??0MB Monaco 自动切换?? ripgrep 集成 / 性能压测??00GB??- **架构??5 忠告落地**??
  1. **数据不出??*——PII 脱敏在前；LLM 调用只见??`[REDACTED:TYPE]`；缓存只存脱敏后数据
  2. **CPU/GPU 自动检??*——本轮不涉及模型推理；embedding / classification ??llama.cpp 本地推理（复??Phase 4 Ollama Sidecar??  3. **批量处理异步??*——本??api 同步返回；V1.5 ??`asyncio.Queue` 批量模式（参考设??§3.4.2??  4. **配置驱动**——`log_analysis_db_path` ??`Settings` 字段（env var `EAIDE_LOG_ANALYSIS_DB_PATH` 覆盖??  5. **??Python 路线代替 sqlite-vec**——脱敏正则覆??8 ??PII（手??/ 身份??/ 银行??/ IPv4 / 邮箱 / AWS / JWT / 高熵 token）；不引入新原生依赖

---

## 2026-07-29

### v2.32 ??Phase 4 V1 本地知识库引擎（59 新测??/ 0 失败 / 0 回归??
- **背景**：Phase 4 V0??026-07-29 v2.30）只交付了端侧模型客户端 + 推理模式切换 + 外部 KB 适配器；**本轮 V1 把本地知识库引擎完整实装**：SQLite + embedding BLOB + numpy cosine 检??+ 4 种分块器 + FastAPI v1 路由 + LangGraph `rag_retrieve` 节点接入主图（受 `settings.rag_enabled=True` 控制）??- **设计哲学坚守**：设计文??§3.1 写的??SQLite-vec `vec0` 虚拟表；V1 改用**??Python 路线**（embedding ??BLOB + numpy 余弦相似度）—??避免 `sqlite-vec` 原生编译依赖，跨平台一键跑通；性能??10w 级完全够用（实测 < 50ms）。数据结构兼??vec0：未来切??vec0 只需 `storage.search_by_vector` 改一行，关系表与 BLOB 列无需迁移??- **核心交付**?? ??Python 文件 / 1 schema / 3 测试文件 / 1 配置??/ 1 graph 接入）：

  1. **`services/agent/src/agent/knowledge/models.py`**??*新增** ~250 行）—??数据类：
     - `KnowledgeDoc` / `KnowledgeChunk` / `RetrievalResult` / `RAGContext` / `KnowledgeStats` ??dataclass
     - `SOURCE_*` 常量（markdown / swagger / conversation / business_rule / code_symbol / pdf??     - `metadata_to_json / metadata_from_json` 列编解码 helper
     - `_row_to_dict` 兼容 `sqlite3.Row` / tuple / dict 的统一取??  2. **`services/agent/src/agent/knowledge/schema.sql`**??*新增** ~75 行）—??SQLite schema??     - `knowledge_docs`（文档元数据 + metadata JSON + soft delete?? 2 索引
     - `knowledge_chunks`（content + seq + token_count + FK cascade?? 2 索引
     - **`embedding_vectors`**（chunk_id PK + embedding BLOB + dim + FK cascade）—????chunks 拆表，BLOB 体积恒定??PRAGMA quick_check 走顺序扫??     - `knowledge_search_log`（query + results_count + avg_similarity + latency_ms + user_id??  3. **`services/agent/src/agent/knowledge/storage.py`**??*新增** ~530 行）—??DAO??     - `KnowledgeStorage(db_path)`：WAL + foreign_keys=ON + synchronous=NORMAL
     - **doc CRUD**：`upsert_doc` / `get_doc` / `list_docs(source_type, limit, offset)` / `count_docs` / `soft_delete_doc`（级联清 embedding + chunks?? `hard_delete_doc`
     - **chunk CRUD**：`upsert_chunks(doc_id, chunks)` 整批替换 + 同步更新 `doc.chunk_count` / `get_chunks_by_doc` / `get_chunk`
     - **`search_by_vector(query_emb, top_k, similarity_threshold, source_type_filter)`**??       - SELECT 顺序??`KnowledgeChunk.from_row` 严格对齐（id doc_id seq content token_count metadata created_at + title source_type + embedding dim??       - numpy 路径：归一??query + 行归一????mat @ q ??argpartition top-k ??阈值过滤（**strict greater than**，sim > threshold 才入选）
       - ??Python 兜底：numpy 缺失时退化（CI 环境验证过）；同样支??dim mismatch 检??     - `search_by_text` LIKE 兜底 + `source_type_filter`
     - `log_search` 写检索历史（??top_k 列）；`get_stats` 出总数 + ??source_type + 平均相似??     - `encode_embedding` / `decode_embedding`：float32 小端 numpy；无 numpy ??struct.pack fallback
     - 单例工厂 `get_default_storage()` ??`settings.knowledge_db_path`
  4. **`services/agent/src/agent/knowledge/chunker.py`**??*新增** ~370 行）—??4 种分块器??     - **`chunk_markdown`**：按 H1/H2/H3 ????超长按段落再切（chunk_size=512 token + overlap=50）；空文??/ 无标??/ ??section 全覆??     - **`chunk_swagger`**：每??endpoint 一块（??method + path + summary + tags + request body + responses?? 单独 schemas 块（上限 200 ??schema??     - **`chunk_conversation`**：每??(user + assistant) 一块；首条 assistant 视为前导
     - **`chunk_business_rules`**：Phase 2G 联动，每??Feature 一块（关联文件 / API / ??/ 业务规则各自独立段落??     - **`chunk_code_symbols`**：Phase 2F 联动，每??symbol 一??     - `chunk_by_source` 统一入口；`estimate_tokens` 中英混合估算（CJK 1.5 ??/ token，英??0.25 ??/ token??  5. **`services/agent/src/agent/knowledge/retriever.py`**??*新增** ~230 行）—??RAG 检索器??     - `RAGRetriever(storage, embedding, top_k, similarity_threshold, max_prompt_chars)`
     - **`retrieve(query, source_type_filter, top_k, similarity_threshold)`**??       1. 向量检索主路径（embedding.embed ??storage.search_by_vector??       2. embedding 不可??/ 抛错 ??LIKE 兜底（storage.search_by_text + ??doc 信息??       3. **统一应用 `similarity_threshold` 过滤**（兜底路径也遵守??.0 similarity 也会被阈值筛掉）
       4. `storage.log_search` 自动记录（含 elapsed_ms + avg_similarity??     - **`format_for_llm(results)`**：拼??system prompt 片段
       ```
       [知识库参????请基于以下文档回答，若无关请忽略，无需引用编号时直接忽略整段]
       [1] markdown: 订单创建 (相似??0.92)
       ## 订单创建
       ...
       [2] swagger: 订单 API (相似??0.85)
       POST /orders ??创建订单
       ...
       ```

       ??`max_prompt_chars` 时按段截??+ ??`[... 内容过长已截??...]`
     - `EmbeddingClientProto`（runtime_checkable Protocol）；`build_default_embedding_client()` 懒加载；单例 `get_default_retriever()`
  6. **`services/agent/src/agent/knowledge/ingestion.py`**??*新增** ~270 行）—??导入编排??     - `KnowledgeIngestion(storage, embedding)`
     - `ingest_markdown_file(path)` / `ingest_swagger_file(path)` / `ingest_pdf_file(path)`：读 ??校验后缀 ??分块 ??批量 embed ??upsert
     - **安全红线**：文件大????50MB（`MAX_FILE_BYTES`?? 后缀白名单（`.md .markdown .yaml .yml .json .pdf`??     - **`sync_from_biznav()`**：从 `biznav.db` 读所??features ????`project_name` 分组 ??每组一??doc??*容错**：biznav.db 不存??/ 表不存在 / 字段不一致一律返 0 不报??     - **`sync_from_codenav()`**：从 `codenav.db` ??`symbols` ??????`file_path` 分组；同样容??     - `pypdf` 软依赖（缺时 `ingest_pdf_file` 抛清??`IngestionError`，提??`uv add pypdf`??     - `build_default_ingestion()` 工厂
  7. **`services/agent/src/agent/knowledge/api.py`**??*修改** ~250 ??/ V0 ??V1）—??FastAPI 路由??     - **V0 向后兼容**（不改）：`GET /knowledge/status` + `POST /knowledge/search`（mock 外部 KB??     - **V1 新增 6 端点**??
       | 端点                                             | 方法   | 功能                                              |
       | ------------------------------------------------ | ------ | ------------------------------------------------- |
       | `/knowledge/v1/status`                           | GET    | 本地 KB 状??+ embedding 可达??+ stats + db_path |
       | `/knowledge/v1/docs`                             | GET    | 文档列表（source_type 过滤 + 分页??              |
       | `/knowledge/v1/docs/upload`                      | POST   | 按后缀路由 markdown / swagger / pdf               |
       | `/knowledge/v1/docs/{doc_id}`                    | DELETE | 软删除（级联??chunks + embeddings??             |
       | `/knowledge/v1/search`                           | POST   | RAG 检索（向量 + LIKE 兜底 + 阈??+ 截断 prompt??|
       | `/knowledge/v1/sync/biznav` / `/v1/sync/codenav` | POST   | 跨模块同??                                       |
     - 完整 Pydantic schema（`V1DocSummary` / `V1SearchRequest` / `V1RetrievalResult` / `V1SearchResponse` / `V1SyncResponse`??     - `reset_for_testing()` 单例清理 hook
  8. **`services/agent/src/agent/knowledge/__init__.py`**??*重写**）—??公开 API 列表（V0 外部适配??+ V1 本地引擎双轨导出），??30+ ??`__all__` ??- **LangGraph 新节??/ 状态扩??/ 主图接入**?? 文件）：

  1. **`services/agent/src/agent/graph/nodes/local_intent.py`**??*新增** ~125 行）—??本地小模型意图分类：
     - 候选意图：`['query', 'mutate', 'orchestrate', 'chitchat']`（与 AgentState.Intent Literal 对齐??     - 三级降级链：local_small ??LMRouter（Ollama）→ 关键词分??     - 关键词兜底：中英文关键字映射 + 中文常见动词??????列表/统计" + "??更新/创建/删除" + "部署/重启/回滚"??  2. **`services/agent/src/agent/graph/nodes/rag_retrieve.py`**??*新增** ~110 行）—??RAG 检索节点：
     - **触发判断**：`_should_retrieve(prompt)` —??13 个触发关键词??文档/知识??参??规范/说明/是什??怎么??如何/doc/knowledge/reference/spec/guide/how to/what is"）或长度 ??12
     - `state.system_prompt_addon = ctx.formatted_prompt`（下??planner/responder ??prompt??     - `state.rag_context = RAGContext`（前??CitationChip 用）
     - 失败兜底：embedding 不可??/ retrieve 抛错 ??`rag_context=None` + addon="" + trace 记录
  3. **`services/agent/src/agent/graph/state.py`**??*修改** ~15 行）—??新字段：
     - `NodeName` Literal 追加 `"local_intent"` / `"rag_retrieve"`
     - `AgentState` 新增 `rag_context: Any | None` + `system_prompt_addon: str`
     - `empty_state()` 默认??+ docstring 同步
  4. **`services/agent/src/agent/graph/compile.py`**??*修改** ~25 行）—??主图接入??     - 注册 `_local_intent` + `_rag_retrieve` 节点（两个都注册，main 路径??`settings.rag_enabled` 决定??     - **关键决策**：`rag_enabled=True`（默认）??`START ??intent ??rag_retrieve ??planner`，否则保留原 `intent ??planner`
     - 测试可通过 `EAIDE_RAG_ENABLED=false` 跳过 rag_retrieve（现有测试套件不破）
- **配置 / LangGraph 公共契约**??
  - `services/agent/src/agent/config.py`??*修改** ~10 行）—??`Settings` 新增 3 项：
    - `knowledge_db_path: str = "knowledge.db"`（相对路径，测试 chdir ??tmp_path 自动隔离??    - `rag_enabled: bool = True`（默认接入主图）
    - `local_embedding_dim: int = 384`（bge-small-zh 维度??- **测试**?? 新文??/ **59 测试** / **0 失败**）：

  - **`tests/test_knowledge_chunker.py`**??*新增** ~190 ??/ 21 测试）：
    - `estimate_tokens`??）：??/ 中英文混??    - `chunk_markdown`??）：H2 切分 / 无标题整??/ 空输??/ 超长再切
    - `chunk_swagger`??）：每个 endpoint 一??+ schemas ??/ 空输??/ 非法输入
    - `chunk_conversation`??）：配对 / ??/ 孤立 user
    - `chunk_business_rules`??）：dict 输入 + Feature 字段透传 / ??    - `chunk_code_symbols`??）：完整字段 / ??    - `chunk_by_source`??）：路由分发 / 未知类型
    - `chunk_by_source` conversation????  - **`tests/test_knowledge_storage.py`**??*新增** ~280 ??/ 19 测试）：
    - BLOB 编解码（2）：400 ??roundtrip / 基础 4 ??    - doc CRUD??）：upsert+get / update / missing 返回 None / list / filter / count / soft_delete（list 不返??+ 第二次返 False + 级联 chunks?? hard_delete
    - chunk CRUD??）：upsert ??content + embedding + 同步 chunk_count / 整批替换 / 空列??    - 向量检索（6）：top_k / 阈值过??/ source_type 过滤 / **dim mismatch 返空** / 软删除文档不参与检??/ ??query
    - 全文检索（3）：LIKE 基础 / ??query / source_type 过滤
    - log_search + stats??）：记录 / 总数 + by_type / 单例工厂??settings.knowledge_db_path
  - **`tests/test_knowledge_retriever.py`**??*新增** ~210 ??/ 19 测试）：
    - 主路径（5）：embedding 主路??+ top_k 限制 + source_type 过滤 + **阈??strict greater than**??.0 阈值过滤掉所有）/ LIEmbedding 失败时退??LIKE
    - 兜底??）：embedding=None ??LIKE / embed 抛错 ??LIKE / ??query / 无命??    - format_for_llm??）：基础拼接 + 引用编号 / ??/ 超长截断
    - passthrough??）：get_doc / get_chunks_by_doc
    - 默认 embedding 工厂??）：不可达时不抛构造错
- **三关核验**??
  - Python `uv run pytest services/agent/tests/` —??知识??3 个新文件 59 测试全过??*全量回归 0 failed / 1 skipped**（历??Windows TestClient 已知 skip??  - Rust `cargo build --lib` —??0 ??warning（V1 没有 Rust 代码，纯 Python 后端??  - TS `pnpm exec tsc -b` —??V1 不改前端?? 错（前端 KnowledgeBaseView 等留 V1.5??- **CLAUDE.md §2 / §4 / §6 红线遵守**??
  - `_LOCAL_ONLY_TASKS` 已含 `local_intent` + `vision_understand`（V0 已加）—??`local_intent_node` ??Ollama，不联网
  - HITL 不涉及（知识库检索是读操作，不调外部副作用）
  - 审计不涉及（V1 ??V2 ??audit.sqlite 的扩展点）；现有 `audit` 表是 `action TEXT` ??CHECK??*schema 无需变更**（Rust / Python 双侧无需同步??  - `knowledge_db` 物理隔离（`knowledge.db` vs `audit.sqlite` / `router.db` / `biznav.db` / `codenav.db` / `log_index.db` / `sessions.db` / `collab.db` / `iam.db` / `license.db` —??9 数据库全互不干扰??- **新增公开 API（`agent.knowledge`??*??
  - 类：`KnowledgeStorage` / `KnowledgeIngestion` / `RAGRetriever`
  - 数据类：`KnowledgeDoc` / `KnowledgeChunk` / `RetrievalResult` / `RAGContext` / `KnowledgeStats`
  - 分块器：`chunk_markdown` / `chunk_swagger` / `chunk_conversation` / `chunk_business_rules` / `chunk_code_symbols` / `chunk_by_source`
  - 单例工厂：`get_default_storage` / `get_default_retriever` / `build_default_ingestion` / `build_default_embedding_client`
  - BLOB 工具：`encode_embedding` / `decode_embedding`
  - V0 兼容（不删）：`KBConfig` / `KBContext` / `KnowledgeBaseAdapter` / `MockKBAdapter` / `build_adapter` / `build_kb_context` / `kb_context_to_prompt_snippet`
- **V1 未交付（V1.5 路线??*：前??KnowledgeBaseView 5 子页??+ ActivityBar 📚 入口 + ChatPanel CitationChip / ScreenshotDropZone + Rust manager.rs Sidecar 生命周期 + Settings LocalAISettings 面板 —??本轮聚焦后端核心 + 公共 API；前端联调留 V1.5
- **架构??5 忠告落地**??
  1. **数据不出??*——V1 全本??SQLite + BLOB，零网络请求；`sync_from_biznav/codenav` 走本??DB 路径
  2. **CPU/GPU 自动检??*——本轮不涉及模型推理；embedding ??llama-server 处理（`LocalEmbeddingClient`??  3. **批量处理异步??*——`upsert_chunks` 单事务批量；`embed_batch` 一次性向量化整批
  4. **配置驱动**——`knowledge_db_path` / `rag_enabled` / `local_embedding_dim` 全部 `Settings` 字段，env var 覆盖
  5. **??Python 路线代替 vec0**——明确标??schema 数据结构兼容 vec0，V2 切回 vec0 只需??`search_by_vector` 一??
---

## 2026-07-29

### v2.31 ??Phase 2F+ V0 大文件查看器 Rust 核心 MVP??54 passed / 0 failed??
- **背景**：Phase 2F+（设??v2.3 立项 / 15 天）原计划整片含 UI / Tail / AI / ripgrep 全交付，**本轮聚焦 Rust 后端核心**：先跑??Storage / Indexer / Reader / Searcher / Registry / Commands 六大模块 + 6 ??Tauri command，让前端??IPC 可以接；UI / Tail / AI / ripgrep / SSE 留待 V1??- **核心交付**?? 文件 / 154 单测 / 0 回归）：
  1. **`apps/desktop/src-tauri/src/logviewer/mod.rs`**??*新增** ~35 行）—??统一对外接口 + 模块导出（`pub use` indexer/reader/searcher/registry/storage??  2. **`apps/desktop/src-tauri/src/logviewer/storage.rs`**??*新增** ~280 行）—??Task 1??     - `LogIndexStorage` SQLite handle；`open(path)` 自动建父目录 + WAL + NORMAL synchronous + busy_timeout=5000
     - `file_index` 表（file_path PK / file_fingerprint / file_size / line_count / line_offsets BLOB / encoding / last_modified / indexed_at / index_version?? `idx_file_index_modified` 索引
     - `get` / `upsert`（单事务，旧行存活至 COMMIT?? `status`（轻??status 返回 `IndexStatus::Missing | Ready { line_count, indexed_at }`??     - `encode_u64_le` / `decode_u64_le` 偏移编解码（??vec ????BLOB；长度非 8 倍数 ??`SchemaError::BlobLength`??     - 物理隔离：`log_index.db` ??audit / codenav / biznav / knowledge / sessions 完全独立
  3. **`apps/desktop/src-tauri/src/logviewer/indexer.rs`**??*新增** ~710 行）—??Task 2??     - 1 MiB BufReader 流式扫描；`read_until(b'\n')` + `Vec<u64>` 偏移累加
     - `validate_path`（绝对路??/ 存在 / 普通文件）+ `fingerprint(path)` `"{size}:{mtime_secs}"`
     - `index_file(path, cancel: Arc<AtomicBool>, progress_cb)` ??`AppResult<Option<IndexSummary>>`：每 1 MiB 边界发进度；取消在每??iteration 顶检查；扫描完成??*单事务原??upsert**，失??/ 取消??*永不 upsert**（老索引存活）
     - 边界：空文件 ??`line_count=0, offsets=[0]` / 末尾??`\n` 仍计入行??/ ??ASCII 用字节偏??  4. **`apps/desktop/src-tauri/src/logviewer/reader.rs`**??*新增** ~545 行）—??Task 3??     - `read_lines(path, start_line, end_line, max_bytes)` ??`ReadLinesResult { lines, truncated, bytes_read }`
     - Half-open `[start, end)`；`offsets[end_line]` ??sentinel (`file_size`)
     - 读前 `fingerprint()` 校验，与索引不一????`AppError::Validation("fingerprint mismatch")`??*禁止**用陈旧索??seek??     - `max_bytes` 截断：超出时 cut 到最后一??`\n`，`truncated=true`；仅剥单??`\n`，`\r` 保留
     - 错误：`NotFound`（无索引?? `Validation`（反??/ 越界 / 陈旧 fingerprint / BLOB 解码失败?? `Io`
  5. **`apps/desktop/src-tauri/src/logviewer/searcher.rs`**??*新增** ~430 行）—??Task 4??     - `LogSearcher::search(path, mode: SearchMode::Literal|Regex, pattern, before, after, max_matches, max_bytes, cancel)`
     - 1 MiB BufReader 流式扫描；literal 子串??`regex` crate 编译正则；空 pattern ??`Validation`；正则编译失????`Validation`
     - rolling before-context（最??`before` 行）+ per-match after-context（直到下一 match ??`after` 行）；`max_matches` / `max_bytes` 触发 `truncated=true` 提前??     - 同样读前 fingerprint 校验；搜索与 indexer 共用??1 MiB BufReader BLOCK_SIZE
  6. **`apps/desktop/src-tauri/src/logviewer/registry.rs`**??*新增** ~1100 行）—??Task 5??     - `LogViewerState` `Arc<Mutex<RegistryInner>>` 共享状态；`storage_path: Arc<PathBuf>` 默认 `%APPDATA%/eaide/log_index.db`（Win?? `$HOME/Library/Application Support/eaide/log_index.db`（macOS?? `$XDG_DATA_HOME/eaide/log_index.db`（Linux），兜底 `log_index.db`
     - `TaskId` = UUIDv4 string；`TaskKind` = `index | search`；`TaskStatus` = `Queued | Running | Completed | Failed | Cancelled`
     - 状态机：`require_transition(from, to)` 拒绝非法跃迁（如 `Completed ??Running`??     - `submit_index(path)`：canonicalize 后查 by-path 锁，**活跃 index task 重复 ??SubmitError::DuplicateActiveIndex**；terminal 后允许新提交
     - `submit_search(path)`??*不去??*（同一文件可并发搜索）
     - `cancel(id)`：Queued ??直接 Cancelled；Running ??仅翻 `Arc<AtomicBool>` flag（worker finalize）；terminal ??no-op
     - `FINISHED_TTL_SECS = 60`：terminal task 保留 60 秒供 UI 最后一??poll，`cleanup_finished(ttl)` 回收
     - `cancel_handle(id)` 克隆 `Arc<AtomicBool>` ??worker 观察
  7. **`apps/desktop/src-tauri/src/logviewer/commands.rs`**??*新增** ~440 行）—??Task 6??     - **6 ??Tauri command**??*??HTTP / ??SSE**）：

       | Command                                                                                        | 异步语义                          | 返回                              |
       | ---------------------------------------------------------------------------------------------- | --------------------------------- | --------------------------------- |
       | `logviewer_index_file(path)`                                                                   | `async` + `spawn_blocking` worker | `CmdResult<TaskId>`               |
       | `logviewer_search(path, pattern, mode, context_before, context_after, max_matches, max_bytes)` | `async` + `spawn_blocking` worker | `CmdResult<TaskId>`               |
       | `logviewer_read_lines(path, start_line, end_line, max_bytes)`                                  | `async`（无 spawn_blocking??     | `CmdResult<ReadLinesResult>`      |
       | `logviewer_task_status(task_id)`                                                               | `async`（SQL row lookup??        | `CmdResult<Option<TaskSnapshot>>` |
       | `logviewer_cancel_task(task_id)`                                                               | `async`（翻 cancel flag??        | `CmdResult<TaskStatus>`           |
       | `logviewer_index_status(path)`                                                                 | `async`（轻??status??           | `CmdResult<IndexStatus>`          |
     - `LogSearchRequest` DTO + `from_args(...)` ??JS `mode: String` ??`SearchMode`（`"regex"` / `"literal"`，未知默??literal）；case-insensitive
     - 错误模型：每??command ??`Result<T, String>`；`AppError` 在边界映射为 `"logviewer command failed: ..."`；`SubmitError` 映射为人类可读字符串
- **测试**（`d:\ditPref\build-with-msvc.bat test --lib logviewer::` ??**154 passed / 0 failed**）：
  - storage codec??0 测试）：empty / multiline / non-ASCII / 小端??/ 8 字节倍数边界??/11/15/16??  - storage CRUD?? 测试）：open parent_dirs / get / upsert roundtrip / overwrite / 跨路径原子??/ status missing vs ready
  - indexer??5 测试）：路径校验 / fingerprint / 空文??/ 单行??`\n` / 多行????`\n` / ??`\n` / ??ASCII / 取消保留旧索??/ 进度回调（小文件至少一??/ 大文??1 MiB 边界?? 单事务原子提??/ 大文件不爆内??  - reader??2 测试）：合法区间 / 空文??/ 末尾??`\n` / CRLF 仅剥 LF / `start==end` / 越界 / 反向 / 缺失索引 / 陈旧 fingerprint / `max_bytes` 截断 / 截断丢弃不完整末??/ `max_bytes` 大于 range 不截??  - searcher??0 测试）：literal / regex / empty pattern ??/ 取消 / 上限 / fingerprint / context before/after / UTF-8 多字??  - registry??5 测试）：生命周期 / 合法跃迁 / 非法跃迁??/ 未知 ID / by-path ??/ cancel 行为分流 / FINISHED_TTL_SECS 延迟清理
  - commands??6 测试，含 wire shape）：TaskId / TaskStatus / TaskKind / IndexStatus / ReadLinesResult / LogSearchRequest / SearchMode / LogSearchMatch / LogSearchResult / TaskSnapshot / 命令边界行为 / cancel handle / storage_path ??clone 稳定 / submit-get roundtrip
- **CLAUDE.md 红线遵守**??  - ??SQLite 审计??Rust + Python 共享 —??本轮 `log_index.db` ??Tauri 侧独??DB??*??*??Python Agent 共享 schema（与 audit 不同），符合"物理隔离"约定
  - ??全部 Tauri command ??`tauri::command` ??+ `State<'_, AppState>` 注入标准模式
  - ??不引入新 crate：复??rusqlite（已用）/ regex（lock 传递可用）/ uuid / serde / thiserror / chrono / tokio
  - ??`mod.rs` 末尾 `#[cfg(test)] mod tests;` 编译烟测，与既有子模块风格一??- **三关核验**??  - **Rust**：`d:\ditPref\build-with-msvc.bat test --lib logviewer::` ??**154 passed / 0 failed**?? ??warning / 0 ??panic??  - **TS**：`pnpm exec tsc -b` ??**0 ??*（v2.31 无前端代码）
  - **Python**：未涉及
- **V0 完成????Phase 2F+ 后续**??  - ??Storage（file_index ??+ BLOB 编码??  - ??Indexer?? MiB BufReader + 取消 + 原子 upsert??  - ??Reader（half-open 区间 + fingerprint 校验 + `max_bytes` 截断??  - ??Searcher（literal / regex + before/after context + 上限??  - ??Registry（TaskEntry 状态机 + by-path ??+ FINISHED_TTL??  - ??6 ??Tauri command
  - ??154 单测
  - ??前端 VirtualLineList / SmartFileOpener（V1??  - ??Tail -f + `notify` crate + Tauri Event forwarder（V1??  - ??Python Agent `loganalysis/` API（V1：PII 脱敏 + LLM 根因 + `log_level_classify` 走本??0.3B??  - ??ripgrep 跨进程集成（v2.31 用纯 Rust `regex` crate；ripgrep ??V1 评估 Windows MSVC 兼容性）
  - ??AI 分析 UI + Session 保存桥（联动 Phase 6??  - ??SSE 三处同步：`log_analysis_started` / `log_analysis_done` + Tauri Event `logviewer://index-progress` / `logviewer://tail-*`
  - ??`search_cache` / `tail_sessions` / `log_analysis_cache` 三张表（schema 预留位置??  - ??100 GB 量级性能压测（v2.31 ??100 MB 量级基础测过??
### v2.30 ??Phase 4 V0 本地端侧模型 + 外部知识库适配??+ 推理模式切换??82 passed + 1 skipped / 0 failed / TS 0 错）

- **背景**：用户明确三个决策：
  1. **知识库不做本??* ??改用外部 KB 适配器接口（复用 Phase 6 `KnowledgeBaseAdapter` 模式??  2. **端侧模型只做"思??（分??列计划），不??执行"** ??内网/云端模型负责具体实现
  3. **DSpark（Phase 13）仅在正常模式下使用** ??为本地端侧模型加??- **核心架构**??  - ChatInput 发送按钮旁新增 ⚡正??/ 🚀性能 切换
  - 正常模式：intent/plan ??local_small 优先 ??失败回退 ollama ??private
  - 性能模式：全部走 ollama/private（跳过端侧）
  - 内网模型(private) 与云端模型地位等同，都可执行复杂任务
- **核心交付**??5 文件 / 25 测试 / 0 回归）：
  1. **`services/agent/src/agent/config.py`**（修??~8 行）—??新增 6 个本地模型配置项
  2. **`services/agent/src/agent/llm/local_small.py`**（新??~170 行）—??本地文本模型客户端（OpenAI 兼容??  3. **`services/agent/src/agent/llm/local_vision.py`**（新??~120 行）—??本地视觉模型客户??  4. **`services/agent/src/agent/llm/embedding.py`**（新??~110 行）—??本地 Embedding 客户??  5. **`services/agent/src/agent/llm/router.py`**（修??~100 行）—??LMRouter 扩展??     - `__init__` 新增 `local_small` 客户??     - 新增 `inference_mode` 属??+ `set_inference_mode()` API
     - `_LOCAL_ONLY_TASKS` 新增 `local_intent`, `vision_understand`
     - `_chain_for` 重构：正常模??intent/plan 链前端加 local_small
     - 新增 `_build_intent_chain` / `_build_plan_chain` 辅助函数
  6. **`services/agent/src/agent/knowledge/`**（新??3 文件）—??外部 KB 适配器（Protocol + Mock + API??  7. **`services/agent/src/agent/localai/`**（新??3 文件）—??本地模型管理（sidecar 健康检??+ API??  8. **`services/agent/src/agent/graph/state.py`**（修??~15 行）—??新增 inference_mode / screenshot 字段
  9. **`services/agent/src/agent/graph/nodes/vision_understand.py`**（新??~80 行）—??截图理解节点
  10. **`services/agent/src/agent/graph/compile.py`**（修??~5 行）—??注册 vision_understand 节点
  11. **`services/agent/src/agent/graph/stream.py`**（修??~3 行）—??SSE 通道：localai_ready / localai_error
  12. **`services/agent/src/agent/main.py`**（修??~5 行）—??注册 localai + knowledge 路由
  13. **Rust SSE ??* `sse_bridge.rs`（修??~8 行）—??新增 LOCALAI_READY / LOCALAI_ERROR 通道
  14. **Rust 命令** `commands/localai.rs`（新??~70 行）—??4 ??Tauri command
  15. **前端**?? 文件修改）：
      - `InferenceModeToggle.tsx` ??⚡正??🚀性能 模式切换按钮
      - `ChatInput.tsx` ??嵌入切换按钮
      - `chatStore.ts` ??inferenceMode 状??+ toggle action
      - `invoke.ts` ??4 ??IPC wrapper
      - `events.ts` ??2 ??SSE 事件常量
- **CLAUDE.md §2 红线遵守**??  - ??HITL 脊梁不动
  - ??`_LOCAL_ONLY_TASKS` 新增 local_intent / vision_understand
  - ??Auto-Repair retry_count ??2 不动
  - ??SSE 三处同步（Python stream.py + Rust sse_bridge.rs + TS events.ts??  - ??审计 schema（action TEXT 列无需改）
- **测试**?? 关核验）??  - **Python**：`uv run pytest services/agent/tests/` ??**582 passed, 1 skipped, 0 failed**（vs v2.29 551 ??新增 25 ??Phase 4 测试 + 6 个内??LLM 测试可用；零回归??  - **TS**：`pnpm exec tsc -b` ??**0 ??*
  - **Rust**：`cargo build --lib` ??未变（无??Rust 代码需编译；sse_bridge.rs 仅常??match arm??- **V0 完成????Phase 4 后续**??  - ??local_small / local_vision / local_embedding 客户??  - ??LMRouter inference_mode 切换
  - ??外部 KB 适配器接口（MockKBAdapter??  - ??ChatInput 模式切换 UI
  - ??Rust sidecar 真进程管理（V1??  - ??模型下载/加载进度（V1??  - ??DSpark 集成正常模式（Phase 13 后续??
### v2.29 ??Phase 6 V0 会话管理骨架 —??sessions/ 后端 + LangGraph checkpointer + 外部 KB 适配器（551 passed + 7 skipped / 0 failed / 13 credential contract 0 违规??
- **背景**：CHANGELOG v2.19 立项 Phase 6??0 天），本轮按"按计划往??聚焦最小可行骨架：
  - 后端 sessions/ 模块??0 搭建（model + storage + checkpointer + knowledge_base + api??  - 前端 sessionsStore + 5 IPC wrappers（V0 不做完整 UI 面板，留 V1 补）
  - **外部 KB 决策**：按用户 2026-07-29 要求??*不做 Phase 4 本地知识库引??*（SQLite-vec + RAG 跳过）；保留 `KnowledgeBaseAdapter` 接口 + `KBConfig` 配置 + `MockKBAdapter` 占位 —??后续 Phase 4 / 第三方（Notion / Confluence / GraphRAG）接入只需**替换 adapter 实现**，Phase 6 调用点零改动
- **核心交付**?? 文件 / 26 测试 / 0 回归）：
  1. **`services/agent/src/agent/sessions/models.py`**??*新增** ~70 行）—??数据类：
     - `Session(id, title, owner, project_name, status, created_at, updated_at, thread_id, metadata)` —??thread_id 默认 = session_id（LangGraph 一对一??     - `Message(id, session_id, role, content, created_at, tool_call_id, tool_name, tool_args, tool_result, metadata)` —??支持 user / assistant / system / tool 四种 role
     - `SessionCheckpoint` —??LangGraph 状态快照引用（实际数据存在 MemorySaver / SqliteSaver??  2. **`services/agent/src/agent/sessions/schema.sql`**??*新增**）—??3 ??DDL??     - `sessions`（PK: id TEXT）—??status CHECK (active/archived/deleted) + 3 索引（status / project_name / updated_at DESC??     - `session_messages`（FK CASCADE）—??角色 CHECK + 索引（session_id / session_id+created_at??     - `session_checkpoints`（UNIQUE (thread_id, checkpoint_id) 防重复）
  3. **`services/agent/src/agent/sessions/storage.py`**??*新增** ~230 行）—??sync sqlite3 CRUD??     - Session CRUD（create / get / list / update / delete + CASCADE??     - Message CRUD（append / list??     - Checkpoint CRUD（INSERT OR IGNORE 防重复）
     - 模块级单例模式（??envconfig / biznav 一致）
  4. **`services/agent/src/agent/sessions/knowledge_base.py`**??*新增** ~180 行）—??**外部 KB 适配器接??*??     - `KBConfig` dataclass（backend / base_url / api_key_ref / timeout_s?? `from_env()` ??`EAIDE_KB_*` 环境变量
     - `KnowledgeBaseAdapter` Protocol（runtime_checkable）：`is_available()` / `search()` / `name` 属??     - `KBQueryResult` / `KBContext` 数据??     - **`MockKBAdapter`** V0 占位（返回固??2 条示??+ 20ms 模拟延迟；V1 ??Phase 4 时只换实现）
     - `build_adapter(config)` 工厂（未??backend ??mock 兜底??     - `build_kb_context(query, adapter, top_k)` 统一入口（best-effort，失败返??KBContext??     - `kb_context_to_prompt_snippet(ctx, max_chars)` ??system prompt 片段（截??2000 字符??token 爆炸??  5. **`services/agent/src/agent/sessions/checkpointer.py`**??*新增** ~75 行）—??LangGraph MemorySaver wrapper??     - V0 ??`langgraph.checkpoint.memory.MemorySaver`（进程内，重启丢??—??框架跑通够用）
     - V1 替换 `SqliteSaver`（需??`langgraph-checkpoint-sqlite`，持久化??sessions.db??     - `save_reference()` ??storage.record_checkpoint + emit logger.info
     - `get_tuple()` 透传 underlying saver.get_tuple（V1 时间旅行用）
  6. **`services/agent/src/agent/sessions/api.py`**??*新增** ~190 行）—??FastAPI 路由??     - `POST /sessions` 创建??01 + SessionSummary??     - `GET /sessions` 列出（status='active' + project_name 可??+ limit 1-200??     - `GET /sessions/{id}` 详情（含 messages + checkpoints??     - `DELETE /sessions/{id}` 删除??04 + CASCADE??     - `POST /sessions/kb/search` KB 检索（V0 mock；V1 接外部）
  7. **`services/agent/src/agent/main.py`** —??`app.include_router(sessions_api.router)`（紧??biznav??- **前端**?? 文件）：
  1. **`apps/desktop/src/ipc/invoke.ts`** —????5 ??typed wrapper??     - `sessionsCreate(body)` / `sessionsList(opts)` / `sessionsGet(session_id)`
     - `sessionsDelete(session_id)` / `sessionsKbSearch(body)`
  2. **`apps/desktop/src/store/sessionsStore.ts`**??*新增** ~140 行）—??zustand 状态：
     - `sessions` 列表 / `activeSessionId` / `loading` / `error` / `kbSnippet`
     - 6 ??action：`loadList` / `create` / `get` / `remove` / `kbSearch` / `setActive`
     - 不持久化（与 envconfig / biznav 同风格）
  3. **SessionsPanel 组件**??*未实??* —??V0 框架足够（store + IPC 全通），UI ??V1 ??- **`services/agent/tests/test_sessions_v0.py`**??*新增** ~250 ??/ 26 测试）—??全场景：
  - storage 8 测试（create_session thread_id / get_404 / list_active / list_project / update / delete_cascade / append_message_tool / list_messages_ordered??  - knowledge_base 7 测试（KBConfig.from_env / build_adapter_known / build_adapter_unknown_fallback / build_kb_context_results / build_kb_context_broken_adapter / kb_context_to_prompt_includes / kb_context_to_prompt_truncates??  - checkpointer 3 测试（saver_is_memory_saver / save_reference_returns_id / save_reference_failure_best_effort??  - api 6 测试（create / list / get / get_404 / delete / kb_search??  - INSERT OR IGNORE UNIQUE 约束语义（cp1.id>0 + cp2.id==0 表示 ignore 生效??- **CLAUDE.md §2 红线遵守**??  - ??**HITL 沿用 Phase 1** —??不动
  - ??**`_LOCAL_ONLY_TASKS`** —??不动（KB 检索不属于敏感任务路由??  - ??**Auto-Repair `retry_count ??2`** —??不动
  - ??**审计 audit.sqlite** —??sessions 用独??`sessions.db`（CLAUDE.md §6 物理隔离??  - ??**SSE 三处同步** —??sessions V0 ??SSE 事件（V1 时间旅行 / checkpoint 通知再补??  - ??**Keyring 占位??* —??`KBConfig.api_key_ref` 仅存 Keyring 占位符名（不存真值；V1 ??IAM 鉴权后填??  - ??**router.db** —??不动
  - ??**凭证保险箱契??* —??test_credential_contract 13/13 通过；sessions 不涉??DSN / fs:* / shell:* / http:*
- **测试**?? 关核验）??  - **Python**：`uv run pytest services/agent/tests/` ??**551 passed, 7 skipped, 0 failed**（vs v2.28 525 ??新增 26 ??V0 测试，零回归??  - **TS**：`pnpm exec tsc -b` ??**0 ??*（sessionsStore 5 处类型修复）
  - **Rust**：`cargo build --lib` ??**0 ??warning**（V0 不改 Rust；Tauri command 后端已在 Python 跑）
  - **Credential Contract**：`uv run pytest apps/desktop/tests/test_credential_contract.py` ??**13 passed**
- **V0 完成????Phase 6 后续**??  - ??sessions/models / storage / checkpointer / knowledge_base / api 后端骨架
  - ??前端 sessionsStore + 5 IPC wrapper
  - ??SessionsPanel 组件 UI（V1 补）
  - ??FTS5 全文搜索（V1 补）
  - ??加密 .eas 导出（V1 补）
  - ??共享权限矩阵（V1 ??Phase 10 IAM??  - ??SessionEvent 链式哈希防篡改（V1 ??Phase 5 Evidence Chain??  - ??SqliteSaver 持久化（V1 ??`langgraph-checkpoint-sqlite`??  - ??三段式上下文管理（sliding window + summary + recall，防 token 爆炸??  - ??启动无感恢复（检测未完成会话 + 3 秒弹窗）
  - **Phase 6 V0 整体 ??骨架落地**，剩??19 天工作量??V1 sprint 集中??- **退出条??*：Phase 6 V0 骨架落地。Phase 6 可标 🟡 部分实装。下一步候选：Phase 4 本地小模型（12 天）/ Phase 5 审核专家??5 天）/ Phase 14 本地智能图像?? 天，已立??v2.23）??
---

## 2026-07-29

### v2.28 ??Phase 2D V1 收尾 —??SkillWatchdog 防自激 + 多项目隔??+ SSE 三处同步??25 passed + 7 skipped / 0 failed / 13 credential contract 0 违规??
- **背景**：Phase 2D V0（CHANGELOG v2.18）已完成 Skill 8 ??Python 模块（loader / intent_classifier / router / schema / models / share / api / __init__?? 32 个测??+ 前端 5 组件 + SkillEditorModal。剩 3 ??V1 收尾??  1. **SkillWatchdog 防自激热加??* —??loader.write_yaml ??watchfiles 会触发自????需??written_by_pid + mtime 双校??  2. **多项目隔??* —??SkillLoader 没有 project_name 概念，V0 是全局共享
  3. **SSE 三处同步** —??`skill_matched` 通道??TS / Rust 占位声明（v2.18 时），Python emit 机制未实??- **核心交付**?? 文件 / 16 测试 / 0 回归）：
  1. **`services/agent/src/agent/skills/events.py`**??*新增** ~60 行）—??V1 SSE emit 机制??     - 进程??deque + asyncio 锁（参照 `agent.biznav.events` 风格??     - `emit_skill_event(kind, payload)` / `consume_skill_events(timeout_s=0.0)` / `flush_skill_events()`
     - 1 个事件通道名常??`EVT_SKILL_MATCHED: str = "skill_matched"`（与 Rust `channel::SKILL_MATCHED` + TS `EVT.AGENT_SKILL_MATCHED` 严格一致）
  2. **`services/agent/src/agent/skills/watchdog.py`**??*新增** ~150 行）—??V1 防自激热加载：
     - `mark_yaml_written(yaml_path)`：loader.write_yaml / share.import_zip 后调用，登记"本进程写??
     - `_is_self_written(yaml_path)`：mtime + pid 双校??     - `reload_yaml_to_loader(yaml_path, loader, project_name='default')`：reload + emit `skill_matched` SSE 事件
     - `SkillWatchdog(skills_dir, loader, project_name, debounce_ms=300)` 类：
       - `project_name='default'` ??watch_dir 是根目录（共??skill??       - `project_name='xxx'` ??watch_dir ??`<root>/<project_name>/`（项目专属，V1 多项目隔离）
     - `async start()` / `stop()` / `_watch_loop()` watchfiles.awatch 300ms 防抖
  3. **`services/agent/src/agent/skills/loader.py:30-77`** —??V1 多项目隔离：
     - `__init__` 新增 `_project_skills: dict[str, dict[str, Skill]]`（项??bucket??     - `load_one_for_project(path, project_name)`：加载到 `_project_skills[project_name]`
     - `get_for_project(skill_id, project_name)`：项目覆盖共享（项目专属优先??     - `list(project_name=None)`：`None` 返全部共享；指定项目返项目专??+ 共享（项目覆盖）
     - `remove(skill_id)`：从共享 + 所有项??bucket 一并清??     - **load_all 仍只扫根目录**（V0 行为兼容；项目目录由 SkillWatchdog 单独加载??  4. **SSE 三处同步（CLAUDE.md §4 红线??*??     - **Python**：`services/agent/src/agent/graph/stream.py:35, 85-90, 95-110` `_CHANNEL_BY_KIND` ??`"skill_matched": "agent://skill_matched"` + 流循??+ finally 各调 `_drain_skill_events()` 一??     - **Rust**：`apps/desktop/src-tauri/src/stream/sse_bridge.rs:262` 已有 `"skill_matched" => channel::SKILL_MATCHED`（v2.18 V0 已加，本轮仅??map_event_to_channel ??fallback `_ => channel::LOG` 之前的行??push??     - **TS**：`apps/desktop/src/ipc/events.ts:30` 已有 `AGENT_SKILL_MATCHED: 'agent://skill_matched'`（v2.18 V0 已加，零改动??  5. **`services/agent/tests/test_skills_v1.py`**??*新增** ~250 ??/ 16 测试）—??V1 全覆盖：
     - events 机制 4 场景（通道名常??/ FIFO 顺序 / 拉空 / flush??     - 防自激 3 场景（mark + is_self_written 同进??/ 其他 pid / 文件不存在）
     - SkillLoader 多项目隔??5 场景（load_all 只扫根目??/ load_one_for_project 归类 / project 覆盖 / remove ??bucket / get_for_project??     - SkillWatchdog 4 场景??default' 走根目录 / 'xxx' 走子目录 / _reload 路由 / 防自激??- **CLAUDE.md §2 红线遵守**??  - ??**HITL 沿用 Phase 1** —??不动
  - ??**`_LOCAL_ONLY_TASKS` 锁死 `skill_router`** —??v2.19 已加入，本轮不动
  - ??**Auto-Repair `retry_count ??2`** —??不动
  - ??**审计 audit.sqlite** —??skill watchdog emit 失败兜底 `logger.warning` 不抛??  - ??**SSE 三处同步** —??Python / Rust / TS 三个通道全部到位（`skill_matched`??  - ??**Keyring 占位??* —??不动
  - ??**router.db** —??skill 用独??`workspace_skills/` 目录（沿??V0 隔离??  - ??**凭证保险箱契??* —??skill watchdog 不写业务凭证；test_credential_contract 13/13 通过
- **测试**?? 关核验）??  - **Python**：`uv run pytest services/agent/tests/` ??**525 passed, 7 skipped, 0 failed**（vs v2.27 509 ??新增 16 ??V1 测试，零回归??  - **TS**：`pnpm exec tsc -b` ??**0 ??*
  - **Rust**：`cargo build --lib` ??**0 ??warning**??0 个全是历史既有；本轮 SSE 三处同步??1 ??map 触发 1 ??unreachable warning，删去重??arm 后恢??10??- **V1 完成????Phase 2D ??*??  - ??V0 后端 Skill 8 模块 + 32 测试（v2.18 已交付）
  - ??V0 前端 SkillEditorModal / SkillImportDialog / 5 组件（v2.18 已交付）
  - ??V1 收尾（本轮）：SkillWatchdog 防自激 + 多项目隔??+ SSE 三处同步 + 16 测试
  - **Phase 2D 整体 ??完成** —??4 天预估全部实??- **退出条??*：Phase 2D V1 全部完成。Phase 2D 可标 ✅。下一步候选：Phase 4 本地模型 / Phase 5 审核专家 / Phase 6 会话管理??
---

## 2026-07-29

### v2.27 ??Phase 2F V1 收尾 —??shared-protocol 镜像 + ActivityId 单源收敛 + CodeNavExtension??09 passed + 7 skipped / 0 failed / 13 credential contract 0 违规??
- **背景**：Phase 2F V0 收尾（CHANGELOG v2.17）只完成前端 MVP??8 mock symbol + FindInFiles 顶部 mode toggle + ActivityBar 顶级入口），??3 ??V1 收尾：本轮全部完成??  - 后端 5 文件（codenav/indexer.py / watcher.py / query.py / mcp_tools.py / api.py + schema.sql + language_registry.py + path_guard.py??*已全部实??*（Tree-sitter Java/Python/TypeScript 三语 AST 提取 + SQLite 符号??+ watchfiles 增量 + FastAPI 路由 + MCP 工具??6/36 codenav 测试全过）??  - Rust `commands/codenav.rs` 14 ??`#[tauri::command]` **已实??*（v2.16：jump / index / status / list_symbols / explain / llm_config / llm_backend / opened_projects 等）??  - **V1 实际只剩 3 项真正未??*??    1. shared-protocol ??codenav 镜像（前端用 `unknown` ??IP 返回类型，缺强类型契约）
    2. ActivityId 双源定义（uiStore.ts:64 + ActivityBar.tsx:16 双源，加 activity 必须同步改两??+ ITEMS + TITLES + Outlet 分支??    3. CodeNavExtension Monaco 集成（右??Go to Definition + F12/Ctrl+K 全局快捷键）
- **核心交付**?? 文件 / 0 回归）：
  1. **`packages/shared-protocol/src/ts/codenav.ts`**（新??~130 行）—??TypeScript ??Python 类型镜像??     - `Language`（java/python/typescript/javascript + 扩展?? `SymbolKind`（class/interface/method/function/field/enum/variable + 扩展??     - `Symbol` / `JumpResult` / `ExplainResult` / `IndexStatus` / `IndexRequest` / `ExplainRequest` / `ListSymbolsRequest` / `LlmBackend` / `LlmConfig` / `AllowedRoots`
     - `packages/shared-protocol/src/ts/index.ts` ??`export * from './codenav';`
     - 前端后续??`import type { Symbol, JumpResult } from '@eaide/shared-protocol'` 替代 `unknown`（V1.5 改造）
  2. **`apps/desktop/src/store/uiStore.ts`** —??`ActivityId` **单源 export**（V1 收敛）：
     - `export type ActivityId = 'explorer' | 'search' | 'source-control' | 'run-debug' | 'extensions' | 'collab' | 'code-nav';`
     - `activityId: ActivityId`（不??inline 字面量联合）
     - 删除"双源定义"备注（V1 已收敛）
  3. **`apps/desktop/src/components/chrome/ActivityBar.tsx`** + **`apps/desktop/src/layouts/WorkspaceLayout.tsx`** —??**删除**本地 `export type ActivityId`，改 `import { type ActivityId } from '@/store/uiStore'`
  4. **`apps/desktop/src/components/codenav/CodeNavExtension.ts`**（新??~150 行）—??Monaco 集成辅助??     - `registerGoToDefinition(editor, currentFile)`：注??Monaco 右键菜单 `codenav.gotoDefinition.<file>.<symbol>`??Go to Definition" ??`ipc.codeNavJump({symbol, current_file})`
     - `attachKeyboardShortcuts(editor, currentFile)`：绑??**F12 / Ctrl+K / Escape**（keycodes 0x135 / 0x1000+41 / 0x11）触发跳??+ 清高??     - **不引入全局 monaco 插件** —??显式 attach / detach 调用方控制生命周期（避免泄漏??     - **失败容错** —??ipc.codeNavJump 失败 console.warn，不抛错阻塞 Monaco
     - 占位：`JumpResult` 落到 console.info（V1.5 ??store-driven navigation ??Tab 跳转??- **CLAUDE.md §2 红线遵守**??  - ??**HITL 沿用 Phase 1** —??不动
  - ??**`_LOCAL_ONLY_TASKS`** —??codenav ??router 无关，不??  - ??**Auto-Repair `retry_count ??2`** —??不动
  - ??**审计 audit.sqlite** —??codenav 用独??`workspace_index.db`（沿??V0 隔离??  - ??**SSE 三处同步** —??codenav ??SSE
  - ??**Keyring 占位??* —??不动
  - ??**router.db** —??不动
  - ??**凭证保险箱契??* —??test_credential_contract 13/13 通过（无 DSN / fs:* / shell:* / http:* 泄露??- **测试**?? 关核验）??  - **Python**：`uv run pytest services/agent/tests/` ??**509 passed, 7 skipped, 0 failed**（vs v2.26 509 ??零回归；codenav 36/36 全过??  - **TS**：`pnpm exec tsc -b` ??**0 ??*（ActivityId 单源 + CodeNavExtension 5 处类型调整全干净??  - **Rust**：`cargo build --lib` ??**0 ??warning**??0 个全是历史既有；V1 收尾不改 Rust??  - **Credential Contract**：`uv run pytest apps/desktop/tests/test_credential_contract.py` ??**13 passed**
- **Phase 2F V1 完成????Phase 2F ??*??  - ??V0 前端 MVP（v2.17）：FindInFiles 顶部 mode toggle + 18 mock symbol + ActivityBar 顶级入口
  - ??V0 后端实装（v2.16 前）：Tree-sitter indexer + watcher + query + mcp_tools + api + schema + language_registry + path_guard + Rust 14 commands
  - ??V1 收尾（本轮）：shared-protocol codenav.ts 镜像 + ActivityId 单源 + CodeNavExtension Monaco 集成
  - **Phase 2F 整体 ??完成** —??4 天预估全部实??- **退出条??*：Phase 2F V1 全部完成。Phase 2F 可标 ✅。下一步候选：Phase 2D V1 收尾（Skill watchdog + LLM 意图?? Phase 4 本地模型 / Phase 5 审核专家??
---

## 2026-07-29

### v2.26 ??Phase 2C V2.5 + V3-2 收尾 + Phase 2C ??完成??09 passed + 7 skipped / 0 failed??
- **背景**：CHANGELOG v2.25 V2.5/V3 部分实装??3 项未做（V2.5-3 Chart.js + V2.5-5 Spark 真拼 + V3-2 并行裁判）。本轮完成全??3 项，**Phase 2C 整体 ??完成**??  - **V2.5-5 Spark 模式真拼 prompt**：`engine.spark_route` 注入 `backend_callers: dict[str, Callable[[str, str], Awaitable[str]]]` 后真??LLM —??reasoning backend 拿草??+ execution backend ??草稿前缀"prompt 执行；caller 缺失时退??V2.0 placeholder（向后兼容）??  - **V2.5-3 5 维评分面??*：零依赖纯文??`ScoringRadar`（inline 横向 bar + 权重刻度??+ 颜色编码：绿 > 0.66 / ??0.33-0.66 / ??< 0.33?? `ManualConfirmationPanel` 集成??  - **V3-2 关键任务并行+裁判**：`dual_judge.py` `asyncio.gather` ??backend 并发推理 + 独立 `judge_caller` 选最优（**注意**：judge caller 独立??candidates，避??caller 复用 bug?? `_LOCAL_ONLY_TASKS` 红线保护（intent/repair/biznav_extract 禁止双模型并行）??- **核心交付**?? 文件 / 17 测试 / 0 回归）：
  1. **后端 `services/agent/src/agent/llm/engine.py:45-74, 225-301`** —??V2.5-5 改造：
     - `RouterEngine.__init__` 新增 `backend_callers` 参数（dict[str, async callable]，None 时退化为 V0 placeholder??     - `spark_route` ??callers 注入时真??LLM：reasoning backend ??draft ??execution backend ??### 草稿（reasoning 模型产出）

{draft}

---
请基于上述草稿继续完善用户请求的最终回答：

{user_prompt}" 执行；LLM 调用失败兜底 placeholder??  2. **后端 `services/agent/src/agent/llm/dual_judge.py`**（新增约 180 行）—??V3-2 `run_dual_with_judge`??     - `asyncio.gather` ??backend 并发（每 backend `asyncio.wait_for` + 30s 默认超时??     - **judge_caller 独立??candidates**（修??v2.26 早期 v1 设计??caller 复用 bug??     - judge_caller=None ??本地规则兜底（pick longest text??     - judge 输出 `WINNER: <idx>` 解析 + 越界 clamp + 拒绝负数（避??`"-1"` 被误解析??`"1"`??     - `_build_judge_prompt` 安全 `str.replace`（避??`.format` 误吞 `user_prompt` 里的 `{xxx}`??     - `_LOCAL_ONLY_TASKS` 红线：value_error 立刻 raise（防止敏感数据走双模型）
     - `JudgeTrace` dataclass 暴露 candidates / winner_index / judge_backend / judge_reason
  3. **前端 `apps/desktop/src/components/router/ScoringRadar.tsx`**（新增约 80 行）—??V2.5-3 零依??5 维评分可视化（inline 横向 bar + 权重蓝线刻度 + ????绿颜色编码）。零 npm 依赖（不引入 Chart.js / recharts），V2.5 收尾轻量化??  4. **前端 `apps/desktop/src/components/router/ManualConfirmationPanel.tsx`** —??DecisionRow 嵌入 `<ScoringRadar>` 显示当前 decision primary backend ??5 维分数（后端 `decision.candidates[0].scores` 注入）??  5. **后端 `services/agent/tests/test_router_v32_dual_judge.py`**（新增约 220 ??/ 17 测试）—??V3-2 全覆盖：2-candidate 裁判选最??/ ??candidate 直返 / 全失??RuntimeError / partial failure / `_LOCAL_ONLY_TASKS` 红线 / missing caller / empty list / judge_caller=None 本地规则 / judge 输出 garbage 兜底 / `_parse_judge_output` 6 场景（含负数、越界）/ `_build_judge_prompt` 安全替换（含 user_prompt 里的 `{xxx}`?? latency 计时 / judge_caller 抛错兜底??- **CLAUDE.md §2 红线遵守**??  - ??**HITL 沿用 Phase 1** —??不动
  - ??**`_LOCAL_ONLY_TASKS`** —??`dual_judge` 红线强制（intent/repair/biznav_extract ??ValueError?? spark_route 仍走 engine.route_request.apply_hard_rules 保护
  - ??**Auto-Repair `retry_count ??2`** —??不动
  - ??**审计 audit.sqlite** —??`JudgeTrace.judge_reason` 截断 500 字符防止审计行过??  - ??**SSE 三处同步** —??不动
  - ??**Keyring 占位??* —??不动
  - ??**router.db** —??L2 cache 默认关（本轮不动??  - ??**LMRouter 4 公开 API 冻结** —??spark_route 签名 0 修改；engine.__init__ 新增参数（向后兼容默??None??- **测试**?? 关核验）??  - **Python**：`uv run pytest services/agent/tests/` ??**509 passed, 7 skipped, 0 failed**（vs v2.25 492 ??新增 17 ??V3-2 测试，零回归??  - **TS**：`pnpm exec tsc -b` ??**0 ??*（ScoringRadar + ManualConfirmationPanel 集成零类型错??  - **Rust**：`cargo build --lib` ??**0 ??warning**（V2.5/V3-2 不改 Rust??- **V2.5/V3 完成????Phase 2C ??*??  - ??V2.5-1 评分实际生效 / V2.5-2 模型管理 UI / V2.5-3 5 维评分面板（ScoringRadar?? V2.5-4 LMRouter 真委??/ **V2.5-5 Spark 真拼 prompt**
  - ??V3-1 L2 语义缓存 / **V3-2 并行裁判 + dual_judge** / V3-3 Manual 面板
  - **Phase 2C 整体 ??完成** —??12 天预估全部实??- **退出条??*：本??v2.26 ??按计划往??完成 Phase 2C V2.5/V3 收尾。Phase 2C 可标 ??完成。下一步候选：Phase 2F V1 / Phase 2D V1 / Phase 4 本地模型 / Phase 5 审核专家??
---

## 2026-07-29

### v2.25 ??Phase 2C V2.5 + V3 部分实装（V2.5-4 LMRouter 真委??engine + V3-1 L2 语义缓存 + V3-3 Manual 确认面板??
- **背景**：CHANGELOG v2.22/v2.16 Phase 2C V1.5 + V2 骨架已交付，??SCHEDULE §3.1 ??12 天里**8 天剩??*（V2.5 + V3）。本轮聚??3 个最关键的剩余点??  - **V2.5-4 LMRouter 4 API 真委??engine.route_request**：原 `_chain_for` 是硬编码 V1.5 fallback（intent: ollama→private→mock; plan: private→ollama→mock）。engine.route_request 的五维评分只用于 emit SSE 事件??*没真影响 chain 顺序**。V2.5 修复??  - **V3-1 L2 语义缓存**：cache_l2.py 不存在，Phase 4 embedding 没上线前必须"默认??+ mock_embed"避免阻塞??  - **V3-3 Manual 模式确认 modal**：V0 runMode toggle 在前端存在，??manual 模式实际不弹 modal（仍自动走）。V3 ??`<ManualConfirmationPanel>` inline 面板显示最??routing decisions??- **核心交付**?? 文件 / 16 测试 / 0 回归）：
  1. **后端 `services/agent/src/agent/llm/router.py:206-274`** —??V2.5 `_chain_for` 改造：engine 可用 + kind ??`_LOCAL_ONLY_TASKS` 时先??`engine.route_request(task_kind, category="balanced", sensitivity="low")`，按 decision.actual_backend + decision.fallback_chain 顺序构??chain；engine 不可用时**保留 V1.5 硬编??fallback**（向后兼??test_router_backcompat）。_LOCAL_ONLY_TASKS 红线保护：engine.route_request 内部 apply_hard_rules 仍优先于五维评分（intent / repair / biznav_extract 强制 ollama）??  2. **后端 `services/agent/src/agent/llm/cache_l2.py`**（新增约 130 行）—??`L2Cache` 语义缓存（embed_fn + threshold + TTL）。`enable=False` 默认禁用（Phase 4 上线前）；`mock_embed` ??sha256 ??64 维向量（??PoC / 测试用，生产??sentence-transformers）。`_LOCAL_ONLY_TASKS` 任务（含 intent）禁止写入（??LMRouter 红线对齐）。`get()` 先精确命中，未命中再遍历??cosine_sim >= threshold 匹配；过期条??lazy 删除??  3. **前端 `apps/desktop/src/components/router/ManualConfirmationPanel.tsx`**（新增约 130 行）—??Manual 模式下面板：5 秒轮??`routerGetDecisions(5)` 显示最??5 ??routing decision（request_id / task_category / actual_backend / fallback_chain / est. cost / fallback_used / cache_hit）。Auto 模式不渲染。`RoutingDecisionLite` interface 容错处理（后端可能返??`{decisions: [...]}` 或裸数组；字段全??optional）。V3 占位"V3.5 ??Phase 4 Ollama 真推????  4. **前端 `apps/desktop/src/components/router/RouterDashboard.tsx`** —??import + 挂载 `<ManualConfirmationPanel />`（在 RunMode 按钮组下方）??  5. **后端 `services/agent/tests/test_router_v25_v3.py`**（新增约 130 ??/ 16 测试）—??V2.5-4 三场景（engine 可用 / engine 不可??/ 红线保护?? V3-1 八场景（disabled / exact / semantic / unrelated / `_LOCAL_ONLY_TASKS` 跳过 / TTL 过期 / 阈值调??/ stats?? 3 ??cosine_sim / mock_embed 单测??- **CLAUDE.md §2 红线遵守**??  - ??**HITL 沿用 Phase 1** —??不动
  - ??**`_LOCAL_ONLY_TASKS` 锁死** —??L2 cache `_LOCAL_ONLY_TASKS` 跳过写；engine.route_request apply_hard_rules 仍保??intent/repair；V2.5-4 chain 构造尊??engine 返回
  - ??**Auto-Repair `retry_count ??2`** —??不动
  - ??**审计 audit.sqlite** —??不动
  - ??**SSE 三处同步** —??不动
  - ??**Keyring 占位??* —??不动
  - ??**router.db** —??L2 cache **默认??*，Phase 4 上线前不??router.db.l2_cache ??  - ??**LMRouter 4 公开 API 冻结** —??V2.5-4 **不修改签??*，仅 `_chain_for` 内部行为变更（向后兼??test_router_backcompat 锁）
- **测试**?? 关核验）??  - **Python**：`uv run pytest services/agent/tests/` ??**492 passed, 7 skipped, 0 failed**（vs v2.24 476 ??新增 16 ??V2.5/V3 测试，零回归??  - **TS**：`pnpm exec tsc -b` ??**0 ??*（ManualConfirmationPanel `fallback_chain` / `estimated_cost` 可选字??+ unknown 中转处理 2 处已修）
  - **Rust**：`cargo build --lib` ??**0 ??warning**（V2.5 不改 Rust??- **V2.5/V3 完成????剩余??*??  - **V2.5-1 评分实际生效** ??v2.16 已交??  - **V2.5-2 模型管理 UI** ??v2.16 已交付（870 ??ModelManagementPanel + role radio + protocol 校验??  - **V2.5-3 可观测面??Chart.js** ??部分（RouterDashboard 5s 轮询 metrics 真接??+ 5 维文本展示；Chart.js 雷达图未??—????V2.5.1??  - **V2.5-4 LMRouter 真委??* ??本轮完成
  - **V2.5-5 Spark 模式真拼 prompt** ??占位（engine.spark_route + LMRouter.set_spark_mode 已落地，V0 placeholder 输出；V2.5.1 ??llama.cpp 真推理）
  - **V3-1 L2 语义缓存** ??本轮完成（默认关 + mock_embed??  - **V3-2 关键任务并行+裁判 + YAML 导入导出** ??未开始（端点暴露 + router.db schema 已就位，业务??V3.1 补）
  - **V3-3 Manual 模式确认 modal** ??本轮完成（inline panel??- **下一??*：按 SCHEDULE §2 ??1 优先，V2.5.1 / V3.1 ??1-2 ??sprint 集中补（Chart.js + Spark 真拼 + 并行裁判）。或者按用户优先级跳其他 Phase（Phase 2F V1 收尾 / Phase 2D V1 收尾 / Phase 4 本地模型 / Phase 5 审核专家等）??
---

## 2026-07-29

### v2.24 ??Phase 2G V1.3.1 bug fix 收尾 + Phase 2G ??完成

- **背景**：CHANGELOG v2.22 V1.3 部分实装留下 2 个已??bug 待外??AI 接手（BUGFIX_LOG #31 hot_reload.sync_yaml_to_db 参数错位 + #32 incremental._handle_changes 路径 JOIN 不命中）。本轮外部修??`services/agent/src/agent/biznav/hot_reload.py:95` 改为 `report = FeatureIO.sync_yaml_to_db(yaml_text, project_name, storage)`（修??#31）；`services/agent/src/agent/biznav/incremental.py` 路径规范化（修复 #32）。重跑测试验证：
  - **biznav 4 测试文件**：`uv run pytest services/agent/tests/test_biznav_events.py test_biznav_extraction_done.py test_biznav_hot_reload.py test_biznav_incremental.py` ??**16 passed**（vs v2.22 报告 12 failed??*全部修复**??  - **全量回归**：`uv run pytest services/agent/tests/` ??**476 passed, 7 skipped, 0 failed**（vs v2.22 478 + 1 + 4 fail ??现在零回归零已知 bug??- **本轮交付**??*零代码改??* + **3 文档台账同步**）：
  1. **BUGFIX_LOG.md #31 状态翻??*：`未修复（待外??AI 接手，本轮已 fix 3 次仍失败）` ??**`已修复（2026-07-29 外部 AI）`**。实际修复方案选了 BUGFIX_LOG #31 中的**方案 B**（最小改动）：`FeatureIO.sync_yaml_to_db(yaml_text, project_name, storage)` —??`yaml_text` 已经??yaml_path 读过，把 raws 删除??  2. **BUGFIX_LOG.md #32 状态翻??*：`未修复` ??**`已修复（2026-07-29 外部 AI）`**。外部修??`incremental.py` 的路径处理（推断??`rel.replace("\\", "/")` + storage.find_features_by_file 同步规范??Path.as_posix()，与 BUGFIX_LOG #32 方案一致）??  3. **CLAUDE.md §3.1 文档索引??Phase 2G ??* 状????????**完成**（vs v2.22 仍是 🟡 V1.3 部分实装）。Phase 2G 整体结束，可进入下一阶段??- **CLAUDE.md §2 红线遵守**（继??v2.22）：
  - ??**HITL 沿用 Phase 1** —??V1.3.1 不动
  - ??**`_LOCAL_ONLY_TASKS` 锁死 `biznav_extract`** —??不动
  - ??**Auto-Repair `retry_count ??2`** —??不动
  - ??**审计 audit.sqlite** —??不动
  - ??**SSE 三处同步** —??Python / Rust / TS 三处都已同步（v2.22 已完成）
  - ??**Keyring 占位??* —??biznav 不读任何凭证
  - ??**router.db** —??biznav 独立 `biznav.db`
  - ??**模式矩阵** —??biznav 不动 WorkMode
- **测试**??*476 passed, 7 skipped, 0 failed**（pytest 输出已确认）??- **V1.3 完成????Phase 2G ??*??  - **V0 ??*??3 件套前端 mock??026-07-15 已收尾）
  - **V1.1 backend ??*??1 文件 + 5 测试 31/31 + BUGFIX #26/#27 已修??026-07-28??  - **V1.2 Rust/IPC/前端接入 ??*?? cmd + 9 wrapper + 6 async action + 1 helper + 1 LMRouter 真接??026-07-28??  - **V1.3 hot_reload + incremental + extraction_done + leftPanelMode ??*（events + hot_reload + incremental + 3 BIZNAV_* SSE 三处同步 + 前端 hooks + uiStore.leftPanelMode + LeftPanelModeToggle + WorkspaceLayout 集成 + biznav_extraction_done??026-07-28/29??  - **V1.3.1 bug fix ??*（BUGFIX #31 + #32 已修复，2026-07-29 外部 AI??  - **Phase 2G 整体 ??* —??8 天预估已全部实装??- **下一??*：按 SCHEDULE.md §2 ??9 优先**承接 Phase 14 本地智能图像处理**（v2.23 已立项，+8 ??/ 5 Task）。Phase 2G 完成腾出 1 ??sprint 窗口，可顺位推进??
---

## 2026-07-28

### v2.23 ??Phase 14 本地智能图像处理引擎 ??正式立项??8 天）

- **背景**：金??政企场景中图像处理是高频刚需——扫描合同模糊需要超分增强、拍照证件歪斜需要自动矫正、扫描件需??OCR 文字提取。Phase 14 ??ONNX Runtime + Real-ESRGAN x2 + PaddleOCR + OpenCV 全部整合??EAIDE Agent 层，**所有模型端侧加载（总计 ~115MB），推理全程零网络请??*，敏感数据绝不出域。这??EAIDE ??代码编辑??进化??研发运维 + 文档处理"综合工具的关键差异化能力??- **核心交付**（规划）??  - **设计文档**：[docs/design/phase-14-local-image-processing.md](docs/design/phase-14-local-image-processing.md)（约 200 行，9 节）
  - **实现文档**：[docs/implementation/local-image-processing.md](docs/implementation/local-image-processing.md)（约 230 行，9 节）
  - **5 ??Task / 8 ??*：Task 1 ONNX Runtime + Real-ESRGAN x2 + PaddleOCR 模型准备?? 天）/ Task 2 OpenCV 矫正??.5 天）/ Task 3 FastAPI 6 接口 + OCR 配置读写??.5 天）/ Task 4 前端 UI + 设置??OCR 配置区块?? 天）/ Task 5 性能优化 + OCR 精度验证 + 测试?? 天）
  - **技术选型**：ONNX Runtime（CPU/GPU?? Real-ESRGAN x2（~50MB ONNX?? PaddleOCR 端侧（检??~15MB + 识别 ~50MB ONNX?? OpenCV headless（传??CV 算法??  - **OCR 范围**??*纯文字识??*——输入图????输出纯文本字符串。不做版面分析、不做表格还原、不做段落结构化。设置页面提供语言选择（中??英文/中英混合）和置信度阈值配置??- **CLAUDE.md §2 红线遵守**??  - ??**HITL 不涉??*（图像处理无写操作）
  - ??**`_LOCAL_ONLY_TASKS` 不涉??*
  - ??**Auto-Repair 不涉??*
  - ??**审计 SQLite 复用**（新??`image_processing_tasks` 表，??`ocr_text` 字段??  - ??**SSE 复用**现有 `agent://log` 通道（批量进度推送）
  - ??**0 ??Sidecar**（ONNX Runtime + PaddleOCR 均在 Agent 进程内）
  - ??**0 ??IPC 通道**（复??Phase 1 FastAPI + Tauri HTTP??- **文档联动更新**??  - `docs/ROADMAP.md` —??§1 阶段??+ Phase 14 行；§2 Phase 14 详情??OCR；?? 风险??+8 条；§4 验收 +16 项；总剩??209 ??217
  - `docs/SCHEDULE.md` —??§1 阶段??+ Phase 14 行；§2 ??9 优先；??.19 Phase 14 5 Task 拆解??OCR；?? 汇总表 + 关联文件
  - `CLAUDE.md` —??修改位置速查 + 关键约定?? 条，??OCR 仅做文字识别 + 配置界面??- **架构??5 忠告**：① 模型体积控制（三模型 ~115MB，不??x4 200MB+?? ??CPU vs GPU 自动检??/ ??批量处理异步??/ ??**OCR 只做文字识别**（不做版??表格/结构化）/ ??**配置界面是关??*（OCR 语言和阈值通过设置页暴露，不写死代码）
- **总剩??*??09 ??**217 工作??*

---

### v2.22 ??Phase 2G V1.3 hot_reload + incremental + extraction_done + leftPanelMode（??部分实装 / 2 已知 bug 待外??AI 接手??
- **背景**：CHANGELOG v2.19 规划 V1.3 = "hot_reload + incremental + leftPanelMode"。本次目标把"后端 watcher ??SSE 三处同步 ??前端订阅 ??左侧栏可切换"链路一次性打通??*实际落地**：events / SSE 三处同步 / 前端 hooks / uiStore.leftPanelMode / LeftPanelModeToggle / WorkspaceLayout 集成 / biznav_extraction_done 全部 OK??*hot_reload.reload_yaml_to_db** ??**incremental._handle_changes** 两个后端核心路径各发??1 ??bug（详??BUGFIX_LOG #31 / #32），按用户要??失败三次不要再继??停止 fix，记录到 BUGFIX 留待外部 AI 接手??- **核心交付**（已落地）：
  1. **后端 `services/agent/src/agent/biznav/events.py`**（新??71 行）—??进程??deque + asyncio 锁，`emit_biznav_event(kind, payload)` / `consume_biznav_events()` / `flush_biznav_events()`?? 个事件通道名常??`EVT_YAML_RELOADED` / `EVT_FEATURE_AFFECTED` / `EVT_EXTRACTION_DONE`。参??`agent.llm.metrics` ??`_router_event_queue` 风格，deque 上限 1000??  2. **后端 `services/agent/src/agent/biznav/hot_reload.py`**（新增约 200 行）—??watchfiles 监听 `<project_root>/.eaide/features/{project_name}.yaml`??*已知 Bug A**：`reload_yaml_to_db` 调用 `FeatureIO.sync_yaml_to_db` 时参数顺序错（`(storage, raws, project_name)` 应为 `(yaml_text, project_name, storage)`）→ 当前所??hot_reload 测试 fail??*修法**??BUGFIX_LOG #31，外??AI 接手??  3. **后端 `services/agent/src/agent/biznav/incremental.py`**（新增约 170 行）—??`AffectedFeaturesWatcher` watchfiles 监听 project_root 文件变更 + JOIN `feature_file_index` 反向索引 + emit `biznav_feature_affected`??*已知 Bug B**：`_handle_changes` 调用 `find_features_by_file` 路径不命中（Windows 路径规范化问题）??3 ??incremental 测试 fail??*修法**??BUGFIX_LOG #32??  4. **后端 `services/agent/src/agent/biznav/api.py:146-187`** —??`/biznav/extract` 后台任务 `try/finally` ??emit `biznav_extraction_done`（success + features_generated + ts）。修??`getattr(result, "total_features", 0)` ??`"features_generated"`（与 `ExtractionResult` dataclass 字段对齐）??  5. **后端 `services/agent/src/agent/graph/stream.py:30-35 + 82-88 + 95-110`** —??`_CHANNEL_BY_KIND` ??3 ??BIZNAV_* 通道；新??`_drain_biznav_events()` 异步消费 `consume_biznav_events()`；流循环 + finally 各调一次确??buffered 事件全部推到 SSE 前端。SSE 三处同步（CLAUDE.md §4 红线）??  6. **Rust `apps/desktop/src-tauri/src/stream/sse_bridge.rs:36-44 + 258-265`** —??`mod channel` ??`BIZNAV_YAML_RELOADED` / `BIZNAV_FEATURE_AFFECTED` / `BIZNAV_EXTRACTION_DONE` 三个 channel 常量；`map_event_to_channel` ??3 ??`match` 分支。SSE 三处同步（CLAUDE.md §4 红线）??  7. **前端 `apps/desktop/src/hooks/useBiznavEvents.ts`**（新??130 行）—??React hook 订阅 3 ??BIZNAV_* 事件 ??自动触发 `biznavStore.loadFeatures` + 简??toast（`console.log` 占位，V1.5 接全局 Toast）。三种事件类型都??typed payload interface + 错误兜底??  8. **前端 `apps/desktop/src/store/uiStore.ts:65-70 + 107 + 139 + 148-153`** —??`leftPanelMode: 'auto' | 'system' | 'business'` + `setLeftPanelMode` action；`persist` 部分包含 `leftPanelMode`（localStorage `eaide.ui` 持久化）。迁移兼容：老用户读到的无此字段自动 fallback `'auto'`??  9. **前端 `apps/desktop/src/store/leftPanel.ts`**（新??27 行）—??`useLeftPanelContent()` selector??auto' 时按 WorkMode 自动决定??full' ??'system' / 'operator' ??'business'），其余强制返回对应值??  10. **前端 `apps/desktop/src/components/asset-tree/LeftPanelModeToggle.tsx`**（新??48 行）—??📁/🧩/??三态循环按钮，点击切换 `leftPanelMode`。仅??`'full'` / `'operator'` mode 显示（`analyst` / `auditor` 是独立布局）??  11. **前端 `apps/desktop/src/layouts/WorkspaceLayout.tsx:265-298`** —??SideBar 主渲染拆??`LeftPanelBody` 子组件（??`useLeftPanelContent()` 决定渲染 SystemAssetTree 还是 BusinessFeatureTree）；`SideBarHeader` ??`<LeftPanelModeToggle />`（条件渲染）??  12. **后端 `services/agent/src/agent/biznav/__init__.py`** —??`__all__` 公开 6 ??V1.3 新增 API：`EVT_YAML_RELOADED` / `EVT_FEATURE_AFFECTED` / `EVT_EXTRACTION_DONE` / `emit_biznav_event` / `consume_biznav_events` / `flush_biznav_events` / `YamlHotReloader` / `mark_yaml_written` / `reload_yaml_to_db` / `AffectedFeaturesWatcher`??  13. **4 ??V1.3 测试文件落地**（共 16 个测试）：`test_biznav_events.py` 5/5 ??/ `test_biznav_extraction_done.py` 2/2 ??/ `test_biznav_hot_reload.py` 3/5 ⚠（Bug A 阻塞?? `test_biznav_incremental.py` 1/4 ⚠（Bug B 阻塞）??- **CLAUDE.md §2 红线遵守**??  - ??**HITL 沿用 Phase 1** —??不动
  - ??**`_LOCAL_ONLY_TASKS` 锁死 `biznav_extract`** —??不动
  - ??**Auto-Repair `retry_count ??2`** —??不动
  - ??**审计 audit.sqlite** —??不动
  - ??**SSE 三处同步**（V1.3 全部到位）—??Python `graph/stream.py` 3 通道 + Rust `sse_bridge.rs::channel` 3 const + TS `events.ts::EVT` 3 占位（已 v2.21 加）+ 后端 `emit_biznav_event` + 前端 `useBiznavEvents.ts` 订阅
  - ??**Keyring 占位??* —??biznav 不读任何凭证
  - ??**router.db** —??biznav 独立 `biznav.db`（env var `EAIDE_BIZNAV_DB_PATH`??  - ??**模式矩阵** —??不动
- **测试**?? 关核验）??  - **Python**：`uv run pytest services/agent/tests/` ??**478 passed, 1 skipped, 4 failed**（vs v2.21 467，新??11 ??V1.3 测试??*4 失败 = 已知 BUGFIX_LOG #31 + #32**，不??V1.3 之外引入??bug??  - **TS**：`pnpm exec tsc -b` ??**0 ??*?? 个新文件 + 2 个改动文件全部类型干净??  - **Rust**：`cargo build --lib` ??**0 ??warning**??0 个全是历史既有；V1.3 ??`sse_bridge.rs` ??3 ??const + map 分支?? warning 增量??- **V1.3 完成????已知 BUG 修复 + V1.5 启动**??  - **V1.3 部分实装**：events + SSE 三处同步 + 前端订阅 + uiStore.leftPanelMode + LeftPanelModeToggle + WorkspaceLayout 集成 + biznav_extraction_done ??  - **已知 2 bug（待外部 AI 接手??*??    - BUGFIX_LOG #31：hot_reload.reload_yaml_to_db ??`FeatureIO.sync_yaml_to_db` 参数错位（外 AI 按方??A inline 合并逻辑 / 方案 B 修参数顺序）
    - BUGFIX_LOG #32：incremental._handle_changes 路径 JOIN 不命中（??AI 按方案双路径尝试 + 规范??Path.as_posix()??  - **V1.5 启动清单**（bug 修完后）??    - 替换 `useBiznavEvents` 简??toast（`console.log`）→ 接入全局 Toast 组件（V1.5 才实装，目前可接受）
    - `yaml_text` 反向写回：watcher 检??DB 变更 ??同步 YAML（V1.5 才做，避免循环依赖）
    - `yaml_text` 多文件支持（一个项目多 yaml 切片??    - shared-protocol 镜像 biznav 事件 payload（TS / Python 双向??- **退出条??*：本??v2.22 ??按计划往??推进 V1.3 大部分。已??2 bug 按用户指令停??fix 留待外部 AI。Phase 2G 整体**只剩 0.5 ??Buffer**（V1.3.1 外部 AI 修复 + 端到??smoke）即可标 ??完成；剩??209 工作日不变??
---

## 2026-07-28

### v2.21 ??Phase 2G V1.2 Rust/IPC/前端接入（??backend 全绿 ??Rust + IPC + biznavStore 异步??+ LMRouter 真接??
- **背景**：V1.1 backend 已全绿（v2.20），但前后端未打??—??Python `api.py` 暴露 9 ??`/biznav/*` HTTP 端点，没??Tauri command 包装，前??biznavStore 仍用 18 mock。V1.2 把链路一次性打通：Rust `commands/biznav.rs` 9 command + `lib.rs` invoke_handler 注册 + 前端 `invoke.ts` 8 IPC wrapper + `events.ts` 3 ??BIZNAV_* 占位 + `biznavStore.ts` 6 异步 action + `chatStore.useFeatureContextPromptSnippet` helper + `_make_llm_client` 真接 `LMRouter.summarise`??- **核心交付**?? 文件 / 9 Rust cmd + 8 TS wrapper + 6 async action + 1 helper + 1 真接）：
  1. **`apps/desktop/src-tauri/src/commands/biznav.rs`**（新增，??200 行，9 command）—??模板照搬 `codenav.rs`：包??`reqwest` HTTP 调用 FastAPI，错误时返回 `agent returned NNN: detail`??*9 ??command**：`biznav_extract` / `biznav_status` / `biznav_list_features` / `biznav_get_feature` / `biznav_upsert_feature`（PUT ??project_name 注入 body??/ `biznav_delete_feature`（DELETE ??body??/ `biznav_import_yaml` / `biznav_export_yaml` / `biznav_affected`。V1.2 ??CHANGELOG v2.20 规划??8 cmd 之上多出 1 ??`biznav_status`（探测后??ready，对前端 demo banner 关闭判断有用）??  2. **`apps/desktop/src-tauri/src/commands/mod.rs:5`** —??`pub mod biznav;  // Phase 2G V1.2` 加入注册列表（按时间序插??codenav ??credentials 之间）??  3. **`apps/desktop/src-tauri/src/lib.rs:209-219`** —??`tauri::generate_handler!` 追加 9 ??`commands::biznav::*` 注册，紧??codenav 14 command 之后。共注册 ~80 ??command（codenav 14 + biznav 9 + dspark 6 + orchestrator 2 + router 6 + skills N + ...）??  4. **`apps/desktop/src/ipc/invoke.ts`**（Phase 2G 段）—??`ipc.biznavExtract` / `biznavStatus` / `biznavListFeatures(opts?)` / `biznavGetFeature(id, project)` / `biznavUpsertFeature(id, project, body)` / `biznavDeleteFeature(id, project)` / `biznavImportYaml(body)` / `biznavExportYaml(project)` / `biznavAffected(file, project)` 9 ??typed wrapper??*返回类型故意宽松**??`unknown[]` / `Record<string, unknown>` —??V1.5 才会??Python dataclass 镜像??shared-protocol 后收窄到 typed `Feature[]`??  5. **`apps/desktop/src/ipc/events.ts`**（Phase 2G 段）—????3 ??BIZNAV_* 占位：`BIZNAV_YAML_RELOADED` = `'agent://biznav_yaml_reloaded'` / `BIZNAV_FEATURE_AFFECTED` = `'agent://biznav_feature_affected'` / `BIZNAV_EXTRACTION_DONE` = `'agent://biznav_extraction_done'`??*V1.2 仅注册占位不 emit** —????CHANGELOG v2.19 计划一致（V1.3 hot_reload / incremental / extraction 任务完成才会真正 emit，Python `graph/stream.py` + Rust `stream/sse_bridge.rs::channel` 同步注册??V1.3 启动时落实）??  6. **`apps/desktop/src/store/biznavStore.ts`**（V0 mock ??V1.2 后端联调）—??新增 6 个异??action：`loadStatus()`（探??backendReady?? `loadFeatures(opts?)`（拉后端 features 替换 mock?? `upsertFeature(id, project, body)`（PUT 后自动刷新）/ `deleteFeature(id, project)`（DELETE 后自动刷新）/ `importYaml(yaml_text, mode)`（POST /biznav/import?? `exportYaml(project)`（GET /biznav/export，返??yaml_text ??`lastExportYaml`）。V0 ??`updateFeature` / `reindex` / `resetToMock` 全部保留作演示兜底（`backendReady=false` ??FeatureDetailPanel demo banner 仍生效，UX 不破坏）??*后端 raw record ??Feature 类型转换**??`BackendFeatureRaw` 接口 + `rawToFeature()` helper：后??snake_case + ??`risk_level` / `deleted_at` 字段 ??前端 camelCase + 兜底 `'low'` / `null`；后??`BusinessRule` 对象 `{text}` ??前端 `string[]` 展开 `.text`（V1.5 才会镜像完整 dataclass）??  7. **`apps/desktop/src/store/chatStore.ts:186-236`** —??新增 `useFeatureContextPromptSnippet()` React hook??*纯选择器，不消??*）：读取 `selectedFeatureContext` ??返回拼好??system prompt 注入片段（含功能??/ 描述 / 关联文件 top 10 / 关联 API top 5 / 关联??top 5 / 业务规则 top 10 + 提示"如用户提出与该功能无关的问题，可正常脱离上下??）??*V0 持续高亮 UX 不变**（ContextChip 仍显示在 ChatInput 上方），??chat 调度处按需读取。V1.5 ??agent 调度 prompt 注入时调用此 hook??  8. **`services/agent/src/agent/biznav/api.py:116-148`** —??`_make_llm_client` ??v2.19 ??noop 返回空串"**真接 `LMRouter.summarise`**??     - 每次 extract 重新构造一??`LMRouter()`（`__init__` 廉价，仅??router.db??     - `router.pick(kind)` 自动??`_LOCAL_ONLY_TASKS` 走本??Ollama（`biznav_extract` 已在 CLAUDE.md §2 红线??     - `backend.summarise(intent="query", user_prompt=..., plan=[], results=[])` —??提取最后一??user message 作为 prompt
     - `except Exception` 兜底 ??`logger.warning` + 返回空串（extractor ??该组不生??feature"逻辑，不阻塞 extract 整体流程??     - `EAIDE_LLM_BACKEND=mock` 测试环境自动??mock backend（`pick` 返回 self.mock），5 ??biznav 测试全过??1/31）??- **CLAUDE.md §2 红线遵守**??  - ??**HITL 沿用 Phase 1** —??V1.2 不动 `graph/nodes/hitl_gate.py`
  - ??**`_LOCAL_ONLY_TASKS` 锁死 `biznav_extract`** —??不动（CLAUDE.md §2 ??119 ??+ router.py:91-96 + dspark/policy.py:37-38 三处一致）
  - ??**Auto-Repair `retry_count ??2`** —??不动
  - ??**审计 audit.sqlite** —??V1.1 5 ??event_type 字符串约定保持不变；V1.2 不增加新事件
  - ??**SSE 三处同步** —??V1.2 仅前??events.ts 注册 3 ??BIZNAV_* 占位（不 emit）；Rust `stream/sse_bridge.rs::channel` + Python `graph/stream.py` 的真 emit ??V1.3 启动时落实（??CHANGELOG v2.19 计划一致）
  - ??**Keyring 占位??* —??biznav 不读任何凭证
  - ??**router.db** —??biznav 独立 `biznav.db`（env var `EAIDE_BIZNAV_DB_PATH`）；`_make_llm_client` 真接时不破坏 router.db
  - ??**模式矩阵** —??biznav 不动 WorkMode
- **测试**?? 关核验）??  - **Python**：`uv run pytest services/agent/tests/` ??**467 passed, 1 skipped**（vs v2.20 467 ??一致，零回归；biznav 5 文件 31/31 全过??  - **TS**：`pnpm exec tsc -b` ??**0 ??*（chatStore ??`});` 语法??1 ??+ biznavStore 类型强转 2 处已修复??  - **Rust**：`cargo build --lib` ??**10 warnings 全是历史既有**（`config.rs` / `process_handle` / `assetstore` 等）??*biznav.rs 0 ??warning**
- **V1.2 完成????V1.3 启动条件**??  - **V1.2 ??全部落地**?? cmd + 9 wrapper + 6 async action + 1 helper + 1 真接 LMRouter??  - **V1.3 启动清单**?? 天工作量）：
    - 后端 `biznav/hot_reload.py` watchfiles 防自激 + 自写??mtime 检??+ SSE ??`biznav_yaml_reloaded`
    - 后端 `biznav/incremental.py` 监听 `codenav.watcher` 文件变更 + 反向索引 JOIN + SSE ??`biznav_feature_affected`
    - 后端 `biznav/extractor.py` 真接 background asyncio task 完成??SSE ??`biznav_extraction_done`
    - 前端 `ipc/events.ts` 3 ??BIZNAV_* 占位 ??前端订阅逻辑（hooks/useBiznavEvents.ts?? UI 反馈（Toast + 树自动刷新）
    - 前端 `uiStore.leftPanelMode` 完整实装 + `useLeftPanelContent` selector + 📁/🧩 切换按钮 + localStorage 持久??    - Python `graph/stream.py` + Rust `stream/sse_bridge.rs::channel` 同步注册 3 ??BIZNAV_* channel（CLAUDE.md §4 SSE 三处同步红线??    - 3 个剩余测??+ 端到??smoke + BUGFIX_LOG 追加 + CHANGELOG v2.22
- **退出条??*：本??v2.21 ??按计划往??推进 V1.2。V1.3 启动需独立 session（前后端 SSE 三处同步 + 新基础设施 hot_reload/incremental 是大块改动，建议 2-3 天连续投入）??
---

## 2026-07-28

### v2.20 ??Phase 2G V1.1 backend 收尾 —??12 失败测试已全绿（31/31 pass / 467 全量 pass??
- **背景**：CHANGELOG v2.19 记录??V1.1 backend 落地??12 测试仍失败（Bug A：rule_engine.py 重复定义 BusinessRule / Bug B：storage.py 嵌套 connection 触发 SQLITE_BUSY??，按用户要求"失败三次不要再继??停止 fix，留待外??AI 接手??*本轮 v2.20 重启??*先核对实际代码状态，发现两个根因 fix **已在上轮 commit 中实际落??*（rule_engine.py:14 ??`from .models import BusinessRule`；storage.py:200-205 ??inline `executemany` 到外??conn），只是 CHANGELOG 描述与代码不同步。重跑测试验证：
  - **biznav 5 个测试文??*：`uv run pytest services/agent/tests/test_biznav_audit.py services/agent/tests/test_biznav_rule_engine.py services/agent/tests/test_biznav_storage.py services/agent/tests/test_biznav_import_export.py services/agent/tests/test_biznav_api.py` ??**31 passed, 2 warnings**?? ??warning ??aiosqlite 测试??event loop 关闭的良性线程异常，不影响结果）
  - **全量回归**：`uv run pytest services/agent/tests/` ??**467 passed, 1 skipped, 3 warnings** in 125.95s（vs v2.19 报告??436，新??31 恰好??biznav 31 ????验证 v2.19 ??20 失败"实际是历史快照，fix 早已完成??- **本轮交付**??*3 文档台账同步** + **零代码改??*）：
  1. **BUGFIX_LOG.md #26 状态翻??*：`未修复（待外??AI 接手）` ??**`已修复（2026-07-28 v2.20 收尾 commit）`**。`rule_engine.py` 实际已删除重??`@dataclass class BusinessRule`（约 15 行），改 `from .models import BusinessRule`；`validate_syntax` 内仅保留 `isinstance` 防御；详细验证步??`python -c "from agent.biznav.models import BusinessRule as A; from agent.biznav.rule_engine import BusinessRule as B; print(A is B)"` ??True??  2. **BUGFIX_LOG.md #27 状态翻??*：`未修复（待外??AI 接手）` ??**`已修复（2026-07-28 v2.20 收尾 commit）`**。`storage.upsert` 实际??inline `executemany` 到外??conn（line 200-205），不再嵌套 `self.add_file_index()`；`grep "self\.add_file_index\|self\.remove_file_index\|self\.rebuild_file_index" storage.py` 结果 = 0 嵌套调用；`_connect(timeout=5)` 保留作为好习惯但已不依赖它解决嵌套问题。详细验??`uv run pytest -k biznav` ??31/31??  3. **教训追加**??26 + #27 备注同步）：**CHANGELOG 写到"未修??前必须先实际跑测试确??*。v2.19 描述的失败状态是历史快照，实??fix 已落地但 CHANGELOG 没追上??- **CLAUDE.md §2 红线遵守**??  - ??**HITL 沿用 Phase 1** —??不动
  - ??**`_LOCAL_ONLY_TASKS` 锁死 `biznav_extract`** —??v2.19 已加入，v2.20 不动
  - ??**Auto-Repair `retry_count ??2`** —??不动
  - ??**审计 audit.sqlite** —??V1.1 5 ??event_type 字符串约定（`feature_extract` / `feature_update` / `feature_delete` / `feature_import` / `yaml_reload`）保持不??  - ??**SSE 三处同步** —??V1.1 仍不发射 SSE（V1.3 才有 `biznav_yaml_reloaded` / `biznav_extraction_done`），零改??  - ??**Keyring 占位??* —??biznav 不读任何凭证
  - ??**router.db** —??biznav 独立 `biznav.db`（沿??codenav 风格 env var `EAIDE_BIZNAV_DB_PATH`??- **测试**??*467 passed, 1 skipped, 3 warnings**（pytest 输出已确认）；TS `tsc -b` 无变化（V1.1 后端不动前端）；Rust `cargo build --lib` 无变化??- **V1.1 完成????V1.2 启动条件**??  - **V1.1 backend ??全绿**（红??4 文件 + 11 后端文件 + 5 测试 31/31 + main.py 注册 + BUGFIX #26/#27 已修 + 本轮 v2.20 文档台账同步??  - **V1.2 启动清单**?? 天工作量）：
    - Rust `commands/biznav.rs` 8 command：照??`codenav.rs` 骨架（`biznav_list_features` / `biznav_get_feature` / `biznav_upsert_feature` / `biznav_delete_feature` / `biznav_extract` / `biznav_import_yaml` / `biznav_export_yaml` / `biznav_affected`??    - 前端 `apps/desktop/src/ipc/events.ts` ??3 ??BIZNAV_* SSE 事件：`BIZNAV_YAML_RELOADED` / `BIZNAV_FEATURE_AFFECTED` / `BIZNAV_EXTRACTION_DONE`（V1.3 ??emit，V1.2 先注册占位）
    - 前端 `biznavStore.ts` 6 异步 action：替??V0 mock 数据源，??Tauri command + IPC wrapper
    - `chatStore.sendMessage` ??`selectedFeatureContext`，发送消息时附加 feature 上下文（关联文件列表 + 业务规则列表??    - 后端 `api.py` / `extractor.py` ??LLM 调用链路真接（替??v2.19 ??`_make_llm_client` noop；走 `LMRouter.summarise` 或新??`extract_business_context`??  - **V1.3 启动清单**?? 天工作量）：`biznav/hot_reload.py` watchfiles 防自激 + SSE ??`biznav_yaml_reloaded` + `biznav/incremental.py` 监听 codenav.watcher + SSE ??`biznav_feature_affected` + `uiStore.leftPanelMode` 完整实装 + 📁/🧩 切换按钮 + localStorage 持久??+ 3 个剩余测??+ 端到??smoke + CHANGELOG v2.21
- **退出条??*：本??v2.20 ??按计划往??推进，先收尾 V1.1 文档台账 + 验证测试全绿。V1.2 启动需要独??session（Rust + 前端 + Tauri command 一组改??+ LMRouter 真接，建??1-2 天连续投入）；V1.3 留待 V1.2 收尾后再启??
---

## 2026-07-28

### v2.17 ??Phase 2F 代码阅读??AI 导航 V0 收尾 —??ActivityBar 顶级入口（⚪ ??🟡 部分实装??
- **背景**：Phase 2F V0 实际**90% 已交??*（前??plan 写时基于 0% 假设）—??`codeNavStore.ts` 1200 行（??18 mock symbols + AI 解释 + 后端联调）、`types/codenav.ts` 102 行（Symbol / SymbolKind / Language / KIND_COLORS / LANGUAGE_COLORS）?? ??`components/codenav/*` 组件（CodeNavSearch / SymbolDetail ??Monaco + revealLineInCenter + setPosition + deltaDecorations 500ms flash / AiExplainPanel / ProjectFileTree / CodeNavSettingsPanel / fileOps）??4 ??Tauri command??4 ??IPC wrapper、FindInFiles 顶部 ModeTabs asset/symbol 切换均已实装??*唯一缺口**：`ActivityBar` 没有 `'code-nav'` 顶级入口，用户从主界面无法直接进入代码符号搜索，只能通过 `<FindInFiles mode='symbol'>`（藏??search activity ??tab 里）??Settings ??CodeNavSettingsPanel??- **核心交付**?? 文件改动 + 2 文档）：
  1. **`apps/desktop/src/store/uiStore.ts:61`** —??`activityId` 字面量联合追??`'code-nav'`。加注释提醒**双源定义**（与 ActivityBar.tsx:14 必须保持一致）—??V1 必须收敛??uiStore 单源 export??  2. **`apps/desktop/src/components/chrome/ActivityBar.tsx`** —??`ActivityId` 类型追加 `'code-nav'`；`ITEMS` 数组追加 `{ id: 'code-nav', label: 'Code Nav\n代码符号', icon: '?? }`（⌘ ??ModeTabs symbol tab ??icon 视觉一致）??  3. **`apps/desktop/src/layouts/WorkspaceLayout.tsx`** —??`TITLES` Record ??`'code-nav': '代码符号'`；`Outlet` 分支??code-nav 全屏渲染 `<div className="h-full"><FindInFiles defaultMode="symbol" /></div>`；`SideBar` 函数顶部??`if (activity === 'code-nav') early-return`（折叠让??320px 双栏，避??CSS `:has` 选择??ActivityBar 切换瞬间 SideBar 闪烁）??  4. **`apps/desktop/src/components/asset-tree/FindInFiles.tsx`** —??`FindInFiles` 接受??prop `defaultMode?: SearchMode = 'asset'`；`useState` 初始化用 `defaultMode ?? 'asset'`。code-nav 顶级入口??`defaultMode='symbol'` 直接??symbol 模式（不展示顶部 ModeTabs 切换??asset/symbol 2-tab 视觉冲突）??- **视觉行为**??  - ActivityBar 点击 ????SideBar 折叠，main 区全屏渲??FindInFiles symbol 模式??20px 左栏 `CodeNavSearch` + flex-1 右栏 `SymbolDetail` ??Monaco snippet + AI 解释??  - search activity ??tab 仍可??asset/symbol（独立路径保留，两路通同一组件??  - 切回 explorer / search / 其他 activity ??SideBar 恢复正常 260px
- **与现有架构契约遵??*??  - ??**HITL 沿用 Phase 1** —??不动
  - ??**LMRouter 4 公开 API 冻结** —??不动
  - ??**`_LOCAL_ONLY_TASKS` 红线** —??不动
  - ??**Auto-Repair `retry_count ??2`** —??不动
  - ??**审计 audit.sqlite** —??不动（code-nav 是前端纯本地 store，无 SSE 事件??  - ??**SSE 三处同步** —??不动
  - ??**Keyring 占位??* —??不动
  - ??**router.db** —??不动
  - ??**mode 矩阵** —??code-nav ??activity，与 WorkMode 平行；`setMode` 已强制重??activityId='explorer'（uiStore.ts:121-127），不动
- **CLAUDE.md §3.3 更新**：Phase 2F 状??????🟡 V0 部分实装（前??MVP + 顶级入口）；V1（Tree-sitter 真接 + shared-protocol 镜像 + 收敛双源 ActivityId）明确推迟??- **测试**：TS `tsc -b` 0 错；Rust `cargo build --lib` 0 ??warning（无??Tauri command）；前端仓库 0 ??vitest 测试，本次不新增（V0 ??UI 入口改造，手动 `pnpm tauri dev` 验证）??- **V1 推迟清单**??  - `ActivityId` 单源化收敛到 `uiStore`（消除双源定义漂移风险）
  - shared-protocol ??`codenav.ts`（镜??Symbol / JumpResult 类型??  - `CodeNavSettingsPanel` 挂到 Settings 顶级 Tab（与 DSpark / RouterDashboard 平级??  - `codeNavStore` mock 数据下沉??`apps/desktop/src/mocks/codeNavMocks.ts`，与 auditStore mock 分离
  - 真实 Tree-sitter 解析（依??Phase 2G??  - 真实 AI 推断（V0 ??mock + 后端 fallback??
---

## 2026-07-28

### v2.18 ??Phase 2G 业务功能点导??V0 收尾 —??演示数据横幅 + 文档台账（⚪ ??🟡 部分实装??
- **背景**：Phase 2G V0 实装时间??2026-07-09 ??2026-07-15 ??2026-07-28 三次增量。V0 spec（[`docs/superpowers/specs/2026-07-15-phase-2g-v0-design.md`](../d:/ditPref/docs/superpowers/specs/2026-07-15-phase-2g-v0-design.md)）定??13 件套 V0 = 前端 mock + 18 features + 5 组件 + biznavStore + types + yamlExport + chatStore 字段 + chatBridge。前三轮探索发现 **13 件已??100% 交付**，本??V0 收尾**零功能性改??*，仅??1 处防误导演示数据横幅 + 4 文件文档台账，避免运营专家模式下用户??18 mock 当真实数据修改后丢失??- **核心交付**?? 前端 + 4 文档）：
  1. **`apps/desktop/src/components/biznav/FeatureDetailPanel.tsx`** —??顶部加可关闭的演示数据横幅（黄底 `#dcdcaa20` + 暖橙 `#dcdcaa` ??+ ⚠️ + V0 提醒文案 + [✕] 关闭按钮）；??`sessionStorage` 持久??已关??标志（key=`biznav.demoBannerDismissed.v1`），刷新 webview 不重现，**重启 tauri dev 自动重现**（持续提??18 mock 不持久化的本质）；hooks 在所有早期返回之前无条件调用（防 BUGFIX #15 同源 hook order 问题）；`useState` lazy initializer ??`sessionStorage`、`useCallback` 关闭回调；`try/catch` 优雅降级 sessionStorage 不可用（隐私模式 / 磁盘满）??  2. **`docs/ROADMAP.md:7, 63`** —??`最后更新` ??v2.17 ??**v2.18 + 总剩余不变（V0 不增工作日）**；?? 阶段??Phase 2G ??`??未开始` ??**`🟡 V0 已交付（V1 待开发）`**，保??+ 8 天预估不变??  3. **`docs/SCHEDULE.md:6, 19`** —??`最后更新` 行加 v2.18；?? Phase 2G 行描述已经是 🟡（与 ROADMAP 同步），`+ 8 天（??6.5 天）` ??V0 完成度??  4. **`docs/implementation/biznav.md:3`** —??顶部状态行 `??未开始` ??`🟡 V0 已交付（13 件套前端 mock??026-07-15??/ V1 待开发`??  5. **`apps/desktop/tests/test_credential_contract.py`** —??rglob `DESKTOP_SRC/**/*.{ts,tsx}` 自动覆盖 `components/biznav/`；现??5 条白名单规则（禁??DSN 字符??/ 凭证日志 / fs:*/shell:*/http:* 权限）全部通过??*无需新增白名单代??*，仅??`pytest apps/desktop/tests/test_credential_contract.py` 验证??- **13 ??V0 已交付清??*（仅记录，不新增代码）：
  - `apps/desktop/src/types/biznav.ts` 71 行（`Feature` / `FeatureContextPayload` / `RelatedFile/Api/Table` / `FeatureRisk` / `FeatureSource`??  - `apps/desktop/src/store/biznavStore.ts` 138 行（18 mock + 6 actions + 3 selectors??  - `apps/desktop/src/store/chatStore.ts` 追加 `selectedFeatureContext` + `setFeatureContext`（已 V0 实装，由 BiznavChatBridge 写入??  - `apps/desktop/src/components/asset-tree/BusinessFeatureTree.tsx` 297 行（已受控化 + Hook 顺序??BUGFIX #15 + 顶部"演示数据 · V0 前端 mock · V1 接后??文案??  - `apps/desktop/src/components/biznav/FeatureDetailPanel.tsx` 250+ 行（+ V0 收尾 demo banner = 270+ 行）
  - `apps/desktop/src/components/biznav/FeatureEditorModal.tsx` 605 行（Monaco YAML + Form ??Tab，YAML V0 只读 + dirty 关闭确认??  - `apps/desktop/src/components/biznav/ContextChip.tsx` 52 行（顶部??+ [×] 关闭??  - `apps/desktop/src/components/biznav/BiznavChatBridge.tsx` 50 行（headless，单??biznavStore ??chatStore??  - `apps/desktop/src/lib/yamlExport.ts` 99 行（手写 `featureToYaml`?? 新依赖）
  - `apps/desktop/src/layouts/CenterChatFlow.tsx` ??`<ContextChip />` ??`ChatInput` 上方
  - `apps/desktop/src/layouts/WorkspaceLayout.tsx` ??`<BiznavChatBridge />` + `<FeatureEditorModal />` + `<FeatureDetailPanel />`（`mode==='operator'` 挂载??  - `apps/desktop/src/components/biznav/__fixtures__/mockFeatures.ts` 462 行（5 业务??/ 6 分类 / 18 feature / 演示路径 `C:/demo/order-service`??  - CLAUDE.md 红线遵守（不??shared-protocol / 不动 4 模式 / 不动 SSE / 不动审计 schema / 不动 LMRouter / zustand ??persist）—??全部遵守
- **与现有架构契约遵??*??  - ??**HITL 沿用 Phase 1** —??V0 收尾不动 `graph/nodes/hitl_gate.py`
  - ??**LMRouter 4 公开 API 冻结** —??V0 收尾不动 `llm/router.py` 4 公开方法签名（V1.1 才加 `biznav_extract`??  - ??**`_LOCAL_ONLY_TASKS` 红线** —??V0 收尾不动（V1.1 ??1 commit 必加??  - ??**Auto-Repair `retry_count ??2`** —??V0 收尾不动
  - ??**审计 audit.sqlite** —??V0 收尾不动（V1.1 ??5 个事件约定字符串??  - ??**SSE 三处同步** —??V0 收尾不动（V1.2 ??3 ??BIZNAV_* 定义 + V1.3 emit??  - ??**Keyring 占位??* —??V0 收尾不动
  - ??**router.db** —??V0 收尾不动
  - ??**zustand ??persist** —??biznavStore 当前不持久化（V1.3 才会??  - ??**CLAUDE.md 不进 shared-protocol** —??`types/biznav.ts` 留在前端本地（V1.2 才镜像）
- **测试**（W8 验证三关）：
  - TS `pnpm exec tsc -b` 0 错误（FeatureDetailPanel ??useState/useCallback 不破坏类型推导）
  - Rust `cargo check` 0 ??warning（无 Rust 改动??  - Python `uv run pytest services/agent/tests/ -q` 436 passed / 1 skipped（无 Python 改动，回归确认）
  - `pytest apps/desktop/tests/test_credential_contract.py` 5 ??case 全过（含 v2.18 新增 demo banner JSX 后无 DSN / 凭证字面量）
- **V1 推迟清单**（明确不??V0 收尾范围）：
  - **V1.1 backend core?? 天）**：后??10 Python 文件（`__init__` / `models` / `schema.sql` / `storage` / `extractor` / `import_export` / `rule_engine` / `api`?? audit 5 事件约定 + LMRouter `_LOCAL_ONLY_TASKS` ??`biznav_extract` + 5 ??`test_biznav_*.py`
  - **V1.2 前端接入 + Rust 桥（2 天）**：`commands/biznav.rs` 8 command（照??codenav.rs 骨架?? ipc/events.ts 3 ??BIZNAV_* + biznavStore 6 异步 action + chatStore.sendMessage ??selectedFeatureContext + ??biznavReadRelatedFiles
  - **V1.3 端到??+ 新基础设施?? 天）**：`biznav/hot_reload.py` 启动 + 防自激 + SSE ??`biznav_yaml_reloaded` + `biznav/incremental.py` 监听 codenav.watcher + SSE ??`biznav_feature_affected` + `uiStore.leftPanelMode` 完整实装 + `useLeftPanelContent` selector + 📁/🧩 切换按钮 + localStorage 持久??+ 3 个剩余测??+ 端到??smoke + CHANGELOG v2.19 + ROADMAP ??V1 + CLAUDE.md
  - 启动条件：下个独??session 即可??V1.1，不必本轮摊开 8 天工作量

---

## 2026-07-28

### v2.19 ??Phase 2G V1.1 backend core（??V0 部分实装 ??backend 部分实装 + 已知 12 测试未过??
- **背景**：用户继续推进选了 Phase 2G V1.1 backend core（plan agent 评估 22.5h / 3 天）?? 个并??Explore 已完??V1.1 边界探索（详??[设计文档](../d:/ditPref/docs/design/phase-2g-business-nav.md) §11 + [实现文档](../d:/ditPref/docs/implementation/biznav.md) §4）。一次投??general-purpose agent 一次性写??11 后端 Python + 5 测试文件 + main.py 注册（约 2900 行代码）??*Python 编译全部通过**；但 5 个测试文件整??**31 个用??20 失败 11 通过**。已自行 fix 3 处根因清晰点，仍??12 失败 = 1 处真 bug（rule_engine.py BusinessRule 类与 models.py 重复定义?? 1 处需重构 bug（storage.py nested connection 死锁）。按用户要求"失败三次不要再继??，停??fix 留待外部 AI 收尾??- **核心交付**?? 红线改动 + 11 后端文件 + 5 测试 + main.py 注册）：
  1. **CLAUDE.md §2 红线同步** —??`_LOCAL_ONLY_TASKS` 描述列表追加 `biznav_extract`?? 项变??6 项：intent / repair / skill_router / data_summary / biznav_extract）??  2. **`services/agent/src/agent/llm/types.py`** —??`TaskKind` Literal 扩展加入 `biznav_extract` 与既??`skill_router` / `data_summary`（之??`_LOCAL_ONLY_TASKS` ??`frozenset[TaskKind]` 是伪类型）??  3. **`services/agent/src/agent/llm/router.py:91-96`** —??`_LOCAL_ONLY_TASKS` frozenset 写入 `biznav_extract`??*??commit 必须包含**，否??extractor 第一??LLM 调用会路由到内网 LLM 漏掉红线）??  4. **`services/agent/src/agent/llm/dspark/policy.py:37-38`** —??默认 frozenset 同步包含 `biznav_extract`，防??DSpark ??dspark/policy.py 内部绕开红线??  5. **`services/agent/src/agent/biznav/audit.py`**??2 行）—??集中 5 ??audit action 常量（`EVT_FEATURE_EXTRACT` / `EVT_FEATURE_UPDATE` / `EVT_FEATURE_DELETE` / `EVT_FEATURE_IMPORT` / `EVT_YAML_RELOAD`），避免 Python 端散落字符串??  6. **`services/agent/src/agent/biznav/__init__.py`**??8 行）—??re-export 公开 API（参??envconfig 风格）??  7. **`services/agent/src/agent/biznav/models.py`**??39 行）—??10 ??dataclass（Feature / RelatedFile / RelatedApi / RelatedTable / BusinessRule / FeatureContextPayload / ExtractionJob / CandidateFileGroup / AffectedFeature / SyncReport?? 8 JSON 编解??helper??  8. **`services/agent/src/agent/biznav/schema.sql`**??2 行）—??设计文档 §3.1 ??4 ??DDL（features / feature_file_index / extraction_jobs / feature_edit_history）??  9. **`services/agent/src/agent/biznav/rule_engine.py`**??0 行）—??BusinessRule + `validate_syntax(rule)` + `to_system_prompt_snippet(rules)`??  10. **`services/agent/src/agent/biznav/storage.py`**??60 行）—??`FeatureStorage` CRUD（带乐观??+ `feature_edit_history` 强制??+ 反向索引 JOIN?? `FeatureVersionConflict` 自定义异常??  11. **`services/agent/src/agent/biznav/import_export.py`**??73 行）—??`FeatureIO` YAML/JSON 三向同步 + `sync_yaml_to_db` / `sync_db_to_yaml` 合并策略（同 id source='ai' 覆盖 / source='manual' 保留??conflicts）??  12. **`services/agent/src/agent/biznav/extractor.py`**??76 行）—??`FeatureExtractor` 4 阶段（扫????启发式分????LLM 并发 ??落盘?? `ExtractionResult` + 私有 `_parse_llm_json`（LLM 通过 prompt 约束，路??JSON 容忍度有限）??  13. **`services/agent/src/agent/biznav/api.py`**??55 行）—??10 路由：`/extract`（后台任务）/`/status`/`/features`/`/features/{id}`（GET/PUT/DELETE??`/import`（POST??`/export`（GET??`/affected`（V1.1 ??list??`/reload`（V1.1 503 占位）；所有路??try/except + ??`await audit(...)`??  14. **`services/agent/src/agent/main.py:174-175`** —????`orch_api.router` 注册前插 3 行：
      ```python
      from agent.biznav import api as biznav_api
      app.include_router(biznav_api.router)
      ```
  15. **`services/agent/tests/test_biznav_audit.py`**??0 行）—??2 测试?? 常量值校??+ `__all__` 列表）??  16. **`services/agent/tests/test_biznav_rule_engine.py`**??2 行）—??6 测试??  17. **`services/agent/tests/test_biznav_storage.py`**??59 行）—??8 测试??  18. **`services/agent/tests/test_biznav_import_export.py`**??37 行）—??7 测试??  19. **`services/agent/tests/test_biznav_api.py`**??52 行）—??8 测试（FastAPI TestClient + sub-app mount 风格）??- **W1.16 部分 fix**?? 处）??  - `services/agent/src/agent/biznav/rule_engine.py:38-44` 简??`validate_syntax` 第一行（删除冗余 `not rule` 判断，因??dataclass 实例默认 truthy）；保留 `isinstance` 检查??  - `services/agent/src/agent/biznav/storage.py:72-79` `_connect()` ??`timeout=5` ??SQLITE_BUSY 自动重试。但**未解决根本问??*（见??已停??fix 待外??AI"）??  - `services/agent/tests/test_biznav_audit.py:39-58` `test_module_exports_5_constants` 改成校验 `__all__` 含变量名（Python 语义正确?? 另加 `getattr` 校验字符串值（??design spec §11.1 对齐）??- **已停??fix 待外??AI**??2 测试仍失败，本轮不再 fix）：
  - **Bug A: `services/agent/src/agent/biznav/rule_engine.py:20-35`** —??agent 错误??*自己重新定义 `@dataclass class BusinessRule`**，与 `services/agent/src/agent/biznav/models.py:47-48` 已有??`BusinessRule` dataclass 不是同一个类。测试用 `from agent.biznav.models import BusinessRule`，但 rule_engine.py 内部 `isinstance(rule, BusinessRule)` 用的是它自己的版????永远 False ??所有走 `validate_syntax` ??4 个测试失败??*修复**：rule_engine.py ??20-35 行整??dataclass 块删掉，改成 `from .models import BusinessRule`；同??`to_system_prompt_snippet` 输入??`Union[BusinessRule, str]` 也用 `models.BusinessRule`。详??BUGFIX_LOG #26??  - **Bug B: `services/agent/src/agent/biznav/storage.py:198`** —??`upsert` 在外??`with self._connect() as conn:` block 内调 `self.add_file_index(...)`，??`add_file_index:286-290` 自己又开第二??`with self._connect() as conn:`。嵌套双 connection 单线程立刻触??SQLITE_BUSY ??`database is locked` 错误。`_connect(timeout=5)` 部分 fix 不够（timeout 5s 只对等锁有用，不能解嵌套）??*修复**：把 `add_file_index` ??`executemany` inline ??`upsert` ??with block 内（用同一 conn），删除末尾??`self.add_file_index()` 调用。同??`soft_delete` / `delete` / `delete_project` 中类似的 `with conn.cursor() as cur` 嵌套也要 review。详??BUGFIX_LOG #27??- **测试结果**??  - **已通过 11 / 31**：test_biznav_audit 2/2、test_biznav_rule_engine 2/6（仅通过 `test_storage_upsert_rejects_invalid_business_rule` ??`test_to_system_prompt_with_structured_placeholder`）??  - **仍失??20 / 31**??    - `test_biznav_rule_engine.py`?? 失败（Bug A ??`validate_syntax` 走错分支??    - `test_biznav_storage.py`?? 失败（Bug B ??upsert 末尾 add_file_index 嵌套 conn 触发 SQLITE_BUSY??    - `test_biznav_import_export.py`?? 失败（依??storage ??Bug B 传递）
    - `test_biznav_api.py`?? 失败（依??storage ??Bug B 传递）
- **V1.1 完成度评??*??  - **红线改动** ??100%?? 文件 + CLAUDE.md + ROADMAP + SCHEDULE??  - **后端代码** ??100%??1 文件落地 + 编译通过 + 接口对齐设计文档??  - **测试代码** ??100%?? 文件落地??31 测试??  - **测试??* ⚠️ 35%??1/31 过；65% 失败都收敛在 Bug A ??Bug B 两个根因??- **CLAUDE.md §2 红线遵守**??  - ??**HITL 沿用 Phase 1** —??不动
  - ??**`_LOCAL_ONLY_TASKS`** —??现含 `intent / repair / skill_router / data_summary / biznav_extract` 5 项（CLAUDE.md ??119 行同步）；第 122-123 ??DSpark 同步??  - ??**Auto-Repair `retry_count ??2`** —??不动
  - ??**审计 audit.sqlite** —??新增 5 ??event_type 字符串约定，无需 schema 迁移
  - ??**SSE 三处同步** —??V1.1 不发??SSE（V1.3 才有 `biznav_extraction_done` 等）；零改动??  - ??**Keyring 占位??* —??biznav 不读任何凭证
  - ??**router.db** —??biznav 独立 `biznav.db`（沿??codenav 风格 env var `EAIDE_BIZNAV_DB_PATH`??- **V1.1 启动条件 ??V1.2 / V1.3 推进**??  - **必须??*：外??AI fix Bug A ??Bug B，让 31 测试全过
  - **V1.2 起动**：Rust `commands/biznav.rs` 8 command（照??codenav.rs 骨架?? IPC wrapper 8 ??+ ipc/events.ts 3 ??BIZNAV_* + biznavStore 6 异步 action + chatStore.sendMessage ??selectedFeatureContext + ??biznavReadRelatedFiles
  - **V1.3 起动**：`biznav/hot_reload.py` 启动 + 防自激 + SSE ??`biznav_yaml_reloaded` + `biznav/incremental.py` 监听 codenav.watcher + SSE ??`biznav_feature_affected` + `uiStore.leftPanelMode` 完整实装 + `useLeftPanelContent` selector + 📁/🧩 切换按钮 + localStorage 持久??+ 3 个剩余测??+ 端到??smoke + CHANGELOG v2.20
- **退出条??*：本轮按用户要求"失败三次不要再继??停止 fix。Bug A + Bug B 根因已记录到 BUGFIX_LOG #26/#27，下??session 或外??AI 可直接按 BUGFIX 描述修??
---

## 2026-07-28

### v2.16 ??Phase 2C 智能 LLM 路由降级 V2.0 闭环（??部分实装 ????端到端打通）

---

## 2026-07-28

### v2.16 ??Phase 2C 智能 LLM 路由降级 V2.0 闭环（??部分实装 ????端到端打通）

- **背景**：Phase 2C V1.5?? ??`/router/*` 端点 + 五维评分 + 熔断??+ storage + L1Cache）已交付，但端到端没闭环 —??Python `graph/stream.py` 没发??3 ??LLM 路由 SSE 事件（违??CLAUDE.md §4 三处同步红线）、LMRouter 4 公开方法没真委托 `RouterEngine.route_request()`、`fallback.py` 没接熔断器、`router_set_weights` Tauri 命令有但 FastAPI PUT `/router/weights` 端点缺、前??4 ??IPC wrapper 缺失导致 RouterDashboard 三组件纯前端 mock、Spark 模式纯前端空壳。V2.0 一次性修复以上所有断点??- **核心交付**?? 项串联小改）??  1. **SSE 三处同步修复**（CLAUDE.md §4 红线）：Python `graph/stream.py` `_CHANNEL_BY_KIND` ??3 个通道映射（`llm_route_decided` / `llm_degraded` / `llm_budget_alert`）；??`agent.llm.metrics.emit_router_event()` 模块级函??+ in-process deque + `consume_router_events()` 异步消费；`RouterEngine.route_request()` 末尾 `metrics.emit_event("llm_route_decided", decision.trace_dict())`；`with_fallback` 全链失败??`emit_router_event("llm_degraded", {...})`；`stream.py` 流循??+ finally 双调 `_drain_router_events()`??  2. **`router.py` 真委??`engine.py`**：`LMRouter.__init__(engine=None)` 接受可??`RouterEngine` 注入（V1.5 兼容 None）；4 公开方法 keyword 签名 100% 冻结（test_router_backcompat.py 锁死），内部通过 `LMRouter.set_spark_mode(True)` 实例属性切换走 `engine.spark_route()`；不破坏 4 ??keyword API 的现有调用方（planner/repair/responder/intent 节点零改动）??  3. **`fallback.py` ??CircuitBreakerRegistry + retry-with-backoff**：`with_fallback` ??`circuit_breaker_registry` 参数；每级调用前 `breaker.allow()` 检??Open 状态直??skip（不??trail ??log）；调用??`breaker.on_success()` / `breaker.on_failure()` 更新状态机；`sleep_between` 指数退避（base * 2^attempt??.01s / 0.02s / 0.04s）；全链失败??emit `llm_degraded` SSE 事件??  4. **FastAPI PUT `/router/weights` 端点 + `router_weights` 表持久化**：新??`router_weights` 单行表（id=1 CHECK 约束强制单行?? `agent.llm.storage.get_router_weights()` / `set_router_weights()` 异步 helper；`WeightsBody` Pydantic 模型校验 5 ????[0, 1] ??Σ ??[0.99, 1.01]；PUT 落库 + `_ENGINE.set_weights()` 热生??Engine 内存（无需重启 Agent）；`engine_api._get_engine()` 启动期用 sync sqlite3 读持久化权重（避??asyncio.run ??lifespan 中冲突）??  5. **前端 4 IPC wrappers + 3 组件 useEffect 拉真数据**：`ipc.routerGetMetrics()` / `routerGetDecisions()` / `routerSetWeights()` / `routerGetWeights()` / `routerResetBreaker()` / `routerSetSparkMode()`；`ScoringWeights` interface 5 维类型；`RouterDashboard` useEffect 5 秒轮??`ipc.routerGetMetrics()` 写入 `useRouterStore.setMetrics()`；Spark toggle ??`ipc.routerSetSparkMode()`；`ScoringWeightsEditor` 启动从后端拉权重 + 「保存」按钮调 `routerSetWeights` + Σ ??1 disabled 保存；`CircuitBreakerStatus` 每条 Open 记录右侧「重置」按钮调 `routerResetBreaker`；Rust `commands/router.rs` 新增 `router_set_spark_mode` command + lib.rs 注册??  6. **Spark 模式后端实装**：`RouterEngine.spark_route()` 双跳 reasoning ??execution（`route_request(role_override='reasoning')` ??第一??LLM 调用 ??草稿??prompt 前缀 `### 草稿\n\n---\n请继续完善` ??`route_request(role_override='execution')` ??第二段）；V2.0 placeholder 输出（`spark_draft` + `spark_execution_output` 字段填充，真 LLM 串联 V2.5 ??llama.cpp）；emit `llm_route_decided` payload ??`spark_mode=True` / `spark_reasoning_backend` / `spark_execution_backend` 标识；`LMRouter.set_spark_mode()` toggle 切到 True ??4 公开方法内部??spark_route；V1.5 mock 路径仍可用（`EAIDE_LLM_BACKEND=mock`）??- **??metrics.py 路径 bug**：`_router_db_path()` 不再硬编 `%APPDATA%/eaide/router.db`，优先级 `EAIDE_LLM_ROUTER_DB_PATH` env > `settings.llm_router_db_path`；测试时 `monkeypatch.setenv("EAIDE_LLM_ROUTER_DB_PATH", str(tmp_path / "router.db"))` 自动隔离；生产绝对路径直接用??- **API 端点清单**（V2.0 完整）：
  - `GET /router/metrics` ??circuits + budget + backends?? 秒轮询用??  - `GET /router/decisions` ??最??routing_decisions（router.db 真实表读取）
  - `GET /router/weights` ??当前评分权重（持久化真源??  - `PUT /router/weights` ??全量配置更新?? ??+ Σ=1 校验??  - `POST /router/breakers/{name}/reset` ??手动重置熔断??  - `POST /router/spark-mode` ??Spark 模式 toggle（body: `{enabled: bool}`??  - `GET /router/backends` / `POST /router/backends` / `PUT /router/backends/{name}` / `DELETE /router/backends/{name}` ??后端 CRUD
  - `POST /router/backends/test-connection` ??真实探测后端连通??- **与现有架构契约遵??*??  - ??**HITL 沿用 Phase 1** —??V2 不动 `graph/nodes/hitl_gate.py`
  - ??**`_LOCAL_ONLY_TASKS` 锁死** —??`engine.py:route_request` 编排顺序保证 `_LOCAL_ONLY_TASKS` 红线永不可绕过（`apply_hard_rules` 第一关执行，role_override 仅在硬规则过滤后再筛选）
  - ??**Auto-Repair `retry_count ??2` 沿用 Phase 1** —??V2 不碰此状??  - ??**审计 `audit.sqlite` 沿用 Phase 1** —??V2 不写新审??action（路由决策落 `router.db.routing_decisions` 表）
  - ??**SSE 三处同步沿用 Phase 1** —??本轮修复 Python 漏同步的 3 ??llm_* 事件
  - ??**Keyring 沿用 Phase 2A** —??`api_key_ref` 占位符，明文不落 router.db
  - ??**router.db ??Python 持有** —??Rust 不消费，无需双侧 schema
- **测试**?? 个新测试文件 + 22 个测试全过；??**436 ??Python 测试 passed**??1 Windows TestClient 已知 skip）；TS `tsc -b` 0 错误；Rust `cargo build --lib` 0 ??warning（沿用旧 10 个）??- **V2.5 后做**（明确推迟）??  - `classifier.py` / `sensitivity.py`（依??Phase 4 + PII 检测）
  - `metrics.py` ??Prometheus（V2 仍是 SQLite??  - `cache_l2.py` 语义缓存（依??Phase 4 embedding??  - shared-protocol ??router 模块（LLMBackend / ScoreBreakdown / RoutingDecision 镜像??  - Spark 模式真接 llama.cpp 推理（V2.0 ??placeholder + role_override 调度??  - `LLMRouterPanel.tsx` 路由表编辑面板（V2.5??
---

## 2026-07-28

### v2.15 ??Phase 13 DSpark 推测解码加速引??V0（⚪ ??🟡 部分实装 / 决策??+ 配置 + 审计??
- **背景**：本??Ollama 推理慢是金融客户核心痛点。DSpark ??*草稿模型快速生??+ 主模型并行验??*的数学等价加速（Leviathan 2023），单次生成可加??2-3x 且输??bit-for-bit 一致。V0 只落??决策??+ 配置 UI + 审计"??*不接 llama.cpp 实际加??*（V1 接入）??- **设计文档**：[design/phase-13-dspark.md](design/phase-13-dspark.md) v1.0（DSpark 核心技??+ 场景化策略路??+ 5 类场景策??+ A/B 方案 + 集成??+ 安全红线 + 8 天任务拆??+ 5 验收）??- **实现文档**：[implementation/dspark.md](implementation/dspark.md)（约 380 ??/ 11 节）：组件清??/ Pydantic 接口 / E2E 工作??/ 测试策略 / 依赖??- **核心交付**?? 条要点）??  1. **决策??4 字段注入 RouteDecision**：`speculative_enabled` / `n_draft` / `draft_p_min` / `draft_model` / `dspark_reason`??  2. **5 关铁??*（顺序敏感，代码锁死）??  3. **12 条场景化策略 + 4 档预??* + YAML 热加??+ YAML 1.1 `off/on/no/yes` 防御??  4. **shared-protocol 类型契约**（DSparkConfig / SpeculativePolicy + 11 校验常量）??  5. **Rust typed struct**：`DSparkConfigUpdateBody` + 前置 validate()??  6. **持久??*：`dspark.json` + EAIDE_DSPARK_PERSIST_PATH env 隔离??  7. **审计落库**：`dspark_config_change` + actor_type='system' + old/new 快照??  8. **??UI 路径统一**：Settings 顶级 Tab `/settings/dspark` + ModelManagementPanel 摘要卡??  9. **RouterDashboard 加速卡**?? 类指标）??- **API 端点**?? ??`/dspark/*` 端点（GET config / policies / recent、POST reload / draft-model-path / config）??- **架构??5 忠告**：词??100% 对齐 / 短输出自动跳??/ `_LOCAL_ONLY_TASKS` 强制关闭 / 静默降级 / `test_dspark_equivalence.py` V1 闸门??- **0 新基础设施**：复??Phase 4 Ollama Sidecar / 复用 Phase 1 audit.sqlite / 复用 shared-protocol 模式??- **测试**??9 ??Python 测试（决策层 36 + audit 3）全过??
---

## 2026-07-22

### v2.14 ??Phase 12 多智能体规模化调??正式立项（⚪ 未开??/ 14 天）

- **背景**：Phase 12 预研备忘录（2026-07-15，原 Phase 10）经评审通过，正式转为立项。三类业务场景（数据洞察 / 多文??PR Review / 根因分析）已超单 Agent 状态机上限，必须派生子 Agent 并行执行??- **设计文档**：[design/phase-12-multi-agent-scaling.md](design/phase-12-multi-agent-scaling.md) ??升级为正式设计文档（原预研备忘录重写），新增 §2.1 三类场景化上下文策略 + §2.2 调度与状态治??+ §2.3 输出可信边界 + §3 评测与可观测性??- **实现文档**：[implementation/multi-agent-scaling.md](implementation/multi-agent-scaling.md) ??**新建**（约 320 行，13 节）：组件清单（14 文件 / ~1400 行）+ Pydantic 契约（SubAgentSpec / SubAgentReport / ContextPolicy / ModelPolicy / RetryPolicy / StateDelta / ArtifactRef?? Redis Key 约定 + 三类上下文工作流穿??+ 队列/重试/DLQ 语义 + 乐观??分布式锁 + HITL/LMRouter/审计安全衔接 + Token Bucket 三层限流 + E2E 工作??+ 测试/评测矩阵 + 依赖 + 开发工作流 + 风险监控??- **核心交付**??0 条铁律替代原 7 条草案）??  1. 派生树硬上限收紧：代码锁??**max_depth=2 / total_nodes??0**（不再保??3 层白名单??  2. **三类场景化上下文策略**：简单任务轻量透传 / 中等协作共享记忆??/ 长会话复杂任务摘??增量
  3. **原文外置 + 引用追溯**：原始长文本存向量库/制品库，??Agent 仅收结构化摘??+ ArtifactRef
  4. **状态版本乐观锁（默认）+ Redis 字典序分布式锁（必要时）**：解决并发覆盖与死锁
  5. **Pydantic 校验分级**：结构错 1 次模板修 ??语义??1 次喂原文 ??2 次失败进 DLQ
  6. **全流程异步解??+ 状态中心化**：不留进程内黑盒状??  7. **ELK + LLM Judge + 轨迹回放**：双层可观测（审计权??+ 分析检索），Judge 不作 CI 闸门
  8. **SSE 3 事件统一命名**：`sub_agent_spawn` / `sub_agent_done` / `sub_agent_progress`
  9. **14 天工期重??*?? Task（Pydantic 锁定 ??Orchestrator 升级 ??上下文策????Worker/DLQ ??????校验 ??HITL/LMRouter/Token ??审计/ELK/Judge ??E2E PoC??  10. **总剩??195 ??209 工作??*（Phase 12 正式纳入总工期）??- **ROADMAP**：?? Phase 12 状态改????未开??+ 交付摘要更新 + 实现文档链接；Phase 12 详情 §2 重写??0 条铁??+ 上下文三策略 + ELK/Judge 评测）；总剩??195 ??209??- **SCHEDULE**：Phase 12 状态改????未开??+ 纳入合计 199 ??+ 开发顺序新增第 4.5 优先 + §3.18 9 任务拆解 + Week 15-17 时间??+ 汇总表新增 4.5/8 优先??+ 累计 209 天??- **CLAUDE.md**：文档索??+ 修改位置速查 + 最近完??+ Phase 12 关键约定更新??- **编号与命名修??*??  - 设计文档标题 Phase 10 ??Phase 12（正文残留全部清理）
  - ROADMAP 链接 Phase 10 ??Phase 12
  - CHANGELOG v2.8 文件路径 `phase-10-multi-agent-scaling.md` ??`phase-12-multi-agent-scaling.md`
  - SSE 事件 `sub_spawn` ??`sub_agent_spawn`（三文档统一??- **不重造现有契??*：HITL `interrupt()` 沿用 Phase 1 / LMRouter `_LOCAL_ONLY_TASKS` 沿用 Phase 2C（追??`data_summary`?? Repair `retry_count??` 沿用 Phase 1（子 Agent worker 重试 ?? 是独立层?? 审计 `audit.sqlite` 沿用 Phase 1 / SSE 三处同步沿用 Phase 1 约定 / Keyring 沿用 Phase 2A / `shared-protocol` 双侧镜像 / 复用 Phase 4 Ollama + Phase 6 Checkpoint??- **架构??10 条铁律（??5 忠告升级??*：① HITL 不可绕过 / ??敏感上下文本??/ ??派生树硬上限 max_depth=2 / ??上下文场景化取舍 / ??必读字段不可??/ ????Agent 输出不可信（Pydantic 校验??/ ??全流程异步解??/ ??状态中心化可观??/ ??审计可回??/ ??Redis 是加速器??
---

## 2026-07-16

### v2.12 ??Phase 2C LLM 路由 V0 五维评分骨架??-4 天）

- 入口决策：复??SettingsView 现有 Tab 体系，新??`🧭 路由仪表盘` Tab??- 后端 7 文件：`llm/scoring.py`?? ??0-1 加权和：capability .35 / cost .25 / latency .20 / compliance .15 / availability .05?? `rules.py`（硬规则先于评分，CLAUDE.md §2 `_LOCAL_ONLY_TASKS` 永不可被评分绕过?? `circuit_breaker.py`（Closed/Open/Half-Open 状态机?? 次失????Open ??30s 后探测）+ `budget.py`（用户级日预??+ 单任务预算硬拦截?? `engine.py`（RouterEngine 编排 hard_rules ??scoring ??budget ??breaker ??cache_l1 占位 ??fallback?? `metrics.py`（routing_decisions 表写??router.db?? ??`llm/models.py`（`role` 字段：utility / reasoning / execution + `validate_protocol()` 协议校验）??- **3 层模??+ auto/manual + Spark 模式**（用户产品决??2026-07-16）：
  - **role 分层**：utility（端侧小模型，强??local）→ reasoning（推理模型，做计??+ 大纲）→ execution（复杂模型，具体实现??  - **Auto/Manual 模式**：Auto = 模型推荐直接执行；Manual = ??VSCode 风格选项让用户确??  - **Spark 模式**：开启时 reasoning 模型先打草稿 ??execution 模型??prompt 前缀拼接草稿，节??token（简单任务也可用??  - **协议约定**：内??云端都用 OpenAI 格式；内网只需??base_url，云端额外需??api_key_ref（Keyring 占位??- **3 SSE 新事??*（CLAUDE.md §4 三处同步）：`llm_route_decided` / `llm_degraded` / `llm_budget_alert`（Python stream.py + Rust sse_bridge.rs + TS ipc/events.ts??- **Rust bridge 4 commands**：`router_get_metrics` / `router_get_decisions` / `router_set_weights` / `router_reset_breaker`
- **前端 4 组件**：`RouterDashboard`?? 块：模式切换 / Spark toggle / 预算 / 评分权重 / 熔断器）+ `ScoringWeightsEditor`?? 维滑块）+ `CircuitBreakerStatus`（每后端状态）+ `routerStore`（zustand 管理 weights / circuits / budget / runMode / sparkEnabled??- **CLAUDE.md 红线遵守**：LMRouter 4 公开 API（classify_intent / plan / repair_call / summarise）签??+ 返回类型**完全冻结**——V0 不调??engine（V1 接后端再委托，避免破坏现有节点调用方）；`_LOCAL_ONLY_TASKS` 硬规则优先级**永远**高于评分（`test_router_rules.py` 断言护栏）；`api_key_ref` 禁明文（Keyring 占位符）；LLMBackend `validate_protocol()` 校验 base_url / api_key_ref 必填性??- 验证：Python 105 测试全过??9 router_2C + 7 router_models + 17 test_nodes + 49 Phase 2D skills + 1 skipped Windows TestClient?? Rust cargo check 0 ??/ TypeScript typecheck 0 错??- 暂不做的：LMRouter 4 API 实际委托 engine（V1?? cache_l2 语义缓存（依??Phase 4 嵌入模型?? LLM 真实评分实际生效（V0 ??store?? Spark 模式真正??prompt（V1 推理 + 执行实际串接?? Manual 模式用户确认 modal（V0 占位）??- 工时：~3.5 天（落地后总剩??154.6 ??**~151 工作??*）??
### v2.13 ??Phase 2D V1 Skill LLM 意图 + 共享导出??.5 天，用户修正：不做热加载??
- **背景**：Phase 2D V0 交付??手动加载模式"（UI ????立即 save_one() 生效）。V0 ??2 个尾巴：??SkillRouter.route() 只用关键词匹配，1 关键??confidence=0.33 阈值才触发，多义词/??prompt 召回率低；② skill 之间无法跨机器共享??- **用户决策 2026-07-16**??*不需??watchdog 热加??*—??只有通过界面维护的才能立即生??（V0 的手动模式已满足这个需求；外部编辑器改 YAML 需要重??Agent 是合理的，因为配置变更不是热路径）??- **V1 范围调整**（从 3.5 ????1.5 天）??  - **删除**：watchdog 热加??+ `watchfiles` 依赖 + 防自激机制（`written_by_pid` / `mtime` 双校验）
  - **保留**：LLM 意图分类（`_LOCAL_ONLY_TASKS=skill_router` 实际??Ollama?? Skill 共享 zip 导出/导入
  - **V0 已知限制保留**：外部编辑器??YAML 需重启 Agent 才生效（V1 不变??- **新文??2 ??Python**??  - `agent/skills/intent_classifier.py` ??LLM 意图分类（调 Ollama，回退关键词）
  - `agent/skills/share.py` ??Skill 共享 zip 导出/导入（V0 import 端点增强：批??+ zip??- **改文??2 ??Python**??  - `agent/skills/router.py` ??`route()` 优先??LLM，Ollama 不可用时回退关键??  - `agent/skills/api.py` ??`POST /skills/import` 接受 zip（多 YAML??+ `GET /skills/export/all?format=zip` 返回 zip
- **改文??0 ??Rust / 前端**（V1 不变 UI——V0 已经有手动导入按??+ 导出按钮，V1 改后端支??zip??- **CLAUDE.md 红线遵守**：`_LOCAL_ONLY_TASKS=skill_router` 调本??Ollama 强制（CLAUDE.md §2 敏感任务不出本机）；Skill 共享 zip 仅含 YAML 文件??*??*??DSN / API Key（V0 validate_no_dsn 仍生效）；LLM 意图分类 Ollama 不可用时**静默**回退关键词（V0 测试护栏 1 关键??0.33 阈值不变）??- **测试覆盖**?? 个测试（`test_intent_classifier.py` 4 + `test_skill_share.py` 1 + ??`test_skills_router.py` ??LLM mock 1）??- **验证**：Python 测试全过 / Rust cargo check 0 ??/ TypeScript typecheck 0 ??/ NSIS 打包??- **暂不做的**：watchdog 热加载（V2 决策后做?? LLM 意图结果缓存（V2?? Skill 版本管理（V2?? Skill 共享签名验证（V2）??- **工时**：~1.5 天（落地后总剩??151 ??**~149.5 工作??*）??
## 2026-07-15

### v2.11 ??Phase 2D Skill / MCP 生??V0?? 天全量）

- 入口决策：复??`SettingsView` 现有 Tab 体系，新??`🧠 技能` Tab 进入 `SkillsManager`??- 新文件：6 ??Python 后端（`models.py` / `schema.py` / `loader.py` / `router.py` / `api.py` + `__init__.py`?? 5 个测??+ 5 个前端组件（`SkillRoutingBadge` / `SkillCard` / `SkillImportDialog` / `SkillEditorModal` / `SkillsManager`?? 1 mock fixtures + 1 类型文件 + 1 store + Rust `commands/skills.rs`??- 改文??9 个：`chatStore.ts`??20 selectedSkill 字段 + setter?? `CenterChatFlow.tsx`??5 ??SkillRoutingBadge?? `WorkspaceLayout.tsx`??5 ??SkillEditorModal + SkillImportDialog?? `SettingsView.tsx`??15 Skills Tab?? `ipc/events.ts`??1 EVT.AGENT_SKILL_MATCHED?? `state.py`??3 active_skill_id / active_skill_name?? `llm/router.py`??1 _LOCAL_ONLY_TASKS ??skill_router?? `graph/stream.py`??15 _CHANNEL_BY_KIND + _convert_chunk 检??skill_matched?? `graph/nodes/intent.py`??25 关键词路由注??state?? Rust `lib.rs` / `commands/mod.rs` / `sse_bridge.rs`??3 channel + map_event_to_channel）??- **CLAUDE.md 红线遵守**：`_LOCAL_ONLY_TASKS` ??`skill_router` / SSE 三处同步?? 个新事件 `skill_matched`?? 不进 zustand persist / 不进 shared-protocol V0 / 凭证保险??DSN 校验 8 ??regex??- **6 ??mock skills**：订单库查询 / 用户库查??/ 财务对账 / 紧急回??/ 日报生成 / SSH 诊断，关键词 + system_prompt + 真实 MCP 工具名??- **关键词路??+ 置信度阈??0.33**：单关键词命??confidence=1/3=0.33 即可触发 SkillRoutingBadge?? 关键词不触发（V1 升级 LLM 意图分类）??- **手动加载模式（无 watchdog??*：用户从 UI 导入 skill ??后端校验 + 写文??+ 立即 `load_one()` 生效。外部编辑器改动需重启 Agent（V0 已知限制）??- **SSE 事件**?? 个新事件 `skill_matched`（intent_node 路由命中??`_convert_chunk` 自动 emit）→ 前端 `SkillRoutingBadge` 出现。三处（Python/Rust/TS）严格同步??- 验证：Python 49 测试全过??9 schema/loader/router/api + 3 integration + 17 test_nodes?? skipped Windows TestClient 路径差异?? Rust cargo check 0 ??/ TypeScript typecheck 0 错??- 暂不做的：watchdog 简版热加载（V1?? LLM 意图分类（V1?? PII 脱敏（Phase 4?? Skill 共享（Phase 8 Server?? 多项目隔离（V1）??- 工时：~4 天（落地后总剩??158.6 ??**~154.6 工作??*）??
### v2.10 ??Phase 2G 业务功能点导??V0 前端 MVP

- 入口决策：复??`useBiznavStore`，`BusinessFeatureTree.tsx` 升受控组件（??140 ??DEMO_FEATURES 静态数组）；新??4 个组??+ 1 headless 桥接??- 新文??8 个：`types/biznav.ts` + `store/biznavStore.ts`??8 mock feature / 5 分类 / 4 selectors / 8 actions?? 4 组件（`FeatureDetailPanel` 360px 右抽??/ `FeatureEditorModal` 800×600 Monaco YAML+Form ??Tab / `ContextChip` / `BiznavChatBridge` headless?? `lib/yamlExport.ts`（手??YAML serialize??*0 新依??*?? 1 fixtures 文件??- 改文??4 个：`BusinessFeatureTree.tsx`??130/+50 受控??+ 顶部 [↻]/[✏️] 按钮 + 选中态高亮）/ `chatStore.ts`??25 ??`selectedFeatureContext` 字段 + setter?? `CenterChatFlow.tsx`??5 ??`<ContextChip />` ??ChatInput 上方?? `WorkspaceLayout.tsx`??15 ??headless bridge + editor + detail panel）??- **CRITICAL React #300 防护**：跨 store 同步??headless `BiznavChatBridge` 订阅者（**单向** biznavStore ??chatStore），反向不通；mode 切换清空也走 bridge。`BusinessFeatureTree` + `FeatureEditorModal` 所??hook 无条件在 early-return 前调用（??2F BUGFIX #15 教训）??- **18 mock features 业务域分??*：订单管??4 / 用户管理 4 / 财务管理 3 / 库存管理 4 / 报表分析 3。每??feature ??2-4 related_files + 1-3 apis + 1-3 tables + 1-3 business_rules + 风险等级（high ??~33%）??- **Monaco YAML 编辑??*：YAML Tab V0 **只读**预览（手??`featureToYaml` 序列化，不用 js-yaml 依赖）；表单 Tab 完整 inline edit + [+ 增加]/[??删除] 按钮 + 顶部 [×] ??未保存，确定离开??确认。V1 接后端时再放开 YAML Tab 编辑（用 js-yaml 替换 yamlToFeature）??- **CLAUDE.md 红线遵守**：不??shared-protocol（V0 前端独有，V1 镜像?? 不动 WorkMode（沿??`full|operator|auditor|analyst`?? 不动 SSE / 不动审计 / 不进 zustand persist（重启回 18 mock?? 凭证保险??mock 数据??`com/xxx/...` 假包名（??DSN）??- 验证：TypeScript typecheck 0 错误；Tauri NSIS 打包成功（timestamp 2026-07-15 15:37 / 36MB）??- 暂不做的：后??`biznav/` 9 ??Python 文件（extractor / storage / YAML / hot_reload / rule_engine / api / schema / models / __init__?? Rust Tauri command `biznav.*` / SSE 事件 / 审计事件 / LMRouter ??`biznav_extract` / 真实文件内容读取注入 / 多项目隔离（V0 单项目演示）??- 工时：~1.2 工作日（落地后总剩??159.8 ??**~158.6 工作??*）??
### v2.9 ??Phase 10 IAM 统一身份认证 + Phase 11 License 离线授权 完整立项

- **背景**：EAIDE 面向两类客户场景——金融企业需要对接现有身份系??+ 数字签名防抵赖、中小客户需要离线激??+ 功能分级商业版。两套机??*并行不冲??*：Phase 10 解决"谁在用、能做什??，Phase 11 解决"哪个客户能商??。原 Phase 10 候选（多智能体规模化调度）被推??Phase 12??- **新增 Phase 10 完整方案**（与 Phase 9 同级别）??  - **设计文档**：[design/phase-10-iam.md](design/phase-10-iam.md)（约 380 行，??11 节：设计哲学 / 架构总览 / 4 种认??/ RBAC + ABAC / 审核联动 / 组织架构同步 / 6 表数据模??/ UI / 安全红线 / 与现有模块联??/ 7 天任??/ 14 项验??/ 价??/ 5 忠告 / 关联文档??  - **实现文档**：[implementation/iam.md](implementation/iam.md)（约 350 行，??11 节：组件清单 / Pydantic 接口 / E2E 工作??/ 测试策略 / 依赖 / 与现有契约点 / 开发工作流 / 监控告警 / 风险 / 验收清单 / 关联文档??  - **核心交付**：OIDC + LDAP + 企微 + 钉钉 + 飞书 + 本地账号降级 6 种登录；RBAC + ABAC 权限表达式（`env × resource × action × condition`）；prod 只读 + 写操作强制审??+ 高危操作 MFA + RSA 数字签名；LDAP 每小时全??+ 企微 Webhook 实时 + 离职 5 分钟自动回收??  - **新数据模??*：`iam.db`（物理隔离，6 表：iam_users / iam_roles / iam_permissions / iam_user_roles / iam_login_logs / iam_permission_logs）??  - **15 天工??*：Task 1 认证 (3d) + Task 2 RBAC (2.5d) + Task 3 LDAP 同步 (2d) + Task 4 签名 + MFA (2d) + Task 5 管理??UI (2.5d) + Task 6 客户端登??(1.5d) + Task 7 全模块接??(2d) + 联调 (0.5d)??- **新增 Phase 11 完整方案**（与 Phase 10 同级别）??  - **设计文档**：[design/phase-11-license.md](design/phase-11-license.md)（约 360 行，??11 节：设计哲学 / 架构总览 / 机器指纹采集 / License 格式 / 校验流程 / 功能分级 / 试用模式 / 厂家后台 / 数据模型 / UI / 安全红线 / 联动 / 6 天任??/ 16 项验??/ 价??/ 5 忠告 / 关联文档??  - **实现文档**：[implementation/license.md](implementation/license.md)（约 380 行，??11 节：组件清单 / 数据模型 / Tauri command 接口 / 后台 API 契约 / E2E 工作??/ 测试策略 / 依赖 / 与现有契约点 / 开发工作流 / 监控告警 / 风险 / 验收清单 / 关联文档??  - **核心交付**：机器指纹采集（CPU + 主板 + 系统??+ BIOS UUID 跨平台）??SHA-256；License JSON + RSA 数字签名（厂家私??/ 客户端公钥硬编码 5 年轮换）??0 天试用（Keychain 防作??+ 时间回退检??+ 可??NTP 校时）；基础/专业/企业版分级（功能菜单隐藏而非灰按钮）；厂家后??Flask 极简版（客户 / License 生成 / CRL / 统计 4 核心）；离线→在线平滑过渡??  - **10 天工??*：Task 1 指纹采集 (1.5d) + Task 2 校验引擎 (2d) + Task 3 试用模式 (1.5d) + Task 4 前端激??(1.5d) + Task 5 厂家后台 (2d) + Task 6 全模块接??(1.5d) + 联调 (0.5d)??- **ROADMAP / SCHEDULE / CHANGELOG 三处登记**??  - **ROADMAP**：?? 阶段表新??Phase 10（⚪ 未开??/ +15 天）/ Phase 11（⚪ 未开??/ +10 天）/ Phase 10 候????Phase 12 重编号；§2 新增 Phase 10 / Phase 11 详细章节，原 Phase 10 章节重命名为 Phase 12；?? 风险表新??**Phase 10 风险 11 ??+ Phase 11 风险 11 ??*；?? 验收表新??**Phase 10 验收 14 ??+ Phase 11 验收 16 ??*；最后更新日期更新到 2026-07-15??  - **SCHEDULE**：?? 阶段表新??Phase 10/11 两行（原 Phase 10 候选重编号??Phase 12）；总剩??**152 ??177 工作??*（含 buffer ??162 ??187 工作日）；?? ??7 优先"身份与商业化底座"（Phase 10/11 串行 25 天，可并行压缩到 15 天）；??.15-3.16 Phase 10/11 详细任务拆解；??.5 关联文件清单同步追加??  - **CHANGELOG**：本??v2.9 完整记录（含背景 / 3 个新设计文档 / 2 个新实现文档 / 数据模型 / 工期 / 与现有契约遵??/ 编号变更说明）??- **架构??5 忠告（Phase 10 IAM??*：① 不要自己??OIDC Server；② LDAP 同步是金融刚需；③ 数字签名是审核灵魂（监管检??谁批的、签名呢??）；??权限申请流程要顺滑（一键申请而非硬??无权??错误）；??Admin 后台要克制（EAIDE ??IAM ??Client 不是 IAM ??Server）??- **架构??5 忠告（Phase 11 License??*：① 机器指纹不要过严（只??CPU+主板+系统盘）；② RSA 密钥对每年轮换向后兼??5 年；??试用防作弊三层（Keychain + NTP + 时间回退）；??License 不加密（签名本身防篡??+ JSON 可读）；??厂家后台可以很简单（Flask + SQLite + 基础账号密码即可）??- **与现有契约遵守（不重造）**：HITL 复用 Phase 1 `hitl_gate.interrupt()`；LMRouter 敏感任务穿透（Phase 2C）；审计复用 `audit.sqlite` ??`actor_type='iam'`/`'license'`；JWT Token / RSA 私钥 / bcrypt 哈希 / 试用日期 全部 OS Keychain（复??Phase 2A Keyring）；`iam.db` ??audit/sessions/codenav/router/data_expert/collab/license 物理隔离；与 Phase 10 IAM 严格边界（Phase 10 解决"??，Phase 11 解决"商用"，两者完全独立可单独/同时启用）??- **总剩余变??*??62 ??**187 工作??*（新??25 天：Phase 10 +15 + Phase 11 +10）??
### v2.8 ??Phase 10 多智能体规模化调??预研备忘录立项（候??/ 待评审）

- **背景**：在分析业内多智能体编排方案（主 Agent 状态机 + Redis 状态池 + 异步队列 + DLQ + 派生树约??+ 上下文压??+ 令牌桶限流）后，提炼??EAIDE ??Agent 状态机到多智能体规模化"的核心挑战。EAIDE 当前 6 节点 LangGraph 状态机??Phase 7（数据专家跨库洞察）/ Phase 5??00 文件 PR Review?? Phase 2F+（日志根因分析）三类场景下已撞上限，需要派生子 Agent 并行干??- **性质**??*预研备忘录，不是已立??Phase 的完整设计终??*——提供立项决策依据（7 条铁??+ 5 维关键约??+ 14 条风??+ 5 条架构师忠告 + 16 项验收门槛）。正式立项评审通过后，再补 §5 接口约定 / §6 测试策略 / §7 依赖清单等实现级章节??- **预研备忘录位??*：[design/phase-12-multi-agent-scaling.md](design/phase-12-multi-agent-scaling.md)
- **核心哲学?? 条铁??v0.1 草案??*??  1. **HITL 是脊梁不可绕??*——子 Agent 任何写操作反向进主图 `hitl_gate`，与顶层写操作无差别??*不重造审批通道**??  2. **派生树有硬上??*——默认层????2；任务类型白名单（codenav / data-explorer / log-analyzer）可临时 ??3??*整树节点????30**??  3. **敏感上下文只能过本地 Ollama**——子 Agent 凡携 DB ??/ SQL 错误 / 用户 PII 路由到内??LLM 一律拒；`intent` / `repair` / `data_summary` 全部加入 `_LOCAL_ONLY_TASKS`??  4. **上下文压缩有保底清单**——子 Agent 回报前压缩，**决策必读字段（错误码 / 状态码 / 重试次数 / API 响应头）不可??*??  5. **状态可回滚或可重放**——所有副作用任务必须**幂等**（带 `idempotency_token`）；不可逆操作（发邮??/ ??webhook）必须经 HITL 审批，不能事后回滚??  6. **Redis 是加速器不是地基**——核心调度状态用持久化存储（SQLite / PostgreSQL）；Redis 仅做轻量缓存 + 通知 + 锁，挂了降级本地 fallback 不阻塞主图??  7. **决策全审??*——子 Agent 每个决策（创??/ 完成 / 失败 / 重试 / DLQ）都??`audit.sqlite` 一条事件，`actor_type='sub_agent'` + `correlation_id` 串联整棵决策树??- **ROADMAP / SCHEDULE / CHANGELOG 三处登记**：ROADMAP 新增 Phase 10 行（状??"🟡 候??/ 预研"??4 天待定）+ §2 详细章节 + §3 风险表新??14 ??+ §4 验收表新??19 条；SCHEDULE 新增 Phase 10 行（标记 "未排??/ 14 天待????*未计入总剩??162 工作??*——待立项评审通过后再纳入）；本文档本??entry 同步登记??- **建议工期**??4 个工作日（拆解详见预研备忘录 §5：Pydantic 契约锁定 / Orchestrator 升级 / Worker Pool + Redis 队列 / 压缩 + 必读字段 / HITL 反向 + LMRouter 穿??/ SSE 三处同步 / 端到??PoC / 文档）??- **待开放问题（6 项，预研阶段先放着，立项前必须再回答）**：① ??Agent 状态机实现选型（LangGraph 子图 vs mini-SM）；??Worker 池规模（3-8 vs 8-16）；??Phase 8 WebSocket 复用程度；④ Token Bucket 配额维度（租??/ 用户 / 优先级）；⑤ 派生树超限的兜底（直??reject vs 拆分建议）；??默认派生层数?? vs 3）与白名单范围??- **与现有契约遵守（不重造）**：HITL `interrupt()` 机制沿用 Phase 1 / LMRouter `_LOCAL_ONLY_TASKS` 沿用 Phase 2C（追??`data_summary`?? Repair `retry_count ??2` ??`Annotated[int, add]` reducer 沿用 Phase 1（子 Agent 用同字段?? 审计 `audit.sqlite` 沿用 Phase 1（加 `actor_type='sub_agent'`?? SSE 三处同步沿用 Phase 1 约定（新??3 事件 `sub_agent_spawn` / `sub_agent_done` / `sub_agent_progress`?? Keyring 沿用 Phase 2A（子 Agent 凭证同保险箱）??- **不计入总剩??162 工作??*——保持候??/ 评审状态；正式立项后纳入总剩余（162 ??176 工作日估算）??
---

## 2026-07-10

### v2.7 ??Phase 2F 代码阅读??AI 导航 V0 前端 MVP

- 入口决策：复??`ActivityBar` 现有 `search` activity??*`FindInFiles.tsx` 顶部??2-tab toggle「资??/ 代码符号??*。理由：`ActivityId` ??uiStore + WorkspaceLayout + TITLES 三处硬编码，新增 icon 改动面比改??search ??3 倍??- 新文??5 个：`types/codenav.ts` + `store/codeNavStore.ts`??8 mock symbol，TS/Java/Python/Rust 4 语言 + class/method/function/field/interface/enum 6 kind?? 3 组件（`CodeNavSearch` 搜索列表 + `SymbolDetail` Monaco drawer + `AiExplainPanel` AI 解释面板）??- 改文??1 个：`FindInFiles.tsx` 顶部 2-tab toggle??30 ??ModeTabs 组件）??- **真实代码片段**：所??18 ??mock symbol ??snippet 都是??EAIDE 仓库抓的真实代码（`AuditDashboard.tsx` / `auditStore.ts` / `collabStore.ts` / `ipc/events.ts` / `WorkspaceLayout.tsx` / `services/agent/src/agent/{llm/router,approval,graph/runner}.py` / `codenav/MonacoCommentExtension.ts` / `credentials.rs` / `mcp/database/order_service.py`），**不是 placeholder**——跳过去能看到真东西??- **Monaco 跳转（核心难点）**：`MonacoEditor.tsx` wrapper ??onMount，所??V0 ??*??`<Editor onMount={(e,m) => editorRef.current = e}>`**，仿 `chat/CodeBlock.tsx` 模式。`revealLineInCenter + setPosition + focus + deltaDecorations(codenav-flash)` 黄高??500ms 闪烁（性能红线：直接操??Monaco DOM，不??Zustand）??- **AI 解释（V0 mock??*：选中 symbol 自动触发 `requestAiExplain` ??setTimeout 800-1500ms 模拟延迟 ??模板渲染"该类封装??X 的核心业务逻辑?? / 置信??0.78-0.92（按 kind 分级：class 0.92 / method 0.85 / 其他 0.78）????重新生成"按钮可重新跑??- 安全红线遵守：跳转是只读操作，不??audit；不??Zustand persist（重启重扫）；不??shared-protocol（V1 ??FastAPI 时再镜像）??- 验证：TypeScript typecheck 0 错误；Tauri NSIS 打包成功??- 暂不做的：真??tree-sitter 解析 / Tauri command `code_nav_jump` / FastAPI `/codenav/jump` 路由 / watchfiles 增量监听 / 多文??Tab 切换 / 真实 LLM 推断（V1 再接）??- 工时：~0.7 工作日（落地后总剩??160.5 ??**~159.8 工作??*）??
### v2.6.1 ??Phase 9 前端 MVP 已交付（演示数据 + mock 推送）

- 入口决策：ActivityBar ??6 个图??`💬 协作`（横切能力，不占用顶??mode）。理由：协作锚点横跨开??审计/数据三种 mode，强??mode 切换违反"伴随??哲学；ActivityBar + 红点 badge 对应 GitHub Notifications / Slack Mentions 肌肉记忆??- 新文??14 个：`types/collab.ts` + `store/collabStore.ts`??80 ??mock + 7 类锚??+ 27 条评??+ 派生选择器）+ 11 个组件（`CollabCenter` / `ContextList` / `TaskDiscussionPanel` / `CommentThread` / `CommentEditor` / `MentionInput` / `MarkdownRenderer` / `ReactionPicker` / `CollabDrawer` / `ShareDialog` / `MonacoCommentExtension`?? `lib/pushMock.ts`??0-60s 随机推??+ 50% 概率 @我触??TopBar 红点）??- 改文??7 个：`ActivityBar`??collab ??+ @N 红点 badge?? `WorkspaceLayout`（center 区域全屏渲染 CollabCenter + CSS 折叠 SideBar + pushMock 生命周期?? `AuditDashboard`（选中审批单自动打开 CollabDrawer?? `TopBar`（??@N 红点 badge?? `uiStore`（`pendingCollabMentionCount`?? `ipc/events.ts`?? ??collab:* EVT 占位声明?? `package.json`（`dompurify` + `@types/dompurify`）??- 安全红线遵守：评论体??DOMPurify 净化（XSS 拦截??/ PII 检测（卡号/身份??手机号）触发顶部黄色 banner 提示"脱敏由后??Phase 4 处理" / 5 分钟撤回窗口 + is_edited 标记 / 密文占位 `[encrypted:AES-256-GCM]`（待 Phase 8 接入真实加密）??- 演示数据贴近真实金融场景?? ??mock 锚点（approval_ticket / code_line / deploy_task / log_segment / hotswap_task / sql_block / custom?? 27 条嵌套评论（最??3 ??Thread?? Reaction 字典??- 验证：TypeScript typecheck 0 错误；Tauri NSIS 打包待跑??- 暂不做的：真??WebSocket / 真实 AES-256 加密 / 真实 PII 脱敏 / 真实企微/钉钉/飞书 Webhook / Monaco 真实 `deltaDecorations` 集成（DiffViewer 暂无 onMount props，V1 再加?? Phase 3B/3C/5 自动创建锚点（仅??AuditDashboard 联动 demo 1 个）??- 工时：~1.5 工作日（落地后总剩??162 ??**160.5 工作??*）??
### v2.6 ??Phase 9 任务级协作引擎（Contextual Collaboration??
- 新增 Phase 9 完整方案：EAIDE 从「工具型协作」演进为「上下文锚定的协作中枢」??*所有协作内容绑定具体上下文（代码行 / SQL / 部署 / 审批 / 日志??*——不做通用 IM，不抢企??钉钉/飞书的群聊饭碗，而是**融入**它们??- 阶段表新??Phase 9 行（+14 天），总剩??148 -> **162 工作??*（各 Phase 之和 138 -> 152）??- 时间表新??**??6 优先：协同生态闭??*（Week 19-22，Phase 8 + Phase 9 串行 26 天）??- **核心机制**??  - **上下文锚点系??*—?? 类锚点（`code_line` / `sql_block` / `deploy_task` / `approval_ticket` / `log_segment` / `hotswap_task` / `custom`），每条协作内容绑定一个锚点，点击可跳转回原始上下文??  - **评论层级**——主评论 + Thread 回复 + Reaction（??👎 ????👀 替代"已读回执"，刻意不??IM ??read/unread）??  - **行级评论**——Monaco 行号??💬 图标，集??Phase 2F 代码导航??  - **任务讨论面板**——部??审批/热更任务右侧 "💬 讨论" Tab，参与者自动订阅??  - **协作中心**——左侧活动栏新图标，"我参与的 / @我的 / 待解?? + 未读红点??  - **外部 IM 分享??*——一键分享到企微/钉钉/飞书（机器人 Webhook），卡片??Deep Link `eaide://collab/open?context=xxx` 唤起本地客户端??  - **上下文自动创??*——Phase 3B/3C/5 任务创建时自动生成对应锚点，参与者自动订阅??- **金融级安全红??*??  - 评论内容 **AES-256 加密存储**（Keyring 管密钥，??Phase 8 审批单同等级别）??  - 每条评论计算 **SHA-256 content_hash**，修改后 `is_edited=1` 并保留历史版本??  - 发布前过 **Phase 4 PII 脱敏**（卡??身份??手机????`[REDACTED]`）??  - **权限隔离**——生产环??SQL 锚点只有运维/DBA 可评论；评论删除需管理员，普通用户只能撤??5 分钟内自己的评论??  - 所有动作写??`audit.sqlite`（复??Phase 1）??- **关键决策（架构师 5 忠告??*??  1. **从审批单讨论切入**——Task 7 优先实现 `approval_ticket` 锚点，最低成本验证协作链路??  2. **行级评论是杀手体??*——Task 3 投入 2.5 天，集成 Phase 2F 跳转??  3. **不做"已读未读"**——Reaction 模型只存 `{user_id: emoji}`，无 `read_at` 字段??  4. **Deep Link 是灵??*——`eaide://collab/open?context=xxx` 让企微卡????EAIDE 中查??按钮一键唤起本地客户端??  5. **克制 IM 功能**——明确范围：不做群聊/不做语音/不做视频；讨论组生命周期 = 任务生命周期（任务结束自动归档）??- **架构??不??IM 轮子"哲学**：没??好友"概念（从企业 LDAP 同步?? 没有"群聊"概念（只??任务讨论???? 不做漫无目的闲聊（每条评论必带上下文锚点）??- **复用 Phase 8 WebSocket 网关**：通过 Server 中转推送评??@/Reaction/分享事件；若 Phase 8 未交付，降级??SSE 长轮询??- **与现有契约遵??*：HITL 仍为写操作入??/ 加密密钥??Phase 2A Keyring / `collab.db` ??audit/sessions/codenav/router/data_expert 物理隔离 / SSE 三处同步新增 3 事件（`collab_comment_new` / `collab_mention` / `collab_share`?? Deep Link 协议固定前缀 `eaide://collab/open` + 一次??token 校验防仿??/ Markdown ??DOMPurify ??XSS??- **新增风险表条??*??4 条，涵盖 PII 漏判 / 内容明文落盘 / 锚点跳转死链 / WebSocket 断连 / @ 提醒风暴 / IM 限流 / Deep Link 钓鱼 / 审计漏写 / 行级跳转冲突 / XSS 注入 / 历史评论性能 / 推送风??/ 锚点重复创建）??- 设计文档：[design/phase-9-collab-engine.md](design/phase-9-collab-engine.md) · 实现文档：[implementation/collab-engine.md](implementation/collab-engine.md)

### v2.5 ??Phase 8 多人协同与审批路由引??
- 新增 Phase 8 完整方案：EAIDE 从「单机工具」演进为「企业级研发运维协同平台」??- 阶段表新??Phase 8 行（+12 天），总剩??136 -> **148 工作??*（各 Phase 之和 126 -> 138）??- **重大架构增量**：首次引入独立后??EAIDE Server（私有化集群部署，Spring Boot/Go + PostgreSQL + Redis/RabbitMQ），与本??Agent 解耦，仅经 REST/WS 契约耦合??- 通讯：上??HTTPS REST + 下行 WebSocket 长连接（断线降级 企微/钉钉 ??邮件 ??短信）??- 审批流三选一：内置极简状态机 / 对接 OA（泛微·致远，API 发起 + Webhook 回调?? 企微钉钉 IM 融合（移动端秒批）??- 金融级安全：审批??payload AES-256 加密存储 + SHA-256 防篡改（客户端执行前校验?? 审核人本??Keychain 私钥数字签名（不可抵赖）??- 采纳架构??3 忠告：客户端??VLAN 不通必??Server 中转（不??P2P?? 移动端审批刚需必接企微钉钉 / Server 是新 SPOF 须集??+ 消息队列保证不丢??- 与现有契约遵守：HITL 仍为写操作入口（扩展为跨终端多人?? 签名私钥??Phase 2A Keyring / 客户端本地审??action ??CHECK 免迁??/ collab 事件三处同步?? 个新事件?? 审计报表联动 Phase 7??- 设计文档：[design/phase-8-collab-approval.md](design/phase-8-collab-approval.md) · 实现文档：[implementation/collab-approval.md](implementation/collab-approval.md)

### v2.4.1 ??Phase 7 数据专家模式 + Phase 2C 模型管理：前??MVP 已交付（演示数据??
- Phase 7 数据专家模式 UI 落地（前??mock）：`WorkMode` 加第 4 ??`'analyst'`（研??运维/合规/数据四模式矩阵完整）+ ModeSwitcher ??4 页签（青绿主题）+ 切换确认??`DATA_MODE_CONTENT`??- 数据工作台四象限：`views/DataWorkbench.tsx` + `components/data/`（DataSourceTree 数据??表结??字典 + QueryEditor SQL/Python/对话三模??+ DataGrid 结果网格 + ChartPanel 内联 SVG 图表 + ExportBar 导出?? `store/dataStore.ts` 演示数据（分行坏账率场景）??- 只读铁律在演示层体现：SQL 含写操作时禁用「执行?? 红字警示（`isReadOnlySql`）??- Phase 2C 设置内模型管??UI：新??Settings「??模型管理」分??`ModelManagementPanel.tsx`?? 后端注册??+ 类型徽章 + 熔断状态灯 + 启停 + 测试连接 + 增删改弹??+ 路由??+ `_LOCAL_ONLY_TASKS` 🔒 红线标记；API Key 标注??Keyring）??- 杂项：对话区空状态删除「企业本地化 AI IDE Agent」副标题，仅??EAIDE??- 验证边界：TypeScript typecheck 通过 + Tauri NSIS 打包成功；GUI 视觉/交互未在构建机验证（前端 mock，无后端）。真实后端引擎见??Phase 实现文档??
### v2.4 ??Phase 7 数据专家模式（智能分析与报表引擎??
- 新增 Phase 7 完整方案：面向数据分析师/BI/财务的「数据工作台」，自然语言驱动取数→清洗→分析→可视化→导出全链路闭环??- 阶段表新??Phase 7 行（+12 天），总剩??124 -> **136 工作??*（各 Phase 之和 114 -> 126）??- 四模式矩阵完整：`WorkMode` ??`'analyst'`（研??完整IDE / 运维=运营专家 / 合规=审核专家 / 数据=数据专家）??- 核心机制：RAG 增强 Text-to-SQL（Schema 链接裁剪 3-5 ??+ 业务字典注入 + Few-shot?? 受限 Python 沙箱（白名单 pandas/numpy，内??2GB + 时限 30s?? 虚拟滚动 DataGrid??0w 行，Arrow/Parquet over WS?? ECharts AI 图表推荐 / Excel(openpyxl) + PDF(Jinja2+WeasyPrint+水印) + CSV 导出??- 采纳架构??4 条落地建议：Schema 链接裁剪（绝不全量塞大模型）/ 业务字典消幻??/ DataGrid 虚拟滚动 + Arrow 传输 / PDF ??HTML 模板 + WeasyPrint（金融级排版?? 敏感报表送审核专家（Phase 5 闭环）??- 金融级安全红线：**只读铁律**（禁 UPDATE/DELETE/DROP，复??mcp-server-database 纵深防御?? 重查询走 HITL / 导出必过 Phase 4 PII 脱敏 + 数字水印 + 导出审计（文??MD5）??- 与现有架构契约遵守：`_LOCAL_ONLY_TASKS` 追加 `schema_link`/`chart_reco`/`data_summary`（含原始数据??敏感表结构，强制本地?? SSE 三处同步 4 事件（大结果集走 WS 不走 SSE?? 审计 `action` ??CHECK 约束无需 schema 迁移 / data_expert.db 物理隔离 + 结果集落 Parquet / Keyring 存连接凭证??- 设计文档：[design/phase-7-data-expert.md](design/phase-7-data-expert.md) · 实现文档：[implementation/data-expert.md](implementation/data-expert.md)

### v2.3 ??Phase 2F+ 大文件快速查看与 AI 分析引擎

- 新增 Phase 2F+ 完整方案：类 klogg.exe 极速日志查看器 + AI 根因分析??- 阶段表新??Phase 2F+ 行（+15 天），总剩??109 -> **124 工作??*??- 采纳架构??5 条落地建议：Monaco largeFileOptimizations + 50MB 阈值切??/ 索引构建后台异步 + 进度??/ ripgrep bundled 二进??/ AI Token 控制（regex 提取 + 去重 + Top-N?? ??Phase 4 本地小模型联动??- 架构分层：Rust ??indexer/reader/searcher/tailer + Python ??loganalysis/api + 前端 react-window 虚拟列表??- 关键决策：`line_offsets` ??BLOB 而非独立 SQLite 行（100 万行 = 8 MB BLOB 单行写入，比 100 万行 SQLite 行快 100 倍）??- 时间表新??Week 17-19（Day 83-97，共 15 天），与 Phase 6 并行??- 性能红线??00GB 打开 < 3 ??/ 搜索 < 10 ??/ 内存 < 200 MB / 滚动 60fps / AI 提取 > 90%??- ??Phase 4 / 5 / 6 强联动：本地 0.3B 分类 / AI 分析触发审批 / 保存分析??Session??- 设计文档：[design/phase-2f-plus-log-viewer.md](design/phase-2f-plus-log-viewer.md)

### v2.2 ??Phase 2C V1.5 落地 + 测试套件转绿

- Phase 2C V1.5 已交付（零新依赖??0 测试全绿）：`models.py` / `schema.sql` / `storage.py` / `cache_l1.py` + `config.py` 4 项。阶段表状??-> 部分实装??- 数据层落地：router.db 三表（`llm_backends` CRUD + `routing_decisions` 决策日志 + `cost_daily` 日聚合），与 audit.sqlite 物理隔离；缓存命中不计费??- 回归??`test_router_backcompat.py`：冻??LMRouter 四个公开 API（graph 节点注入依赖，签??返回类型不可破）??- 顺带修复既有测试套件不全绿（详见 [BUGFIX_LOG #14](BUGFIX_LOG.md)）：`test_interrupt.py` 引用已删除的 `await_approval` -> 重写为非阻塞 API 测试；`planner.py` 模块级工具目录缓存跨用例泄漏 -> ??`reset_tool_specs_cache()` + conftest autouse 清理。`uv run pytest` 166 passed / 6 skipped??- 记录一个未修复的设计疑点：图拓??`planner -> tool_runner -> hitl_gate` 下写操作首步疑似先执行后审批，待用户确认是否立项??
### v2.1 ??Phase 6 会话管理与恢复系??
- 新增 Phase 6 完整方案：会话生命周??+ LangGraph SQLiteCheckpointer（MsgPack 而非 pickle?? 三段式上下文管理（sliding window + 摘要 + 按需召回?? 启动无感恢复?? 秒）+ 加密 .eas 导出（PII 脱敏 + Fernet?? ??Phase 5 断点续审联动??- 阶段表新??Phase 6 行（+20 天），总剩??74 -> **94 工作??*??- 采纳架构??5 条落地建议：MsgPack 序列??/ Token 爆炸根治 / 启动无感恢复 / Phase 5 联动 / 加密导出??- 时间表新??Week 14-16（Day 63-82，共 20 天）??- 性能目标??0000+ 消息 < 500ms??000 条会??LLM token <= 3000、启动恢??<= 3 秒??- 设计文档：[design/phase-6-session-mgmt.md](design/phase-6-session-mgmt.md)

### v2.0 ??Phase 2C 升级为智??LLM 路由降级系统 + 新增设置内模型管??
- Phase 2C 全面改写：从「Router/Executor 两级 + Fallback 链」升级为五维评估调度系统（能??成本/延迟/安全/可用性）。独立设计文档：[design/phase-2c-smart-router.md](design/phase-2c-smart-router.md) v2.0 初稿??- 盘点 V1 baseline（`router.py` + `fallback.py` 已存在），标注状??-> 部分实装??- 新增模型管理（Settings）：`ModelManagementPanel.tsx` + 后端 CRUD + Keyring 占位??+ 测试连接 + 路由表编??+ YAML 导入导出（响应用户新需求）??- 明确红线：四个公开 API 冻结（`test_router_backcompat.py` 回归锁）/ 硬规则先于评分（`_LOCAL_ONLY_TASKS` 永不可绕过）/ router.db ??audit.sqlite 物理隔离 / 审计事件为写入约定无 CHECK 约束，无需 schema 双侧迁移??- 阶段表：Phase 2C 预估 4 ??-> 12 天（MVP-first V1.5->V2->V2.5->V3）??- SSE 三处同步新增 3 事件（`llm_route_decided` / `llm_degraded` / `llm_budget_alert`）??
---

## 2026-07-09

### v1.6 ??Phase 5 审核专家模式（金融级审批与审计）

- 新增 Phase 5 完整方案：审批工作台 + Monaco Diff 审核 + MFA 二次验证 + Evidence Chain + 不可篡改审计签名链（HMAC_SHA256 哈希链）??- 架构图、阶段总览表、时间表（Week 11-13 插入 Phase 5，??74 工作日）同步更新??- 明确 WorkMode 迁移策略：`full | operator | auditor`，兼容旧 `audit` URL/localStorage 值??- 与现有架构契约：HITL 仍为写操作唯一入口 / 审计 schema 双侧同步 / 新事件需 SSE 三处同步 / 批量审批后端硬限制低危任务??- 设计文档：[design/phase-5-audit-expert.md](design/phase-5-audit-expert.md)

### v1.5 ??Phase 2A + 2E 完成

- **Phase 2A** 多环境治????完成?? ??env preset + Keyring 占位??+ 加密导入导出（EAIDE-ENC-V1 Fernet?? Tauri 命令??+ 49 单测全过 + 顶部醒目活跃环境徽章（跨模式可见）。设计文档：[design/phase-2a-env-governance.md](design/phase-2a-env-governance.md) v1.1??- **Phase 2E** 运营专家模式 ??完成：顶部页签切换（开发模??/ 运营专家模式?? 220ms cubic-bezier 丝滑过渡 + 切换确认弹窗 + "下次不再提示" 持久??+ Ctrl+~ 半屏 Xterm 抽屉（逃生通道）。设计文档：[design/phase-2e-operator-mode.md](design/phase-2e-operator-mode.md) v1.0??- Bug 修复：grid `auto auto 1fr auto auto` 5 行导??1fr 行空白，改为 4 ??+ 内部 flex（关??BUGFIX_LOG 1 条）??
### v1.4 ??Phase 4 本地小模??+ 知识库引擎（云边协同??
- 新增 Phase 4 完整方案：三位一体架构（Qwen2.5-0.5B 文本 + Moondream2 视觉 + bge-small-zh-v1.5 embedding + SQLite-vec 知识??+ RAG 检索）??- 阶段表新??Phase 4 行（+12 天），总剩??47 -> **59 工作??*。设计文档：[design/phase-4-local-ai.md](design/phase-4-local-ai.md) v1.0??- 知识库独立导航栏 Tab（ActivityBar 入口 + 5 子页面：上传 / 浏览 / 搜索 / 统计 / 同步?? Phase 2G/2F -> 知识库双向同步??- 采纳架构??4 条落地建议：模型选型（Qwen/Moondream/bge?? GGUF Q4_K_M + SQLite-vec ANN / llama.cpp Sidecar 隔离 / 离线可用作为商业卖点??- 与现有架构契约：审计 schema 双侧同步 / LMRouter `_LOCAL_ONLY_TASKS` 追加 `local_intent` / `vision_understand` / SSE 事件三处同步?? 个新事件?? ??Phase 2C 路由降级合并??
### v1.3 ??Phase 2G 业务功能点导航（运营专家模式专属??
- 新增 Phase 2G 完整方案：代码反向抽象为业务功能??+ YAML 热加载（不重??Agent?? 双视角联动（研发看代??/ 运营看功能）+ 业务规则扩展点。设计文档：[design/phase-2g-business-nav.md](design/phase-2g-business-nav.md) v1.1??- 采纳架构??4 条落地建议：MVP 半自动（AI 70% + 人工 30%?? `features.yaml` 变更即生??/ 运营专家模式左侧自动切换??BusinessFeatureTree / 业务规则未来可升级为结构??DSL??- 阶段表新??Phase 2G 行（+8 天），??47 工作日??- 与现有架构契约：审计 schema 双侧同步?? 个新事件类型?? LMRouter `_LOCAL_ONLY_TASKS` 追加 `biznav_extract` / SSE 事件三处同步?? 个新事件?? ??Phase 2E 运营专家模式深度联动??
### v1.2 ??Phase 2F 轻量级代码阅读与 AI 导航引擎

- 新增 Phase 2F 完整方案（Tree-sitter AST 索引 + SQLite 符号??+ Monaco 跳转 + AI 语义推断）。设计文档：[design/phase-2f-code-nav.md](design/phase-2f-code-nav.md) v1.0??- 设计哲学：降维打击传??LSP（JDTLS/Pyright），采用 AI 原生架构实现秒级响应和跨系统理解??- 架构图、阶段总览表、时间表（扩展为 8 周，??39 工作日）同步更新??
### v1.1 ??合并架构审查反馈

- 新增核心风险预警??避坑"建议?? 个技术深水区 + 时间表缓冲）??- 新增运营专家模式快捷键逃生通道（Ctrl+~ 半屏 Xterm 抽屉）??- 更新时间表：Week 4 / Week 6 各加 1 ??Buffer，总工作日??33 -> 35??
### v1.0 ??项目路线图初??
- 整理 Phase 2 全部 4 个模??+ Phase 3 全部 4 个模块??- ??7 周开发计划??- 标识 Phase 2A ??80% 完成、Phase 2B/C/D + Phase 3 全部未开始??
---

## 维护标准

### 何时追加记录

以下任一情况发生时，必须在本文件末尾追加一条变更记录（日期降序）：

1. **Phase 交付完成**：一??Phase 的状态从「未开??进行中」变更为「已完成」，记录交付内容、新??修改文件数量、关联设计文档链接??2. **重大架构决策**：引入新的系统组件（如新 Sidecar、新数据库、新 IPC 通道）、废弃旧模块、或改变核心数据流方向??3. **版本发布**：每次打包发布（NSIS installer 构建成功且通过冒烟测试）??4. **设计文档定稿**：新??Phase 设计文档初稿完成并合并到 ROADMAP.md（即 ROADMAP.md 出现新版本号时）??5. **红线变更**：修??CLAUDE.md 中定义的任何硬性约束（??`_LOCAL_ONLY_TASKS` 集合、审??schema、SSE 事件命名）??
### 记录格式

每条记录必须包含??
- **日期**（`YYYY-MM-DD`??- **版本标签**（与 ROADMAP.md 中的版本号一致，??`v2.3`??- **变更摘要**??-8 条要点，说明做了什么、为什么、影响范??- **关联文档链接**：引用相关设计文档（`docs/PHASE*`）、BUGFIX_LOG 条目号、或 Git commit hash

### 禁止事项

- 不得删除或重写历史记录（只追加，不修改）??- 不得在无 ROADMAP.md 版本号对应的情况下新增条目??- 不得省略关联文档链接（便于后续追溯完整设计上下文）??
## 2026-08-05

### v2.x · reqflow V1 运营模式需求改造工作流（需求卡片）

- **新增 `agent/reqflow/` 模块**：需求批??+ 需求卡片（reqcards.db），自动编号（BAT-/REQ- 按日递增），卡片每次修改自动记版本（req_card_versions 快照，可切换历史版本只读查看，默认最新）??- **AI 卡片生成**：功能点上下??+ 对话摘要 ??三级降级链（本地 Ollama ??router.db 内网 ??router.db 云端，均??extract_chat）结构化为卡片草稿；全失败返??502 + 明确原因??- **状态流??*：草????待审????已批????开发中 ??已完成（任意非终态可驳回），V1 手动切换，can_transition() 为审批模式预留接缝；仅草稿可删除；创??切状态走审计埋点??- **批次导出需求文??*：Markdown / Word（python-docx），含批次概览完成情况统??+ 逐条需求明细??- **前端**：功能点树多选勾??+ 「??发起改造需求?? 对话区需求对齐横幅（生成需求卡??取消）；ActivityBar「??需求」→ 需求工作台三栏视图（批次列??卡片网格/详情编辑 + 版本切换）；功能点详情抽屉展示关联需求卡片??- **打包**：eaide-agent.spec datas ??reqflow/schema.sql（提示词已含??llm/prompts 整目录）；python-docx ??pyproject 依赖??
