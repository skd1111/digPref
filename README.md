# EAIDE — Enterprise AI IDE

> ## 让生产环境的每一次操作，都能用一句话完成——而且敢放心完成。
>
> **EAIDE** 把 AI Agent 装进企业内网的本地 IDE：运维、开发、业务人员无需切换工具、无需记住命令，用**自然语言**就能查询数据库、调用 API、连接 SSH、编排遗留系统。会做的直接做，要动的先审批——每一步都有审计。
>
> ### 为什么选择 EAIDE
>
> - 🔒 **数据不出域** —— 全部署在企业内网，敏感任务强制走本地大模型，凭证只进系统钥匙串，零落盘。
> - ✅ **写操作必审批** —— 改文件 / 执行命令 / 写数据，先出预览再点批准，审批闸门不可绕过。
> - 📜 **全链路可追溯** —— 每一步对话、工具调用、审批决策落审计库，哈希签名链防篡改。
>
> **一句话：生产环境操作，从“敢不敢做”变成“说一句话就做”——因为每一步都被管住了。**

---

## 1. 技术栈


| 层     | 技术                                                                 |
| ------ | -------------------------------------------------------------------- |
| 桌面端 | Tauri 2.0 (Rust) + React 18 + TypeScript + Tailwind CSS              |
| 控制层 | Python 3.12 / FastAPI / LangGraph                                    |
| 执行层 | MCP Servers（Python，stdio / HTTP）                                  |
| 协议   | `packages/shared-protocol`（TS ⇄ Python 双侧镜像）                  |
| 存储   | SQLite（audit / sessions / router 等多库物理隔离）+ sqlite-vec 向量存储 + OS Keychain 凭证 |
| 包管理 | pnpm workspaces（JS）+ uv（Python）+ Cargo（Rust）                   |

## 2. 架构分层

