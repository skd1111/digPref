"""Phase 12 V0 — 多智能体 Pydantic 契约。

V0 简化版（V1 加 Redis Key + 制品库 + Pydantic discriminated union）：

- SubAgentSpec —— 主 Agent 派单
- SubAgentReport —— 子 Agent 回报
- ContextPolicy —— 三类上下文策略
- ModelPolicy —— 模型选择（task / role）
- SubAgentStatus —— 状态枚举
- ArtifactRef —— 制品引用（V0 简化：本地 dict 引用）
- StateDelta —— 增量状态快照
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---- 上下文策略：场景化取舍（铁律 4）--------------------------------


class ContextPolicy(BaseModel):
    """上下文传递策略 —— V0 三类对齐设计文档 §2.1。

    简单任务用 passthrough，中等用 shared_memory_pool，长会话用 incremental_summary。
    required_fields 在任何压缩策略下都原样保留（铁律 5）。
    """

    strategy: Literal["passthrough", "shared_memory_pool", "incremental_summary"] = "passthrough"
    # 必读字段不可压（铁律 5）
    required_fields: list[str] = Field(default_factory=list)
    # 共享记忆池（中等协作用）
    shared_keys: list[str] = Field(default_factory=list)
    # 摘要最大 token（长会话用）
    max_summary_tokens: int = 500


# ---- 模型选择（铁律 3：敏感上下文本机）----------------------------------


class ModelPolicy(BaseModel):
    """子 Agent 模型选择策略。

    role 在 V0 里直接复用 LMRouter 的 'utility' / 'reasoning' / 'execution'。
    task_type 用于 `_LOCAL_ONLY_TASKS` 红线判断（intent / repair / data_summary）。
    """

    role: Literal["utility", "reasoning", "execution"] = "execution"
    task_type: Literal["intent", "repair", "data_summary", "plan", "summarise", "custom"] = "custom"
    # 敏感负载标记：携带 DB 行 / SQL 错误 / PII → 强制本地
    carries_sensitive_payload: bool = False
    # 用户显式指定 backend（V0 简化：空 = 走 router 默认）
    preferred_backend: str | None = None


# ---- 子 Agent 规格（派单入口）---------------------------------------


class SubAgentSpec(BaseModel):
    """主 Agent 派给子 Agent 的任务单。"""

    spec_version: int = 1
    # 子 Agent ID（主图生成 / 或子 Agent 派生时自己生成；UUID）
    sub_agent_id: str
    parent_run_id: str  # 主 Agent 的 run_id
    parent_sub_agent_id: str | None = None  # 嵌套派生的父 sub_agent_id
    # 派生树深度（0 = 主 Agent 派；1 = 一级子；2 = 二级子；上限 = 2）
    depth: int = 1
    # 任务内容（结构化；不传裸 prompt）
    task_type: str
    task_description: str
    # 输入 payload（结构化）
    input_payload: dict[str, Any] = Field(default_factory=dict)
    # 上下文传递策略
    context_policy: ContextPolicy = Field(default_factory=ContextPolicy)
    # 模型选择策略
    model_policy: ModelPolicy = Field(default_factory=ModelPolicy)
    # 写操作标记：true → 子 Agent 写操作必须回主图 hitl_gate（V0 stub）
    requires_write: bool = False


# ---- 制品引用 ---------------------------------------------------


class ArtifactRef(BaseModel):
    """子 Agent 产出的制品引用（V0 简化：内存 dict；V1 换 Redis / 向量库）。

    content_hash 用于可追溯（铁律 4）。
    """

    artifact_id: str
    kind: Literal["summary", "raw_text", "table", "chart_spec", "code_diff"] = "summary"
    content_hash: str = ""  # SHA-256 hex（V0 简化：可空）
    byte_size: int = 0
    preview: str = ""  # 100 字符摘要，供前端展示


# ---- 增量状态快照 -----------------------------------------------


class StateDelta(BaseModel):
    """增量状态 —— 子 Agent 回报里的关键字段变化（V0 简化：整段 return）。"""

    fields_added: dict[str, Any] = Field(default_factory=dict)
    fields_modified: dict[str, Any] = Field(default_factory=dict)
    raw_refs: list[ArtifactRef] = Field(default_factory=list)


# ---- 子 Agent 状态枚举 -----------------------------------------------


class SubAgentStatus(str, Enum):
    """子 Agent 生命周期状态（V0 简化：缺 running / cancelled 终态细节）。"""

    PENDING = "pending"  # 已派单，未启动
    RUNNING = "running"  # LLM 调用中
    OK = "ok"  # 完成 + 校验通过
    ERR = "err"  # 异常 / LLM 调用失败 / 校验失败
    DLQ = "dlq"  # 重试 3 次仍失败 → 进死信
    CANCELLED = "cancelled"  # 用户主动取消


# ---- 子 Agent 回报 -----------------------------------------------


class SubAgentReport(BaseModel):
    """子 Agent 回报 —— 主 Agent 决策的输入。

    V0 结构校验：
      - status 必填
      - 主 Agent 必读字段（summary / confidence / latency_ms / state_delta）必填
      - status=ok 时 summary 不可空
      - status=err / dlq 时 error_message 不可空
    """

    spec_version: int = 1
    sub_agent_id: str
    parent_run_id: str
    parent_sub_agent_id: str | None = None
    status: SubAgentStatus
    started_at: datetime
    finished_at: datetime | None = None
    # LLM 返回内容（结构化或纯文本）
    summary: str = ""
    confidence: float = 0.0
    # 状态变更（增量）
    state_delta: StateDelta = Field(default_factory=StateDelta)
    # 制品
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    # 模型信息（V0 简单填）
    backend_used: str = ""
    model_used: str = ""
    latency_ms: int = 0
    # 失败时的错误
    error_message: str = ""
    # 重试次数
    attempts: int = 1

    def validate_semantic(self) -> list[str]:
        """业务校验：返回错误信息列表（空 = 通过）。

        比 Pydantic schema 校验更严格的语义检查（如 status=ok 时 summary 必非空）。
        """
        errors: list[str] = []
        if self.status == SubAgentStatus.OK and not self.summary.strip():
            errors.append("status=ok 时 summary 必填且非空")
        if (
            self.status in (SubAgentStatus.ERR, SubAgentStatus.DLQ)
            and not self.error_message.strip()
        ):
            errors.append(f"status={self.status.value} 时 error_message 必填且非空")
        if self.confidence < 0 or self.confidence > 1:
            errors.append(f"confidence 越界（0-1），实际 {self.confidence}")
        if self.latency_ms < 0:
            errors.append(f"latency_ms 为负，实际 {self.latency_ms}")
        return errors
