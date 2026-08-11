"""智能路由数据模型（Phase 2C v2）。

这些是 RouterEngine 的配置与决策载体，同时也是 router.db 各表的行镜像。
纯 dataclass / Enum，无 I/O，无外部依赖——storage.py 负责落盘，engine.py 负责决策。

设计约束（见 docs/design/phase-2c-smart-router.md §4）：
    - LLMBackend.api_key_ref 只存 Keyring 占位符，明文绝不进这里（CLAUDE.md §5）
    - data_residency 决定后端能否承接敏感任务（硬规则 rules.py 读它）
    - ScoreBreakdown 权重固定：能力 .35 / 成本 .25 / 延迟 .20 / 合规 .15 / 可用 .05
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class TaskCategory(str, Enum):
    """任务复杂度分类（classifier.py 产出，决定候选后端层级）。"""

    SIMPLE = "simple"  # 意图 / 实体 / 关键词 → 本地小模型
    MEDIUM = "medium"  # 代码解释 / SQL 生成 / 日志分析 → 私有中模型
    COMPLEX = "complex"  # 代码生成 / 跨系统规划 / 金融分析 → 云端大模型
    SPECIAL = "special"  # 视觉理解 / 向量化 → 专用模型


class Sensitivity(str, Enum):
    """数据敏感度（sensitivity.py 产出，喂给硬规则）。"""

    PUBLIC = "public"
    INTERNAL = "internal"
    PII = "pii"  # 金融 PII → 强制不出云
    PRODUCTION = "production"  # 生产操作 → 强制私有化


# 数据驻留等级，从松到严；硬规则按此判断后端能否承接某敏感度任务。
RESIDENCY_LOCAL = "local"
RESIDENCY_PRIVATE = "private"
RESIDENCY_CLOUD = "cloud"

# 熔断器状态
CB_CLOSED = "closed"
CB_OPEN = "open"
CB_HALF_OPEN = "half_open"


@dataclass
class LLMBackend:
    """一个模型后端的配置（router.db.llm_backends 行镜像）。

    api_key_ref 列直接存明文 API Key（配置文件模式，不走系统凭据管理器）。
    """

    name: str
    type: str  # local / private / cloud
    base_url: str
    model_name: str
    api_key_ref: str | None = None
    capabilities: list[str] = field(default_factory=list)
    max_context: int = 8192
    cost_per_1k_tokens: float = 0.0
    timeout_seconds: int = 30
    data_residency: str = RESIDENCY_LOCAL
    enabled: bool = True
    role: str = "execution"  # V0 默认 execution（兼容旧 LLMBackend）；utility/reasoning/execution

    def to_row(self) -> dict:
        """转成 storage 可写的扁平 dict（capabilities → JSON 由 storage 处理）。"""
        return {
            "name": self.name,
            "type": self.type,
            "base_url": self.base_url,
            "model_name": self.model_name,
            "api_key_ref": self.api_key_ref,
            "capabilities": list(self.capabilities),
            "max_context": self.max_context,
            "cost_per_1k_tokens": self.cost_per_1k_tokens,
            "timeout_seconds": self.timeout_seconds,
            "data_residency": self.data_residency,
            "enabled": self.enabled,
            "role": self.role,
        }

    @property
    def can_leave_host(self) -> bool:
        """是否允许接收「必须留在本机」的任务。仅 local 驻留为 False→可留本机。"""
        return self.data_residency != RESIDENCY_LOCAL

    def validate_protocol(self) -> str | None:
        """检查 base_url / api_key_ref 必填性（Phase 2C V0 用户约定）。

        Returns:
            错误信息（None = 通过）
        """
        # 端侧：Ollama 原生，不需要 api_key_ref
        if self.type == "local":
            if not self.base_url:
                return f"{self.name}: 端侧 Ollama 必须配置 base_url"
            return None
        # 内网（private / local_private）：OpenAI 格式，需要 base_url，不需要 api_key
        if self.type == "private":
            if not self.base_url:
                return f"{self.name}: 内网模型必须配置 base_url"
            return None
        # 云端：OpenAI 格式，需要 base_url + api_key_ref
        if self.type == "cloud":
            if not self.base_url:
                return f"{self.name}: 云端必须配置 base_url"
            if not self.api_key_ref:
                return f"{self.name}: 云端必须配置 api_key_ref（API Key 直接存 DB）"
            return None
        return f"{self.name}: 未知 type={self.type}"


@dataclass
class ScoreBreakdown:
    """五维评分明细（scoring.py 产出）。各维度 0-1，total 为加权和。"""

    capability: float = 0.0
    cost: float = 0.0
    latency: float = 0.0
    compliance: float = 0.0
    availability: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.capability * 0.35
            + self.cost * 0.25
            + self.latency * 0.20
            + self.compliance * 0.15
            + self.availability * 0.05
        )

    def as_dict(self) -> dict:
        return {
            "capability": self.capability,
            "cost": self.cost,
            "latency": self.latency,
            "compliance": self.compliance,
            "availability": self.availability,
            "total": self.total,
        }


@dataclass
class RoutingDecision:
    """一次路由决策的完整记录（router.db.routing_decisions 行镜像 + Trace 载体）。"""

    request_id: str
    user_id: str = "anonymous"
    task_category: TaskCategory | None = None
    sensitivity: Sensitivity | None = None
    primary_backend: str | None = None
    actual_backend: str | None = None
    fallback_chain: list[str] = field(default_factory=list)
    # 候选排序审计：[(backend_name, ScoreBreakdown), ...]
    candidates: list[tuple[str, ScoreBreakdown]] = field(default_factory=list)
    fallback_used: bool = False
    cache_hit: bool = False
    estimated_cost: float = 0.0
    actual_cost: float = 0.0
    latency_ms: int = 0
    quality_score: float | None = None

    # === Phase 13 DSpark 字段（V0 决策层注入）===
    # 4 字段从 agent.llm.dspark.api.decide_for_task() 填充
    speculative_enabled: bool = False
    n_draft: int = 1
    draft_p_min: float = 1.0
    draft_model: str | None = None
    dspark_reason: str = "off-no-dspark"  # 决策原因（供 metrics 指标展示）

    # === Phase 2C V2.0 Spark 模式双跳字段（engine.route_spark 填充）===
    spark_draft: str | None = None
    spark_execution_output: str | None = None
    spark_reasoning_backend: str | None = None
    spark_execution_backend: str | None = None

    def trace_dict(self) -> dict:
        """结构化 Trace（写入 routing_decisions.trace_json + SSE llm_route_decided）。"""
        return {
            "request_id": self.request_id,
            "task_category": self.task_category.value if self.task_category else None,
            "sensitivity": self.sensitivity.value if self.sensitivity else None,
            "primary_backend": self.primary_backend,
            "actual_backend": self.actual_backend,
            "fallback_chain": list(self.fallback_chain),
            "fallback_used": self.fallback_used,
            "cache_hit": self.cache_hit,
            "candidates": [
                {"backend": name, "score": sb.as_dict()} for name, sb in self.candidates
            ],
            "speculative_enabled": self.speculative_enabled,
            "n_draft": self.n_draft,
            "draft_p_min": self.draft_p_min,
            "draft_model": self.draft_model,
            "dspark_reason": self.dspark_reason,
            "spark_draft": self.spark_draft,
            "spark_execution_output": self.spark_execution_output,
            "spark_reasoning_backend": self.spark_reasoning_backend,
            "spark_execution_backend": self.spark_execution_backend,
        }


@dataclass
class BudgetVerdict:
    """预算检查结论（budget.py 产出）。"""

    allowed: bool = True
    force_downgrade: bool = False
    reason: str = ""