```
┌──────────────────────────────────────────────────────────────────┐
│  表现层  Tauri 2.0 (Rust) + React + TS + Tailwind                │
│   ┌──────────┬──────────────────────┬──────────────────────┐     │
│   │ 左:资产树 │   中:对话流+代码块   │ 右:执行过程/执行链路  │     │
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
│   - 内置工具 48 个 (进程内 <1ms, 路径沙箱 7 项校验)               │
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
| 基础架构                    | Tauri 桌面壳 / 四象限 IDE 布局 / LangGraph 状态机 / HITL 审批闸门 / SSE 事件桥 / SQLite 审计 / Agent 启动闸门（Agent(:8765) 未就绪期间整屏模糊 + 禁点禁快捷键，/health 一通自动放行；首轮超时给重试 / 重启 Agent / 查看 eaide.log / 跳过闸门四个出口，且后台每 3s 复探自动放行，BUGFIX #191）                                                                                                                                                                                                                                                                                                                                     |
| 界面缩放 / 字体渲染      | 全界面连续可调 80%–200%（默认 110%，持久化）：View 菜单 Zoom In / Zoom Out（每档 10%）/ Reset Zoom + 设置 → 高级设置滑块（5% 步进）与 − / ＋；走 WebView 原生 zoom，专家卡 / 材料行 / 页签等 px 硬编码一并放大，不需要逐处改字号；字体渲染改回系统默认（去掉 antialiased，Windows 下恢复 ClearType），小字 + 非 100% 缩放不再发虚 |
| Agent 状态徽标              | 状态栏 / 顶栏单源徽标（AgentStatusBadge）：**运行中** 绿色动态 SVG（缺口弧自转 + 核心点呼吸）/ **未活动** 灰色静态待机环（进程活着但当前无任务）/ **未就绪** 红色叉号（健康检查探不通，进程挂了），悬浮提示说明当前含义                                                                                                                                                                                                                                                                                                                                                             |
| 双框架智能体（Phase 18）    | ModeRouter 智能路由 Coding / Work 双模式 + 任务分解 + Auto-Repair 自动修复 + 工具循环预算 24 轮 + 停滞熔断（连续 3 轮零成功才终止，有进展的长链任务不受影响）+ 任务进度待办列表（update_todos 伪工具驱动，进度横幅悬浮在会话页签下方常驻：不占对话区版面，滚动/翻历史不丢失，全部完成后自动收起，BUGFIX #150；todo 事件按 runId 路由到归属页签，多会话并发不再串台/源页签关闭后丢失，BUGFIX #169）；选项式追问支持粗体选项（**A. xxx**）成卡、手打孤立编号回复自动展开为结构化确认文本防丢上下文（BUGFIX #149）；执行块人性化文案：工具名显示为中文动作短语（写文件/执行命令/创建 Office 文档…）+ 进行中/已完成/失败状态词，点击动作跳转思维链对应步骤并闪烁（BUGFIX #153）；SSE 终止信号三层防丢（done 移出 finally 安全路径 / 无图块熔断 / Rust 桥 stream_closed 兜底，BUGFIX #152）                                                                                                                                                                                                                       |
| 内置工具层                  | 48 个进程内工具（文件读写/搜索/计算器/正则/时间日期含农历/相对日期解析/shell 等）+ 文档处理工具族（file_to_markdown 文件转 Markdown / Excel 查询导出 / PDF 合并拆分 / Word 生成）+ V9 Office 工具族（OfficeCLI 引擎：office_read / office_edit / office_create / office_validate，写操作走 HITL）+ klogg 式大文件只读查看与搜索（突破 100MB 限制）+ 路径沙箱 + HITL 前置闸门                                                                                                                                                                     |
| 结构化意图识别              | Intent Router 一次分析产出改写句/细分意图/实体/追问信号/风险等级；三级快速路径：关键词 → 向量语义路由（semantic-router 模式，本地 embedding 零 LLM，示例句向量缓存持久化在 sqlite-vec）→ LLM 分析，明确信号直达路由省掉决策器调用；动态 Few-Shot 案例库向量同落 sqlite-vec（intent_memory.db）；向量路由未命中回退 LLM 时注入接近带候选的 tree_hint 消歧说明（借鉴 intent-fusion 双描述，区分查库/删库、知识库/数据库等易混意图；tree_hint 不向量化、不进指纹，受 semantic_route_hint_floor/top_k 调控）                                                                                                                                                                                                                                 |
| 向量存储基建（sqlite-vec） | 全仓向量统一落 SQLite sqlite-vec 扩展（vec0 虚拟表，`agent/vector_store.py` 单一入口），与进程内 ONNX 向量模型（bge-small-zh-v1.5）端侧闭环；六处消费点：意图 Few-Shot 案例 / 语义路由示例句 / L2 语义缓存（router.db 持久层，重启后仍可语义命中）/ 财税规则库与 NL2SQL 表向量持久缓存（重启免全量重新向量化）/ 知识库分块检索；扩展不可用全部静默回退（返空/现算），不阻塞主链路                                                                                                                                                                                                                                 |
| 智能 LLM 路由               | 五维评估调度 / 多级降级 / 熔断器 / 分层缓存（L1 精确响应 + L3 幂等工具）/ 预算控制 / 关键任务双模型并行+裁判 / 内网或云端可达时自动切换 OpenAI 原生 Function Calling 工具循环（首消息探测一次，不可用回退提示词协议，HITL 闸门不变）/ 中文响应硬兜底（responder 层语言守卫，小模型指令遵循失效时强制中文输出）/ 跨轮对话上下文连贯（BUGFIX #135：最近几轮对话原文压成简报注入 summarise 终答，跨轮追问不再「说一句忘一句」，L1 缓存 key 同步含历史简报防跨上下文误命中）/ 多厂商协议兼容适配（BUGFIX #159：OpenAI 专属参数如 json_schema、max_tokens 遭 400 拒绝时自动去参/换名降级重发，429 按 Retry-After 同后端退避重试，限流与请求被拒不消耗熔断额度） |
| 回答逐字流式输出            | 终答从「节点跑完整段下发」升级为逐 token 流式（`answer_delta` SSE 事件，三处同步）：responder 全部 summarise 路径（MAIN_AGENT 直答 / 中段闲聊 / 工具结果综合 / 中文重写 / 子 Agent 汇总）接入 Ollama NDJSON / OpenAI SSE 流式原语；`ThinkBlockFilter` 增量抑制思考块防漏；草稿气泡与终稿同 msgId 原地覆盖收敛（#142 机制）；首 delta 前失败切下一后端、已发 delta 后失败回退非流式，行为不劣化；`answer_stream_enabled` 一键回退整段下发                                                                                                                                                                                                                                                                          |
| 缓存命中率优化（Phase 17）  | L1 精确响应缓存（接入 summarise 最终答案链，重复请求直返省一次全链 LLM 调用）+ L3 幂等只读工具结果缓存（白名单 16 工具 + write_detector 双重把关，短 TTL）+ 请求规范化稳定 key + prompt 版本化（bump 自动失效缓存）+ MCP 工具稳定排序 + Ollama keep_alive（多轮 KV 复用）+`GET /router/cache-stats` 命中率统计 / `POST /router/cache-toggle` 一键回滚 + `llm_cache_stats` SSE 实时推送；红线：写操作 / 敏感任务 / 凭证一律不缓存 |
| Token 用量计量              | 状态栏/顶栏 Agent 状态徽标旁徽章实时展示上传（prompt）/ 下载（completion）速率（近 30s 滑动窗口，2s 轮询）+ 当日调用次数；鼠标悬浮弹明细卡片（当日 tokens 累计 / 调用次数 / 费用总 + 按模型明细，单价取模型管理`cost_per_1k_tokens`，本地免费模型不计费）。数据落 router.db `token_usage_daily`，跨重启保留、按日滚动；后端无 usage 字段时按字符数估算                                                                           |
| 提示词模板体系（v2.64）     | 统一六段式`.md` 模板资产（`llm/prompts/`，运行时加载、非工程人员可直接编辑）+ JSON 输出四层防御（API 参数 → Prompt 纪律 → 容错解析 → 重试自纠错）+ 模型族适配矩阵（Claude/OpenAI/开源/国产闭源）                                                                                                                                                                                                                              |
| Skill / MCP 生态            | Skill YAML + MCP JSON Schema / 关键词路由（归一化 + 中英混排核心 token 兜底，「做一个…的ppt」也能命中 office_pptx_designer）/ LLM 意图分类端侧探活门 / 热加载 / 多项目隔离；追问/修改轮 skill 粘性继承（上轮命中的技能随对话透传，防「太丑了重做」裸生成）；首次启动自动播种内置种子（Office 生成规范四件套：office_doc_writer / office_excel_analyst / office_pptx_designer / visual_deck_designer，蒸馏自 MIT/Apache-2.0 开源 skill 与开源设计体系美学思想自研，用户可修改覆盖、删除不复活）；输入框 `/` 系统指令（V1：Skill）——键入 / 浮出已启用技能列表（鼠标/↑↓+Enter 双通道），选中后发送时自动注入该技能的提示词经验块；Skill 强钉互斥：手动钉住的技能优先级最高，后端短路自动路由器（省一次分类调用）、前端剔除功能点绑定技能段，低优先级命中物理不进入模型输入空间（本地小模型/云端大模型同策略，免多段规则互掐 + 省 token）                                                                                                                                                                                                                                                                          |
| 任务级工作空间与产物治理     | 统一工作空间（默认安装目录/workspace，设置页可自定义）+ 任务级文件夹（一个聊天页签 = 一个任务文件夹，运行期产生的所有文件都落在里面）；创建类工具（含 office_create）输出路径调度入口统一改写，绝对路径豁免收紧为「必须出现在用户对话原文」防模型自造路径散落用户目录；交付后弹验收清理卡〔清理中间文件（保留产物）/全部保留〕，只删任务目录内非产物文件；对话中的文件路径可点击直接用默认程序打开、右键在资源管理器定位/复制/编辑器打开（带空格目录名的路径也能完整识别，执行步骤行内路径同享，BUGFIX #170）                                                                                                                                                                                                                                                |
| 专家团资产                  | 团+成员两级结构化/ 设置页独立维护（CRUD + 导入导出）/ 运营工作台点业务自动选择（功能点预设 → 静态模块默认 → LLM 本地→内网→云端降级 → 关键词）+ 输入栏手选 + 会话自动注入；选团状态跨模式切换持久化（store 记录业务锚点，切回同业务直接复用不重跑推荐，在途推荐竞态校验防错配）；Skill 预设默认专家团/材料/交付物三字段；资产包格式 zip = 根目录`team.yaml`（提示词/成员定义）+ `templates/`（交付物文档模板 docx/md），导入一次到位、导出整团打包；内置种子：12 个业务域专家团（业务办理/资料预审/智能填单/授权复核/风险预警/反洗钱/对公账户/贷后/客户经营/客户服务/差错整改/知识培训，`agent/expert_teams/seeds`，启动自动播种、同名不覆盖、删除不复活、设置页可一键恢复）+ 尽调资产包 `docs/expert-team-seeds/due-diligence-team.zip`                                          |
| 专家验收工作流（运营模式）  | 取代传统大 Chat：按专家上传材料（拖拽 / Ctrl+V 粘贴入件）→ AI 审核验收（结论+关键要素+证据链，幻觉证据丢弃，低置信标红）→ 专家迷你提问（人设 + 上传知识库 RAG 制度出处引用）→ 交付标准逐项确认 → 交付物 zip 打包导出（交付文件/检查结果/问答记录/业务小结/报告初稿 docx 模板占位符渲染）+ 多文档交叉比对防呆 + 人工改判纠错样本闭环（全操作审计）；专家录入表单（交付草稿）支持**上传文件 OCR 自动填入**：图片端侧 RapidOCR / 文档解析（PDF 扫描件自动 OCR 回退）→ 模型按模板抽字段自动落表，字段标「OCR 填入」、可撤销并回看识别原文；**专家验收前先过一遍系统合规审核**（与审核专家·文档审核同一套分类+分块风险分析+证据定位）：有风险点自动弹审核报告（风险描述 + 原文证据 + 处理建议 + 知识库依据），无风险不弹窗；材料行只给「结论 + 结果 + 两个入口」：审核意见直接显示不用展开，风险改为独立按钮「⚠ 合规风险 N」，步骤链/关键要素/证据链/人工干预收进「查看审核明细」                                                                            |
| 多环境治理                  | 4 env preset / Keyring 占位符 / PBKDF2+Fernet 加密导入导出 / 环境徽章                                                                                                                                                                                                                                                                                                                                                            |
| 代码导航                    | Tree-sitter AST 索引 + SQLite 符号库 + Monaco 跳转 + AI 语义推断 + 实时语法错误显示（tree-sitter ERROR/MISSING 节点 → Monaco marker，纯语法级不做语义分析）+ 文件树深层展开定位 + 编辑器选区代码自动附加进 prompt（防幻觉跳转）                                                                                                                                                                                                                                                                                                                                                                 |
| 业务功能点导航              | 代码→业务功能点抽象 + YAML 热加载（运营模式业务列表数据源）+ Open Folder 同步触发 AI 功能点提取                                                                                                                                                                                                                                                                                                                                                                     |
| 需求改造工作流              | 功能点发起改造需求 + AI 可行性对齐 + 需求卡片（批次/编号/改造点/影响面/外部系统） + 版本快照 + 按批次导出 MD/Word                                                                                                                                                                                                                                                                                                                |
| 运营模式（独立页签）        | 顶部顶级页签与开发模式并列：运营工作台二栏（左业务列表 16 模块导航 + 中专家验收工作流 ExpertWorkflowPanel：专家团页签 + 横向专家卡拟人化审核，新结果未读徽标）；功能点以 Skill 承载、选中业务自动注入 Skill 与专家团；交付草稿区默认收起为提醒栏（启动不自动铺屏，新草稿到达/手动点开才展开），多草稿以页签切换、直开草稿自动侧边拉开右侧抽屉（不再整屏铺开；全屏仅由草稿卡「⛶ 全屏」手动触发，离开再回来不重复弹出，BUGFIX #195/#208/#210）；材料审核带墙钟上限与僵尸状态自愈（BUGFIX #202：超时/中断一律落回「待审核」，Agent 重启后自动自愈，审核中也可手动「↻ 重审」）；系统合规审核报告的「知识库依据」文件名与「原文证据」均可点开原文核对（BUGFIX #176）；导出自动生成可审计业务记录卡片；专家卡提问框收起⇄展开带高度+淡入过渡动画（ask-dock，不再瞬间跳变）                                                                                                                                                                             |
| 右键编译（本地轻量）        | 文件树右键编译 .java（javac，Maven 多模块项目级 classpath 汇总）/ .py（py_compile）/ .c/.cpp（gcc/g++）；错误输出按系统代码页解码（UTF-8 → GBK 回落）；设置页「编译配置」面板（编译器目录手选/自动探测 PATH + 产物输出目录，compile.json 持久化，Agent 离线也能编译）；用户显式触发的 UI 命令，不走 HITL                                                                                                                          |
| 数据字典（公共参数）        | ActivityBar 独立入口（📖，横切全屏树形视图，自动隐藏会话侧栏与右侧控制台，铺平维护）：左分类树（分组可折叠 + 搜索实时过滤）+ 右详情/编辑表单；Skill 里写引用 key、字典维护参数值；CRUD / seed 内置条目可显式覆盖（dict.db）；内置分类体系随产品定义补齐（知识库分类 9 类：财务/财税·法务/合同·合规/制度·数据安全·人力/组织·运营/业务·风险/信贷·营销/品牌·技术/研发）                                                                                             |
| 业务汇总（业务办理台账）    | ActivityBar 独立入口（📊，横切全屏查询页，自动隐藏会话侧栏与右侧控制台）：**业务发起即登记**（客户经理上传第一份材料时建档，一条业务一行）；四态流转 —— 🟦 正在办理 / 🟨 挂起（人工点「⏸ 挂起」→ 当前交易界面只读冻结，可去办另一个业务，回来接着办）/ 🟧 人工确认（系统自动：材料被打回或审核异常，待人工接入）/ ⬜ 结束（导出交付物后自动）；多维查询：业务大类、小类、状态（计数条点一下即筛选，可多选）、发起日期区间、关键词；点行展开明细（待处理项 / 材料进度 / 关联业务小结 / **凭证齐备度与影像回看**） |
| 凭证预览与影像要素定位    | 运营模式办理界面页头独立按钮「🪪 凭证预览 N/M」（缺张时红字「缺N」）：凭证节点由**专家团预设**（`member.credentials`：名称/必传/介质/上传要求/归类关键词/期望采集要素，6 个影像密集型内置团开箱预置 33 个节点；存量同名团启动时只**补写缺失的凭证清单**，不覆盖用户改过的）；面板左侧按专家列出「该传哪几张、实传了哪几张、还缺哪几张」，右侧影像工作区支持滚轮缩放/拖拽平移/适应窗口，**点已录入的要素名即自动放大并闪烁描边高亮图上对应区域**（定位数据 = 端侧 RapidOCR 逐行四点框落库，全半角与 OCR 混淆字符归一后匹配，值被切成同行多块时取并集）；要素区分来源【AI 提取 / OCR 填入 / 手动录入】，低置信标红，定位不到时给实话而不是乱框（未识别影像可用「🔍 立即识别影像」补跑）；材料上传后**后台自动识别+归类**（关键词→LLM 白名单，不阻上传、不花多余 LLM），未归属材料可**拖到凭证节点**上挂载（跨专家拖拽被拒，不改审核人），也可一键「🪄 智能归类」；OCR 自动填入的原图登记为**识别底图**（独立表，不进专家审核链路，避开二次审核噪声），保证“填过的字段能回查到来源影像” |
| 侧边栏（ActivityBar）      | 除设置页穿透外优先级第一：任何模式下点击侧栏图标均出对应视图（数据字典/需求工作台/协作全屏接管，会话/搜索展开左侧栏）；**资源管理器（系统资产 / 文件列表 / 系统功能点）只在开发模式展示**（`devOnly` 标志，专家模式入口不渲染、外层左栏恒折叠，左栏由运营/审核/数据工作台自带）；占位装饰项（审计版本/运行调试/扩展/代码符号）已隐藏（`hidden` 标志，可恢复）                                                                                                                                           |
| AI 状态指示（全局）        | 智能体运行/思考指示统一简洁黑色（2026-11-09 用户要求去科技绿）：顶栏/状态栏「运行中」徽标（弧自转+呼吸）、chat 与专家卡「思考中」胶囊（弧光 orb+均衡器能量柱）均为黑灰系；业务语义色（验收通过/成功）不受影响                                                                     |
| 选项式追问与高级设置        | AI 需确认时输出可点选选项卡片（3-5 选项/理由/推荐项/多问题页签/自定义输入），自由文本选项枚举（选项X / o1/o2/o3 编号）在终答出口确定性补选项卡；「任一/多选」类引导语与多选题（复选框连选、「、」连接回发）同样成卡，围栏 JSON 损坏也不裸露原始 JSON（BUGFIX #149）；多维参数确认式追问（「请直接回复例如：'…'，缺省按 '…' 执行」）确定性抽默认组合出「按默认执行（推荐）/ 自定义」二元确认卡（#168）；推理模式与会话自主性迁入设置-高级设置                                                                                                                                                                                                                                                                                                               |
| 大文件日志查看              | Rust 字节偏移索引 + 流式读取 + 进程内搜索 + GBK 编码 + AI 日志分析                                                                                                                                                                                                                                                                                                                                                               |
| 会话管理（V1.6）            | 会话生命周期 / Checkpoint / FTS5 全文搜索 / 分支 / 共享 / .eas 加密导出导入 / 启动中断会话恢复 / 事件哈希链校验 / 详情五 Tab 中央面板内联视图（#94）；多会话并发执行（#158：多个页签可同时各跑一个任务，模型选择按 run 作用域隔离、流事件按 runId→页签路由不串台、思考指示器/停止按钮/产物汇总全部按页签归属）                                                                                                                                                                                                                                                                                                |
| MACC 上下文压缩             | 三层自适应压缩（工作记忆 / 情景记忆事件图谱 / 语义规则蒸馏）                                                                                                                                                                                                                                                                                                                                                                     |
| 多智能体调度                | Orchestrator 派生子 Agent / Worker Pool / DLQ / 限流 / 派生树硬上限                                                                                                                                                                                                                                                                                                                                                              |
| 数据专家模式（V1.1 补齐版） | NL2SQL（向量 Schema 链接选 3-5 表，本地 embedding 不可达退化关键字 + 历史已确认 SQL few-shot 飞轮 + 生成后 sqlglot 校验/问题回喂自纠错重试 1 次 + SELECT 白名单前置校验）+ 业务字典 YAML 外置（config/biz_dict：_global 全局 + 源级术语→编码映射，运营改口径不碰代码，mtime 缓存改文件即时生效，缺失退化内置默认）+ ReadOnlyPool 真实执行（6 方言族）+ 真实 Schema 同步（无 schema 数据源首拉自动补同步）+ 重查询 HITL 确认 + WS/Arrow 大结果流（Rust 中继）+ 虚拟滚动 DataGrid + ECharts + 沙箱 Python 清洗（SQL 结果 df 链路）+ Excel/PDF/CSV 导出（服务端取数 + PII 脱敏 + 水印，支持自选输出路径）+ 定时报表调度 + 历史分析（同一 SQL 去重只留最近一次）                                                                                                                                 |
| 执行过程可视化（V1，界面名「执行过程」，原思维链） | 中文执行过程时间线 + 文件操作追踪 + hover Diff 预览 + 任务结束改动文件汇总（changed_files 卡片，write/edit 成功事件累积）+ 新增条目自动滚到底（贴底跟随：意图由滚动事件记录，批量刷新一次增高超屏也不断随，上滚回看历史不打断，BUGFIX #151）+ 执行块点动作可定位并闪烁执行过程对应步骤（#153，2026-08-27 升级：连续执行步骤树形合并为一棵可折叠「执行过程」节点，摘要含步数/完成度/总耗时，进行中自动展开、完成自动收起；点子项按 occurrence 精确定位右侧同名多次调用不串，#162）；思考指示器阶段化（等模型返回 / 工具调用中：某动作）+ 每步做完人性化回执（#156）+ 执行归属页签化（其他会话不再假显思考态）；同类工具调用降噪：连续同名工具行折叠为一行 + ×N 徽标（聚合状态：任一在跑转圈/任一失败标红，摘要仍按真实步数统计，#168）。**V2（Claude Code 式细粒度，v2.116）**：新增 4 类过程事件（run_started / tool_progress / shell_chunk / file_write_preview，协议三处同步）；shell 命令边跑边流式输出（工具卡内嵌终端质感输出面板 + 退出码徽标，白名单/超时强杀语义不变）；长耗时工具阶段文案实时进工具卡副标题；写类工具在 HITL 审批前先下发写前 unified diff（只读不落盘），预览卡内嵌 +/- 统计 + 复用 FullDiffModal 红绿对比，看清「将改什么」再批准；协作式取消（`POST /chat/{run_id}/cancel` 旗标，流循环/工具循环边界短路，收尾 done 带 cancelled）；Rust 本地执行器双形态：桌面壳内 `builtin_*` Tauri command，独立部署自动拉起 `eaide-executor`（stdio JSON-RPC，同一份 path_sandbox 沙箱实现，二进制缺失降级 Python 原生兜底） |
| 文档风险审核                | PDF/DOCX/TXT/MD 解析 → LLM 分类（合同/制度/公告/标书 × 合规/法律/数据安全/资金）→ 分块风险分析 → 风险位置正文高亮 + 知识库/案例库引用依据（审核专家模式文档审核 Tab）；审核结果可直接跳到**原版式**原文：OfficeCLI 渲染的 docx/xlsx/pptx 版式预览中自动滑到风险所在列/段落并高亮（表格单元格优先匹配、所在行淡背景、纵向+横向均滚到视口）；用户上传参考资料经本地知识库混合检索（FTS5 BM25 + sqlite-vec 向量 + RRF + ONNX reranker，父子分块回喂）注入 `{{rag_context}}` 槽位，带编号引用与禁止编造指令，依据按风险类型软优先召回同类+未分类素材（命中不足放宽到全库、零漏召回）；上传参考资料支持一份多版本（更新即新增版本并自动生效、旧版完整留存可一键切回，检索仅用当前「生效」版本，切换 O(1) 无需重建索引；分类可在列表内直接修改、作用于全部版本；列表支持逐条勾选 + 表头全选，批量删除一次清理多份资料（语义同单条删除，连同各自全部版本，失败项汇总提示）；导入文件先出待导入清单（文件名 / 分类 / 导入时间）供确认，分类在预览阶段即由模型自动判定（可逐条改分类或移除，确认时直接带入该分类，入库不再重复判定；模型限流/不可用时按文件名启发式兜底，不会留空），确认后才分块 + 向量化入库，列表展示导入时间；入库并发改为两级分工：文档级闸门（默认 8，设置页「检索参数」可调 1~16）控解析/向量化并行度，云端 LLM 调用级闸门（默认 8，可调 1~32）控同时在飞的模型请求数、避免 429；父块级增强（上下文前缀/实体抽取）并行执行，单文档不再串行等 N 次云端往返，批量导入不会退化成排队长龙；列表区分「排队中 / 索引中」，进程重启后残留的未完成记录自动置为失败可重入                                                                                                                                                                                                                                                        |
| 自进化闭环（Phase 19）       | 三层自进化（均 best-effort 不阻塞主链路）：L1 经验学习（失败轨迹/用户 👎 → 本地 `reflection` 提炼教训 → 经 `extra_rules` 注入后续任务）；L2 技能蒸馏（同签名多次成功 → 规则类 Skill 草稿，强制 `enabled: false`，approve 是唯一启用入口）；L3 Few-shot 影子优化（新旧版影子回放 + Judge 打分，增益达阈才产候选版本，人工采纳/一键回滚）+ 主对话确定性抽样 Judge + 终答 👍👎 反馈；独立 `evolution.db` 六表，轨迹只存摘要（落库前 `scrub_dsn` 脱敏）；四个进化任务均入 `_LOCAL_ONLY_TASKS`；设置页经验库看板 + 技能页待审草稿 Tab + 实验面板                                                                                                                                                                                                                                                        |
| Office 文档能力（V9）       | OfficeCLI 单二进制引擎（无需安装 Office，随安装包内置，缺失时优雅降级）：docx/xlsx/pptx 细粒度创建/编辑/模板 {{key}} 填充/校验自愈（写操作强制 HITL）+ 文件树右键「📄 Office 预览」（内置渲染引擎 → HTML 沙箱 iframe / 逐页 PNG）与「🎨 HTML 演示预览」（视觉演示稿直读展示）；生成质量四层防线：内置生成规范种子（知识层，含咨询商务/杂志/暗色大气三套设计系统，默认跨国集团商务腔调：Action Title/金字塔/执行摘要，简约大气禁花哨）→ 模板优先（引擎层）→ office_validate 自愈闭环（校验层）→ 预览+HITL（人工层）；file_to_markdown 复杂排版失败时可选 Docling 兜底（`agent[parse-full]`）；二进制拉取：`infra/scripts/fetch-officecli.ps1`（SHA256 校验）                                                                                                                                                                                                                                                        |

### 🟡 部分实现


| 模块                     | 现状                                                                                                         | 未实现部分                                                      |
| ------------------------ | ------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------- |
| 本地小模型 + 知识库      | **本地混合检索 RAG 已落地**（审核专家 + 聊天共用）：SQLite FTS5 原生 BM25（jieba 分词）+ sqlite-vec 向量余弦 + RRF 融合 + ONNX cross-encoder reranker 重排；父子两层分块（small-to-big）+ 标题上下文前缀；文档级 CRUD + 上传入库（复制源文件/解析/分块/向量化/状态机）+ 前端知识库管理面板（审核页，支持多选批量删除；导入先预览确认再入库向量化）+ 设置页 RAG 参数（`/settings/knowledge`，存 kb.db `kb_config` 表、查询期参数保存即热生效、索引期参数一键重建）；数据落 `data_root()/knowledge/`（kb.db 含参数表 + files/ 源文件 + 向量同库），复制即迁移、换机即用，模型漂移基于库内原文自愈重建；大模型知识库验证总开关（`rag_llm_enhance_enabled`，默认关 → 审核/聊天只走本地混合检索、零大模型调用）；知识库依据/已上传文件点击预览（md/txt/html/office 分流 + 应用内 PDF 内嵌；md 顶部 frontmatter 渲染为元数据卡 —— 字段不再挤成一行、分类/标签成胶囊，本地文档不展示外链字段，BUGFIX #201）；embedding/FTS5/reranker 任一不可用均 best-effort 降级；**聊天召回真正接入作答（BUGFIX #192）**：`rag_retrieve` 召回经 `system_prompt_addon` → `summarise` 全链 `rag_context` 注入终答 prompt（此前只写 state、无读取方，模型看不到召回），文档/制度类查询且 BM25 命中相关资料时 `decompose` 走 RAG 优先据库直接作答（`rag_first_answer_enabled`），追问轮改用 `rewritten_query` 检索并拼上一轮主题；`semantic_route` 新增 `kb_lookup` 路由让「知识库里没有吗」类确认追问零 LLM 直出 `need_tool=False`；工具循环新增只读文件探测发散刹车（`tool_loop_fs_probe_soft/hard_limit`）+ 单 run 墙钟超时（`tool_loop_wall_clock_sec`），防跨盘 shell 扫描卡死 | LLM 增强检索阶段（HyDE / 查询扩展 / LLM 上下文前缀，受 `rag_llm_enhance_enabled` 总开关 + 各子开关控制，默认关；启用时走「模型管理」已启用模型）、聊天引用卡前端渲染（refs 已透出 trace）                        |
| 审核专家模式（金融审计） | V0 后端骨架（审批队列 + 签名链 + 合规规则）+ 文档审核子功能已交付（风险高亮 + 知识库引用）                   | 前端 AuditDashboard 完整工作台、Monaco Diff 审核、MFA、双人复核 |
| 本地图像处理             | V0 后端骨架（6 端点 + 任务表）+ OCR 真实后端（RapidOCR onnxruntime 端侧 PP-OCRv4，纯本地不出域；扫描件 PDF 经 pypdfium2 栅格化后识别，doc_review 仅无文本层时回退，chat 可经意图/工具触发）                                                                               | ONNX 超分 / 倾斜校正真实集成、前端 UI               |
| 前端实时预览             | V0.1（Vite 管理器 + 独立预览窗口 + 设备模式）                                                                | 真实 Vite 端到端 HMR 实测收尾                                   |
| 大文件日志 V1.5          | 核心读写搜索已交付                                                                                           | tail -f 实时监控、ripgrep 集成、AI 分析 UI                      |
| 编译打包引擎（3A）       | 本地右键轻量编译已交付（javac/py_compile/gcc + 编译配置面板 + 项目级 classpath）                                    | 完整工具链探测 + 异步构建流水线 + SSE 实时日志                  |
| DSpark 推测解码             | V0 骨架：DSparkConfig + 策略 yaml（config/llm/speculative.yaml）+ engine.spark_route 路由占位 + 场景化策略路由配置                                    | 草稿模型下载、llama.cpp / Ollama 真实推测推理串联（V2.5 规划）；当前 spark 模式 classify/plan/summarise 返回占位结果 |

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
| HITL 强制审批（写操作不可绕过）      | `services/agent/src/agent/graph/nodes/hitl_gate.py` + 前端 ApprovalCard（卡面只留操作概要，参数留痕在执行过程/审计）；「此后都按此执行」：会话级同类操作（同工具）免审批直接执行，仅限非双重确认风险级且硬阻断（DROP/TRUNCATE）永不可豁免，全程审计；决策提交后卡片即时改写结果文案不再卡「提交中」                                    |
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
│   │       ├── knowledge/     # 本地知识库混合检索（FTS5 BM25 + sqlite-vec 向量 + RRF + reranker，审核专家/聊天共用）
│   │       ├── vector_store.py # sqlite-vec 统一向量访问层（全仓向量存储单一入口）
│   │       ├── builtin/       # 内置工具层（48 工具：文件/shell/文档处理/大文件查看等）
│   │       ├── dataexpert/    # 数据专家模式（NL2SQL + 只读池 + 沙箱 + 导出 + WS/Arrow 流）
│   │       ├── trace/         # 思维链收集与文件操作追踪
│   │       ├── ops/ expert_teams/ # 运营验收工作流（case 存储/交叉比对/报告模板）与专家团资产包（含 seeds 内置种子）
│   │       ├── evolution/     # Phase 19 自进化闭环（签名/轨迹/反思/蒸馏/Judge/Prompt 影子优化，独立 evolution.db）
│   │       ├── …              # codenav（索引/语法检查）/biznav/reqflow/skills/preview/doc_review（依据走上传 RAG）/audit/safety 等
│   └── mcp-servers/           # 【执行层】MCP Server 矩阵（database/rest/ssh/rpa）
├── packages/shared-protocol/  # 跨语言协议包（TS + Python 唯一事实来源）
├── config/driver/             # 离线数据库驱动 wheel（不入库，构建时本地放置）
├── config/biz_dict/           # NL2SQL 业务字典 YAML（_global 全局 + {source_id} 源级术语→编码映射，改文件即时生效）
├── model/                     # 内置 ONNX 模型资产（bge-small-zh 向量 + bge-reranker 重排，slim 构建可跳过）
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

# Windows 精简包：不把 ONNX 模型（model/ 约 306MB）打包进安装包
build-all.bat slim
```

