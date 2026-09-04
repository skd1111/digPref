"""Agent 配置 —— 通过环境变量 / .env 文件加载。

所有配置项都可通过 EAIDE_ 前缀的环境变量覆盖。
Pydantic Settings 自动处理类型转换与校验。
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """EAIDE Agent 全局配置。

    使用方式：`from agent.config import settings` 后直接访问属性。
    测试中通过 monkeypatch 设置环境变量来覆盖默认值。
    """

    model_config = SettingsConfigDict(
        env_prefix="EAIDE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 服务器 ----
    host: str = "127.0.0.1"
    port: int = 8765
    dev: bool = False
    log_level: str = "info"

    # ---- LLM 路由 ----
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen2.5:14b"
    # 端侧 Ollama 总开关（BUGFIX #89）：未配置端侧模型时置 0，所有 LLM 链路零探测。
    # 已建 router.db.llm_backends 表的环境以「模型管理」里的 local 后端为准（无 local 行 = 未配置）。
    ollama_enabled: bool = True
    # 内网企业 LLM（兼容 OpenAI 协议）。
    # 默认为空：是否启用内网后端只看「模型管理」（router.db.llm_backends）里
    # 有没有启用的 private 后端；确需环境变量注入时才通过 EAIDE_PRIVATE_LLM_*
    # 覆盖。不要内置占位网关地址 —— 不可达的默认地址会让每条消息白等
    # TCP 连接超时（BUGFIX #57）。
    private_llm_base_url: str | None = None
    private_llm_api_key: str | None = None
    private_llm_model: str | None = None

    # ---- MCP ----
    mcp_config_path: str = "mcp.yaml"

    # ---- 审计 ----
    audit_db_path: str = "audit.sqlite"

    # ---- 智能路由（Phase 2C v2）----
    # 路由决策 / 成本 / 后端配置 SQLite（与 audit.sqlite 物理隔离）。
    # 相对路径 → 测试 chdir 到 tmp_path 时自动隔离。
    llm_router_db_path: str = "router.db"
    # L1 精确缓存（内存 LRU）。默认开；TTL 5 分钟。
    llm_cache_l1: bool = True
    llm_cache_l1_ttl: int = Field(default=300, ge=1)
    llm_cache_l1_max: int = Field(default=512, ge=1)

    # ---- 超时与限制 ----
    tool_timeout_sec: int = Field(default=10, ge=1)
    row_limit: int = Field(default=50, ge=1)
    # 用户审批决策的最长等待时间（秒）。超时自动拒绝。
    approval_timeout_sec: int = Field(default=1800, ge=1)

    # ---- HITL 总开关 ----
    require_hitl_for_write: bool = True

    # ---- Phase 1B 原生工具层 ----
    # 内置工具总开关（False 时 dispatcher 直接 short-circuit 拒绝）
    builtin_enabled: bool = True
    # 允许的根目录白名单（路径沙箱校验，相对 cwd 解析）
    # V0 简化：测试场景下为空（不强制白名单），生产应至少设 ["./workspace"]
    builtin_allowed_paths: list[str] = Field(default_factory=list)
    # 单文件最大字节数（超过转 logviewer）
    builtin_max_file_bytes: int = Field(default=100 * 1024 * 1024, ge=1024)
    # file_to_markdown 工具：外部 markitdown CLI 路径（可选覆盖；默认空 = 仅用进程内库）
    builtin_markitdown_executable: str = ""
    # file_to_markdown 工具：转换超时（秒）
    builtin_markitdown_timeout_sec: float = Field(default=60.0, ge=1.0, le=600.0)
    # office 工具族：OfficeCLI 二进制路径（可选覆盖；默认空 = 捆绑二进制 > PATH 探测）
    builtin_officecli_executable: str = ""
    # office 工具族：OfficeCLI 子进程超时（秒；渲染 / 批量操作可能较慢，默认 120）
    builtin_officecli_timeout_sec: float = Field(default=120.0, ge=1.0, le=1800.0)

    # ---- Phase 4 本地端侧模型 ----
    # 文本小模型（Qwen2.5-0.5B，意图分类/列计划）
    local_small_base_url: str = "http://127.0.0.1:8081/v1"
    local_small_model: str = "qwen2.5-0.5b"
    # 视觉模型（Moondream2，截图理解）
    local_vision_base_url: str | None = None
    local_vision_model: str | None = None
    # Embedding 模型（意图语义路由 / Few-Shot 检索 / KB 向量化共用）
    # 进程内 ONNX 优先（bge-small-zh-v1.5 量化版，随安装包分发，纯 CPU）；
    # 显式配置 local_embedding_base_url 时改走外置 OpenAI 兼容端点。
    local_embedding_base_url: str | None = None
    local_embedding_model: str | None = None
    # 进程内向量模型目录（含 tokenizer.json 与 onnx/model_quantized.onnx）
    local_embedding_onnx_dir: str = "model/bge-small-zh-v1.5-onnx"

    # ---- Phase 4 V1 本地知识库 ----
    # 知识库 SQLite 路径（相对路径 → 测试 chdir 到 tmp_path 时自动隔离）
    knowledge_db_path: str = "knowledge.db"
    # RAG 检索节点是否接入主图（START → intent → rag_retrieve → planner）
    # 默认 True；测试可设 EAIDE_RAG_ENABLED=false 跳过 rag_retrieve
    rag_enabled: bool = True
    # 默认 embedding 维度（bge-small-zh-v1.5 实测 512 维；仅零向量兜底用）
    local_embedding_dim: int = Field(default=512, ge=64, le=4096)

    # ---- 本地知识库混合检索 RAG（2026-09-03，审核专家 + 聊天共用）----
    # 混合检索 = SQLite FTS5（jieba 分词 + 原生 BM25）+ sqlite-vec 向量余弦，RRF 融合；
    # 数据（kb.db + 上传文件 + 参数 JSON）统一落 rag_kb_dir，复制即迁移。
    # 数据根目录（空 = 自动 data_root()/knowledge；生产=安装目录/knowledge）
    rag_kb_dir: str = ""
    # 子块（child）分块大小（字符）与重叠比例——子块语义聚焦，只做索引/检索
    rag_chunk_size: int = Field(default=512, ge=128, le=4096)
    rag_chunk_overlap: float = Field(default=0.1, ge=0.0, le=0.5)
    # 父块（parent）大小（字符）——small-to-big：命中子块后回喂父块给 LLM 补全上下文
    rag_parent_size: int = Field(default=2000, ge=512, le=16000)
    # 每次检索返回条数 / 各通道候选倍数（Top-k×倍数 再 RRF 融合）/ RRF 经验常数
    rag_top_k: int = Field(default=5, ge=1, le=20)
    rag_candidate_multiplier: int = Field(default=4, ge=1, le=10)
    rag_rrf_k: int = Field(default=60, ge=1, le=500)
    # 通道开关（关掉任一则单通道；两者都关或不可用则返空）
    rag_bm25_enabled: bool = True
    rag_vector_enabled: bool = True
    # 标题上下文前缀（无 LLM 的 Contextual Retrieval）：把层级标题路径拼到子块开头
    rag_contextual_prefix_enabled: bool = True
    # 单文件上传大小上限（MB）
    rag_max_file_mb: int = Field(default=50, ge=1, le=500)
    # 冷启动预加载（lifespan 后台预热 embedding / reranker / FTS5，best-effort）
    rag_preload_on_startup: bool = True
    # RAG 优先作答（根因修复 2026-09-04）：文档/制度类查询且知识库已 BM25 命中
    # 相关资料时，decompose 直接走 MAIN_AGENT 据召回作答，不进工具循环用 shell 翻文件系统。
    rag_first_answer_enabled: bool = True
    # 判定“知识库能作答”所需的最少 BM25 词元命中条数（matched 非空的召回数）。
    rag_first_min_matched_hits: int = Field(default=1, ge=1, le=20)

    # ---- ONNX Reranker（cross-encoder 重排，2026-09-03）----
    # 混合检索召回 Top-N 后，用 bge-reranker ONNX 交叉编码器深度打分重排取 Top-K；
    # 量化模型随安装包分发；模型文件缺失时自动 no-op（保持 RRF 排序），功能不中断。
    rag_rerank_enabled: bool = True
    # 重排候选数（从融合结果取前 N 送 reranker；N = top_k × 该倍数，另有上限）
    rag_rerank_top_n: int = Field(default=20, ge=1, le=100)
    # 进程内重排模型目录（含 tokenizer.json 与 onnx/model_quantized.onnx）
    local_reranker_onnx_dir: str = "model/bge-reranker-base-onnx"

    # ---- LLM 增强检索阶段（可插拔 seam，默认关；依赖本地 LLM 可用，敏感素材受 local-only 红线约束）----
    # 大模型知识库验证总开关（2026-09-03）：关闭时文档审核 / 聊天检索只走本地混合检索
    # （FTS5 BM25 + 向量 + RRF + reranker），检索与入库全程零大模型调用；开启后下列各
    # 增强阶段才按自己的子开关生效。总开关是硬闸：为 False 时子开关即使为 True 也不调模型。
    rag_llm_enhance_enabled: bool = False
    # 入库期：LLM 为每个子块生成上下文前缀（Anthropic Contextual Retrieval）
    rag_llm_contextual_enabled: bool = False
    # 检索期：HyDE（LLM 先生成假设性文档，用其向量检索）
    rag_hyde_enabled: bool = False
    # 检索期：Query Expansion（LLM/同义词扩展查询词，多路 BM25 合并）
    rag_query_expansion_enabled: bool = False

    # ---- 意图向量快速路由（semantic-router 模式，2026-08-07）----
    # 总开关：命中预置 Route 时零 LLM 直出意图分析；未命中/不可用静默回退原链路。
    # 2026-08-31 起默认开启（进程内 ONNX 向量模型；模型文件缺失时静默回退，
    # 不影响主链路）
    semantic_route_enabled: bool = True
    # 余弦相似度命中阈值（低于此值视为未命中，回退 LLM 分析）
    semantic_route_threshold: float = Field(default=0.78, ge=0.5, le=1.0)
    # V2（2026-08-31）：困难负样本拦截裕度——负样本分 >= 正样本分 - margin 即拦截
    semantic_route_negative_margin: float = Field(default=0.02, ge=0.0, le=0.5)
    # V2：BM25 混合检索权重（0 = 纯向量；无关键词信号时自动保持纯向量）
    semantic_route_hybrid_weight: float = Field(default=0.35, ge=0.0, le=0.9)
    # V2：高风险路由（删库/远程执行类）默认命中阈值，高于普通路由
    semantic_route_high_risk_threshold: float = Field(default=0.85, ge=0.5, le=1.0)

    # ---- Phase 13 DSpark 推测解码 ----
    # 策略 yaml 路径（不存在 / 解析失败 → 落回 DEFAULT_POLICIES）
    dspark_yaml_path: str = "config/llm/speculative.yaml"
    # 草稿模型路径（V0 占位；V1 才会真正下载）
    dspark_draft_model_path: str | None = None
    # 全局开关
    dspark_enable_global: bool = True
    # 短输出阈值
    dspark_short_output_threshold: int = Field(default=20, ge=1)
    # 上下文窗口大小（tokens）
    dspark_context_size: int = Field(default=4096, ge=512, le=262144)
    # GPU 推理层数（llama.cpp n_gpu_layers）
    dspark_gpu_layers: int = Field(default=0, ge=-1, le=999)

    # ---- Phase 14 V0 本地图像处理 ----
    # 图像处理任务 SQLite 路径（与 audit / knowledge / biznav 等 10 db 物理隔离）
    image_processing_db_path: str = "image_processing.db"
    # 图像处理最大文件字节数（默认 50MB）
    image_processing_max_bytes: int = Field(default=50 * 1024 * 1024, ge=1024 * 1024)
    # OCR 默认语言（ch=中文 / en=英文；V1 PaddleOCR 接力）
    image_processing_ocr_langs: tuple[str, ...] = ("ch", "en")

    # ---- Phase 15 V0 前端实时预览引擎 ----
    # 预览会话 SQLite 路径（与 audit / router / knowledge 等 db 物理隔离）
    preview_db_path: str = "preview.db"
    # Vite 子进程内存上限（MB；超出自动 kill + 审计）
    preview_max_memory_mb: int = Field(default=512, ge=128, le=2048)
    # 不活跃会话自动停止阈值（秒；默认 30 分钟）
    preview_inactive_timeout_sec: int = Field(default=1800, ge=60, le=86400)
    # 预览项目路径白名单（空 = 默认 home 子目录 + ~/.eaide/projects）
    preview_allowed_paths: list[str] = Field(default_factory=list)

    # ---- Phase 2B V0 类 FinalShell SSH PTY ----
    # SSH 会话 SQLite 路径（与 11 个其他 db 物理隔离）
    ssh_db_path: str = "ssh.db"
    # SSH 连接超时（默认 10s）
    ssh_connect_timeout: float = Field(default=10.0, ge=1.0, le=60.0)
    # 命令执行超时（默认 30s）
    ssh_command_timeout: float = Field(default=30.0, ge=1.0, le=300.0)
    # 最大并发 SSH 会话数
    ssh_max_sessions: int = Field(default=32, ge=1, le=256)

    # ---- Phase 5 V0 审核专家模式 ----
    # 审核专家 SQLite 路径（与 audit / tool_calls / ssh / image_processing 等 db 物理隔离）
    audit_expert_db_path: str = "audit_expert.db"

    # ---- 文档风险合规审核（审核专家 · 文档审核）----
    # 文档审核 SQLite 路径（与 audit_expert / preview 等 db 物理隔离）
    doc_review_db_path: str = "doc_review.db"
    # 文档审核模型名（缺省取 ollama_model）
    doc_review_model: str | None = None
    # 分类阶段读取文档前 N 字符
    doc_review_classify_max_chars: int = Field(default=4000, ge=500, le=20000)
    # 分析分块大小（字符）与重叠
    doc_review_chunk_max_chars: int = Field(default=8000, ge=1000, le=32000)
    doc_review_chunk_overlap: int = Field(default=200, ge=0, le=2000)
    # 分析并发度：风险维度×分块 单元的 LLM 并发调用数（限流防云端 429）
    doc_review_analyze_concurrency: int = Field(default=3, ge=1, le=8)
    # kb_refs 依据检索并发度：读取详情时对每条 finding 各做一次混合检索，串行会随
    # finding 数线性放大（实测 16 条≈32s）超过前端 client 超时；此处检索不走 LLM，
    # 只受 CPU(reranker)/SQLite 约束，并发压缩总耗时
    doc_review_kb_refs_concurrency: int = Field(default=4, ge=1, le=16)
    # 文档审核 LLM 路由链（按序降级）：cloud / private —— 两者要求相同，都只取
    # 「模型管理」注册表里已启用的后端（router.db.llm_backends，enabled_only）。
    # 云端优先：先试云端避免白等内网连接超时；云端不可用/未启用时回退已启用的内网 private。
    # 不回退 settings/env 配置、也不回退本地 ollama；两者都未启用则抛 LLMBackendError。
    doc_review_llm_chain: list[str] = Field(default_factory=lambda: ["cloud", "private"])
    # 扫描件 PDF OCR 回退（2026-09-04）：仅当 PDF 无文本层（pypdf 抽不到字）时，
    # 用 pypdfium2 栅格化逐页 + RapidOCR（端侧 ONNX）识别。纯本地、数据不出域；
    # 依赖缺失时静默退化为原「未提取到文本」报错。聊天侧 OCR 走 image_processing，与此开关无关。
    doc_review_pdf_ocr_enabled: bool = True
    # OCR 栅格化缩放（≈ dpi/72；2.0 ≈ 144dpi，兼顾识别率与速度）
    doc_review_pdf_ocr_scale: float = Field(default=2.0, ge=1.0, le=6.0)
    # 单文档最多 OCR 页数上限（防超大扫描件长时间阻塞；0 = 不限）
    doc_review_pdf_ocr_max_pages: int = Field(default=0, ge=0, le=2000)

    # ---- Phase 7 V0 数据专家模式 ----
    # 数据专家 SQLite 路径（与 audit / router / knowledge / ssh 等 db 物理隔离）
    data_expert_db_path: str = "data_expert.db"
    # 结果集 Parquet 存储目录（大对象不入库）
    data_result_dir: str = "results"
    # SQL 强制 LIMIT 上限（防 OOM）
    data_sql_row_limit: int = Field(default=10000, ge=1)
    # 业务字典目录（YAML 外置：_global.yaml 全局 + {source_id}.yaml 源级；
    # 相对路径 → 测试 chdir 自动隔离；目录缺失/解析失败退化内置默认字典）
    data_biz_dict_dir: str = "config/biz_dict"
    # Python 沙箱内存上限（MB）
    data_sandbox_mem_mb: int = Field(default=2048, ge=256)
    # Python 沙箱执行超时（秒）
    data_sandbox_timeout: int = Field(default=30, ge=5)
    # 导出数字水印开关
    data_export_watermark: bool = True
    # 导出强制 PII 脱敏（生产别关）
    data_require_mask_on_export: bool = True
    # 运行环境（EAIDE_ENV）："dev" | "prod"，默认 prod（fail-safe）
    env: str = "prod"
    # SQL 白名单豁免开关：仅当 env=="dev" 且本开关=true 时，
    # 数据专家模式才放行非 SELECT 语句（降级黑名单校验）
    data_allow_non_select_in_dev: bool = False

    # ---- Phase 12 V1.5 多智能体规模化调度 ----
    # Orchestrator 权威持久层 SQLite（与 audit / router / sessions 等 db 物理隔离）
    # 架构决策（2026-07-31）：本地 EAIDE 单进程，不引入 Redis —— SQLite 即权威层
    orchestrator_db_path: str = "orchestrator.db"
    # Worker Pool 并发（设计文档 §9：3-8）
    orchestrator_concurrency: int = Field(default=4, ge=1, le=8)
    # 单个子 Agent 硬超时（秒；设计文档 §2.4 默认 30s，上限 60s）
    orchestrator_task_timeout_sec: int = Field(default=30, ge=1, le=60)
    # Worker 重试上限（≤3；与图内 Auto-Repair retry_count≤2 是两层独立机制）
    orchestrator_max_attempts: int = Field(default=3, ge=1, le=3)
    # LLM Judge 抽样率（0 = 关闭；默认 10%，不作为 CI 闸门）
    orchestrator_judge_sample_rate: float = Field(default=0.1, ge=0.0, le=1.0)
    # 结构化事件日志目录（不引入 ELK；logs/orchestrator-YYYYMMDD.jsonl）
    orchestrator_log_dir: str = "logs"
    # 取消传播上限（秒；验收标准：≤ 1s）
    orchestrator_cancel_deadline_sec: float = Field(default=1.0, ge=0.1, le=10.0)

    # ---- Phase 12 V2 自动多智能体（Agent 自动判断，非用户手动选择）----
    # 总开关：False 时所有任务走单 Agent 工具链路（不触发分解判定）
    multi_agent_auto_enabled: bool = True
    # 单次自动分解最多子任务数（2 ≤ n ≤ 30；派生树整树上限 30 仍然生效）
    multi_agent_max_subtasks: int = Field(default=6, ge=2, le=30)

    # ---- 动态工具加载与工具调用（Phase 13 V2 / 2026-08-03）----
    # 总开关：false 时主图走既有 planner → tool_runner 路径（测试用 / 回退）
    tool_loop_enabled: bool = True
    # 动态工具循环最大轮次（防死循环；2026-08-25 默认 8→24：长链任务如
    # PPT 生成 10+ 步在 8 轮下必被误杀；死循环改由停滞熔断拦截，见 loop.py）
    tool_loop_max_turns: int = Field(default=24, ge=2, le=30)
    # 发散刹车（根因修复 2026-09-04）：只读文件探测（dir/ls/glob/find/list_dir/grep）
    # 累计达软阈先注入强制收敛指令，达硬阈直接停——专治跨盘符全盘翻找卡死。
    tool_loop_fs_probe_soft_limit: int = Field(default=6, ge=1, le=100)
    tool_loop_fs_probe_hard_limit: int = Field(default=12, ge=2, le=200)
    # 单 run 工具循环墙钟超时（秒）：云端每轮往返可达百秒，24 轮累计十几分钟
    # → 用户感知死机；超墙钟即用已有上下文收敛作答。<=0 关闭该刹车。
    tool_loop_wall_clock_sec: float = Field(default=240.0, ge=0.0, le=3600.0)

    # 回答逐字流式（2026-09-03）：responder 终答路径（summarise 家族）逐 token
    # 下发 answer_delta SSE 事件；false 时回退整段 message 下发（非流式 summarise）。
    answer_stream_enabled: bool = True
    # 单轮最多注册候选工具数（与提示词 MAX_SELECTED_TOOLS 一致）
    tool_loop_max_selected: int = Field(default=5, ge=1, le=20)
    # 单条工具结果注入上下文的最大字符数
    tool_loop_max_result_chars: int = Field(default=4000, ge=200, le=20000)
    # 上下文里保留的最近工具结果条数
    tool_loop_max_results_kept: int = Field(default=10, ge=1, le=50)
    # http_get 工具：超时（秒）与响应大小上限（字节）
    builtin_http_timeout_sec: float = Field(default=10.0, ge=1.0, le=120.0)
    builtin_http_max_bytes: int = Field(default=1024 * 1024, ge=1024, le=10 * 1024 * 1024)

    # ---- 工具结果剪枝与 spill 落盘（借鉴 dsh；2026-08-14）----
    # 超大只读工具结果全文落盘 + 内联替换为「头尾预览 + 定位符」
    tool_spill_enabled: bool = True
    # 触发阈值（字符）：替换后内联内容固定 ≤ ~3400，不会超出 tool_loop_max_result_chars 预算
    tool_spill_threshold_chars: int = Field(default=4000, ge=500, le=200000)
    # spill 文件目录（相对路径 → 测试 chdir 自动隔离，与 router.db 同机制）
    tool_spill_dir: str = "spill"

    # ---- Phase 16 思维链可视化与文件操作追踪 ----
    # 思维链 SQLite 路径（与 audit / router / knowledge 等 db 物理隔离）
    trace_db_path: str = "trace.db"
    # 思维链记录总开关（False 时不写 thinking_steps；金融合规场景勿关）
    trace_enabled: bool = True

    # ---- Phase 18 双框架 ----
    # 工具链路径配置（单文件 JSON：python/node/pnpm/java/javac/tsc 等本机路径）
    toolchain_config_path: str = "toolchain.json"
    # 工作空间路径配置（单文件 JSON：{"path": ...}；空 = 默认数据根/workspace）
    workspace_config_path: str = "workspace.json"
    # 工作空间内分类子目录名（docs/data/images/other；改键名即改目录名）
    workspace_subdir_docs: str = "docs"
    workspace_subdir_data: str = "data"
    workspace_subdir_images: str = "images"
    workspace_subdir_other: str = "other"

    # ---- Phase 19 V0 自进化与自评测闭环 ----
    # 进化总开关（False 时不记轨迹 / 不反思 / 不注入经验；反馈 API 仍可用）
    evolution_enabled: bool = True
    # 独立持久库（与 sessions / orchestrator / audit 等 db 物理隔离）
    evolution_db_path: str = "evolution.db"
    # 主对话 Judge 抽样率（0 = 关闭；V1 才启用，设计文档 §2.3）
    evolution_judge_sample_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    # 经验注入：单次检索条数上限 + 注入片段字符上限（防挤占上下文）
    evolution_experience_top_k: int = Field(default=3, ge=1, le=10)
    evolution_experience_max_chars: int = Field(default=1200, ge=200, le=4000)
    # Phase 19 V1：技能蒸馏触发门槛（同签名成功次数；草稿永不自动启用）
    skill_draft_min_successes: int = Field(default=3, ge=2, le=20)
    # Phase 19 V1.5：Prompt 影子优化——采纳最小得分增益（1-5 制）与自动采纳开关（默认人工确认）
    prompt_optimize_gain_threshold: float = Field(default=0.5, ge=0.0, le=2.0)
    evolution_prompt_auto_adopt: bool = False


settings = Settings()
