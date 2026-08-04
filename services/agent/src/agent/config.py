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
    # 内网企业 LLM（兼容 OpenAI 协议）。
    # 默认指向集群内网关；生产环境通过环境变量覆盖。
    private_llm_base_url: str | None = "http://172.1.0.134:8000/v1"
    private_llm_api_key: str | None = "internal-no-auth"
    private_llm_model: str | None = "DeepSeek-RD-Llama-70B-Int8"

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

    # ---- Phase 4 本地端侧模型 ----
    # 文本小模型（Qwen2.5-0.5B，意图分类/列计划）
    local_small_base_url: str = "http://127.0.0.1:8081/v1"
    local_small_model: str = "qwen2.5-0.5b"
    # 视觉模型（Moondream2，截图理解）
    local_vision_base_url: str | None = None
    local_vision_model: str | None = None
    # Embedding 模型（bge-small-zh，外部 KB 检索用）
    local_embedding_base_url: str | None = None
    local_embedding_model: str | None = None

    # ---- Phase 4 V1 本地知识库 ----
    # 知识库 SQLite 路径（相对路径 → 测试 chdir 到 tmp_path 时自动隔离）
    knowledge_db_path: str = "knowledge.db"
    # RAG 检索节点是否接入主图（START → intent → rag_retrieve → planner）
    # 默认 True；测试可设 EAIDE_RAG_ENABLED=false 跳过 rag_retrieve
    rag_enabled: bool = True
    # 默认 embedding 维度（bge-small-zh-v1.5 = 384）
    local_embedding_dim: int = Field(default=384, ge=64, le=4096)

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

    # ---- Phase 7 V0 数据专家模式 ----
    # 数据专家 SQLite 路径（与 audit / router / knowledge / ssh 等 db 物理隔离）
    data_expert_db_path: str = "data_expert.db"
    # 结果集 Parquet 存储目录（大对象不入库）
    data_result_dir: str = "results"
    # SQL 强制 LIMIT 上限（防 OOM）
    data_sql_row_limit: int = Field(default=10000, ge=1)
    # Python 沙箱内存上限（MB）
    data_sandbox_mem_mb: int = Field(default=2048, ge=256)
    # Python 沙箱执行超时（秒）
    data_sandbox_timeout: int = Field(default=30, ge=5)
    # 导出数字水印开关
    data_export_watermark: bool = True
    # 导出强制 PII 脱敏（生产别关）
    data_require_mask_on_export: bool = True

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
    # 动态工具循环最大轮次（防死循环）
    tool_loop_max_turns: int = Field(default=8, ge=2, le=30)
    # 单轮最多注册候选工具数（与提示词 MAX_SELECTED_TOOLS 一致）
    tool_loop_max_selected: int = Field(default=5, ge=1, le=20)
    # 单条工具结果注入上下文的最大字符数
    tool_loop_max_result_chars: int = Field(default=4000, ge=200, le=20000)
    # 上下文里保留的最近工具结果条数
    tool_loop_max_results_kept: int = Field(default=10, ge=1, le=50)
    # http_get 工具：超时（秒）与响应大小上限（字节）
    builtin_http_timeout_sec: float = Field(default=10.0, ge=1.0, le=120.0)
    builtin_http_max_bytes: int = Field(default=1024 * 1024, ge=1024, le=10 * 1024 * 1024)

    # ---- Phase 16 思维链可视化与文件操作追踪 ----
    # 思维链 SQLite 路径（与 audit / router / knowledge 等 db 物理隔离）
    trace_db_path: str = "trace.db"
    # 思维链记录总开关（False 时不写 thinking_steps；金融合规场景勿关）
    trace_enabled: bool = True

    # ---- Phase 18 双框架 ----
    # 工具链路径配置（单文件 JSON：python/node/pnpm/java/javac/tsc 等本机路径）
    toolchain_config_path: str = "toolchain.json"


settings = Settings()