> 🧪 **Windows 构建期临时目录**：`build-all.bat` / `build-with-msvc.bat` 会把 `TMP`/`TEMP` 重定向到 `D:\RustTools\tmp`，使 rustc（MSVC link）与 PyInstaller 的中间产物不再占用 C 盘 `%TEMP%`。
>
> 📍 **Rust 工具链迁移**：`build-all.bat` / `build-with-msvc.bat` 优先读取 `CARGO_HOME` 环境变量定位 cargo（未设置时回退 `%USERPROFILE%\.cargo\bin`）。若要把工具链本体（`CARGO_HOME` ≈ 1.1GB、`RUSTUP_HOME` ≈ 1.6GB）从 C 盘迁到 D 盘，只需设 `CARGO_HOME`/`RUSTUP_HOME` 用户环境变量，**无需再改脚本**。Rust 编译产物（`target/`）本就在仓库目录内（D 盘）。
>
> ℹ️ `build-all.bat` / `build-with-msvc.bat` 为本机专用脚本，已在 `.gitignore` 中，不入库。
>
> 🧩 **Rust dev profile 瘦身**：`src-tauri/Cargo.toml` 已配置 `[profile.dev] debug = 1`（line-tables-only，断点/单步照常可用，watch 变量受限）+ `incremental = false`（增量 session 目录只增不删，历史上堆积 33GB；实测增量仅省 23s，不值）+ `[profile.dev.package."*"] debug = 0`（第三方依赖不生成调试信息，栈回溯依赖帧仍可见）。实测 `target/debug/deps` 从 **15.0GB → 1.9GB**（`.pdb` 从单个 156MB 降到个位数 MB），`target/debug` 整体从 **55.6GB → 4.4GB** 且此后不再增长。代价：改 Rust 代码的编译耗时 11s → 34s（本项目 Rust 仅是 Tauri 壳，核心业务在 Python/TS）。详见 [CLAUDE.md](CLAUDE.md) 「Rust target 目录体积控制」。

> ⚠️ 数据库离线驱动（`config/driver/*.whl`，约 30MB）**不在仓库中**。完整构建前需自行将 wheel 文件放到 `config/driver/` 目录。
>
> 📚 内置 `knowledge-base/` 已于 2026-09-04 **彻底移除**（不再打包、仓库目录删除）：文档审核「依据」与 ops 制度问答均已改走用户上传的本地 RAG 知识库（kb.db + files/，随数据根迁移、换机即用）；内置 grep 知识库、财税规则库与 audit_doc 演示脚本一并下线。
>
> 📖 NL2SQL 业务字典种子 `config/biz_dict/` 同样随包分发（同策略：工作目录优先，缺失回退内置副本）；运营在工作目录放同名目录即可覆盖包内版本。
>
> 🧪 演示数据种子：`infra/scripts/seed_demo_data.py` 按后端真实存储层灌入业务汇总 5 条台账 + 需求卡片一批 6 卡（幂等、自动探测数据根，支持 `--project` / `--dry-run`），供空库环境体验筛选 / 明细 / 状态流转 / 版本历史。
>
> 📦 内置 ONNX 模型（`model/bge-small-zh-v1.5-onnx` 向量 ~23MB + `model/bge-reranker-base-onnx` 重排 ~283MB）默认打进安装包。用 `build-all.bat slim`（或 `EAIDE_SLIM_BUILD=1`）可跳过：安装包小约 306MB，运行时重排静默 no-op（保持 RRF 融合序）、语义意图路由回退 LLM 分析、向量检索退化为关键词。需要本地模型时把 `model/` 目录放到安装目录即可（Agent 以安装目录为 cwd 解析）。

## 7. 开发约定

- **Python**：`ruff` + `mypy --strict`；snake_case；行宽 100；pytest（`asyncio_mode = "auto"`）
- **TypeScript/React**：`prettier` + `eslint`（零告警）；camelCase；vitest
- **Rust**：`rustfmt` + `cargo clippy --all-targets -- -D warnings`；行宽 120；模块测试写在 `tests.rs`
- **分支模型**：主干开发，`feat/*` 短分支 → PR 合入 `main`（2 reviewer）
- **协议同步**：线协议类型改动必须同时更新 `shared-protocol` 的 TS 与 Python 两侧
- **SSE 事件**：新增事件名必须在 `graph/stream.py`、`stream/sse_bridge.rs`、`ipc/events.ts` 三处同步
- **前端热替换**：前端已改由磁盘 `web/` + `eaide` 自定义协议加载（`web_loader.rs`）；改前端只需 `pnpm build` 后覆盖 `<安装根>/versions/web/<ver>/` 并写 `versions/web-active.txt`（回滚写 `factory`），**不必重编 Rust**。实现与验收见 `docs/ops/incremental-update-runbook.md` §6
- **提示词模板**：LLM 提示词资产统一放在 `services/agent/src/agent/llm/prompts/*.md`（六段式结构：角色/任务/输入/输出格式/硬性约束/示例），占位符用 `{{KEY}}`；JSON 输出统一走四层防御（`agent.llm.json_discipline`）。索引与接入规范见 `docs/prompt-templates.md`、`docs/llm-prompt-sop.md`
- **Prompt 版本化**：修改 `llm/prompts/*.md` 后必须 `bump_prompt_version()` bump 版本（自动失效 L1 缓存，防旧答案误命中）；缓存相关约束见 `docs/design/phase-17-cache-hit-rate.md`
- **文档台账**：交付 Phase 后更新 `docs/CHANGELOG_2.md`（当前台账，自 v2.161 起；旧册 `docs/CHANGELOG.md` 已封存不再更新）；每次修 bug 在 `docs/BUGFIX_LOG_2.md` 追加记录（自 #163 起；旧册 `docs/BUGFIX_LOG.md` 已封存）；并同步本 README 的「功能实现状态 / 安全红线 / 目录结构」

## 8. License

[MIT](LICENSE) © 2026 skd1111
