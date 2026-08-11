"""sessions.models_macc —— Phase 6 V1 MACC 三层自适应压缩数据类。

设计（来自 phase-6-session-mgmt.md §2 / §5）：
- L3 工作记忆：WorkingMemoryAnchor（关键状态变量锚点）+ WorkingMemorySlice（窗口切片）
- L3 情景记忆：EventNode / EventEdge / EventGraph（图谱）
- L3 语义记忆：SemanticRule（蒸馏规则）
- L2 表示层：GistToken（占位，V1 不实装真正编码器）
- 路由：CompressionContext / CompressionStrategy / CompressionResult
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

# ---- L3 语义记忆 ----------------------------------------------------------


@dataclass
class SemanticRule:
    """从情景记忆蒸馏出的语义规则（L3 Semantic Memory）。

    Attributes:
        id: uuid
        session_id: 来源会话（最近一次更新时所属）
        pattern: 触发模式（"订单平账" / "deploy redis" 等）
        rule_text: 蒸馏后的规则描述（自然语言）
        confidence: 0-1（出现频率归一化）
        last_updated: 毫秒时间戳
        source_event_ids: 可追溯的事件 ID 列表（便于回溯）
    """

    id: str
    session_id: str
    pattern: str
    rule_text: str
    confidence: float = 0.0
    last_updated: int = 0
    source_event_ids: list[str] = field(default_factory=list)

    @classmethod
    def new(
        cls,
        *,
        session_id: str,
        pattern: str,
        rule_text: str,
        confidence: float = 0.0,
        source_event_ids: list[str] | None = None,
    ) -> SemanticRule:
        return cls(
            id=str(uuid.uuid4()),
            session_id=session_id,
            pattern=pattern,
            rule_text=rule_text,
            confidence=confidence,
            last_updated=int(time.time() * 1000),
            source_event_ids=list(source_event_ids or []),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "pattern": self.pattern,
            "rule_text": self.rule_text,
            "confidence": self.confidence,
            "last_updated": self.last_updated,
            "source_event_ids": list(self.source_event_ids),
        }


# ---- L3 情景记忆 ----------------------------------------------------------

EventStatus = Literal["ok", "pending", "rejected", "error"]
EventRelation = Literal["next", "triggers", "depends_on", "caused_by"]


@dataclass
class EventNode:
    """事件图谱节点（L3 Episodic Memory）。

    Attributes:
        id: uuid
        session_id: 所属会话
        entity: 主体（表名 / 工具 / 节点名）
        action: 动作（SQL / 工具调用 / 状态变更）
        result: 结果摘要
        status: 'ok' | 'pending' | 'rejected' | 'error'
        metadata: 透传 JSON
        created_at: 毫秒
    """

    id: str
    session_id: str
    entity: str
    action: str
    result: str = ""
    status: EventStatus = "ok"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: int = 0

    @classmethod
    def new(
        cls,
        *,
        session_id: str,
        entity: str,
        action: str,
        result: str = "",
        status: EventStatus = "ok",
        metadata: dict | None = None,
    ) -> EventNode:
        return cls(
            id=str(uuid.uuid4()),
            session_id=session_id,
            entity=entity,
            action=action,
            result=result,
            status=status,
            metadata=dict(metadata or {}),
            created_at=int(time.time() * 1000),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "entity": self.entity,
            "action": self.action,
            "result": self.result,
            "status": self.status,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


@dataclass
class EventEdge:
    """事件图谱边（L3 Episodic Memory）。"""

    id: int = 0  # sqlite 自增
    session_id: str = ""
    from_node: str = ""
    to_node: str = ""
    relation: EventRelation = "next"
    metadata: dict[str, Any] = field(default_factory=dict)


# ---- L3 工作记忆 ----------------------------------------------------------


@dataclass
class WorkingMemoryAnchor:
    """关键状态变量锚点 —— 即便窗口滑动也不丢失（设计 §2.1）。

    Attributes:
        node_name: 节点名（"hitl_gate" / "tool_runner" / "repair" / "intent" 等）
        vars: 关键变量名列表
    """

    node_name: str
    vars: list[str] = field(default_factory=list)


# 架构师约定的关键状态变量锚点表（CLAUDE.md §6 备忘）
DEFAULT_ANCHORS: tuple[WorkingMemoryAnchor, ...] = (
    WorkingMemoryAnchor(node_name="hitl_gate", vars=["approval_id", "pending_decision"]),
    WorkingMemoryAnchor(
        node_name="tool_runner", vars=["last_tool_name", "last_tool_status", "affected_rows"]
    ),
    WorkingMemoryAnchor(node_name="repair", vars=["retry_count", "error_message"]),
    WorkingMemoryAnchor(node_name="intent", vars=["intent", "active_skill_id"]),
)


# ---- L2 表示层（占位）-----------------------------------------------------


@dataclass
class GistToken:
    """L2 Gist Token（占位 —— V1 不实装真正编码器）。

    设计 §3.1：V2 才训练专用 Perceiver Resampler；V1 仅数据结构 + 接口定义。
    """

    id: str
    source_type: str  # 'code' | 'image' | 'log' | 'db_row'
    token_count: int
    embedding: list[float] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---- 路由 / 上下文 --------------------------------------------------------


@dataclass
class CompressionContext:
    """CompressionRouter 的输入（设计 §5）。"""

    session_id: str
    token_count: int = 0
    message_count: int = 0
    task_complexity: Literal["simple", "medium", "complex", "reasoning"] = "medium"
    memory_entropy: float = 0.0  # 0-1（信息重复度）
    has_multimodal: bool = False
    has_code: bool = False
    has_logs: bool = False
    has_db_rows: bool = False
    idle_time_s: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


# 压缩策略枚举（设计 §5.1）
CompressionStrategy = Literal[
    "NONE",  # 原文全量（短对话）
    "WORKING_ONLY",  # 仅 L3 工作记忆滑动窗口
    "MEMORY",  # L3 全三层（工作 + 情景 + 语义）
    "GIST",  # L2 Gist Token 压缩
    "HYBRID",  # 混合（默认）
    "KV_CACHE",  # L1 KV Cache 压缩（由 DSpark 实际执行）
]


@dataclass
class CompressionResult:
    """CompressionRouter 的输出。"""

    strategy: CompressionStrategy
    before_tokens: int
    after_tokens: int
    compression_ratio: float  # after / before
    layers_used: list[str] = field(default_factory=list)  # ["L1", "L2", "L3"]
    working_memory: list[Any] = field(default_factory=list)  # WorkingMemorySlice（待 V1.5）
    event_graph_nodes: list[EventNode] = field(default_factory=list)
    semantic_rules: list[SemanticRule] = field(default_factory=list)
    gist_tokens: list[GistToken] = field(default_factory=list)
    formatted_prompt: str = ""  # 拼好的 Prompt 片段
    elapsed_ms: int = 0
    backend: str = "router"

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "before_tokens": self.before_tokens,
            "after_tokens": self.after_tokens,
            "compression_ratio": self.compression_ratio,
            "layers_used": self.layers_used,
            "working_memory_count": len(self.working_memory),
            "event_graph_node_count": len(self.event_graph_nodes),
            "semantic_rule_count": len(self.semantic_rules),
            "gist_token_count": len(self.gist_tokens),
            "formatted_prompt_len": len(self.formatted_prompt),
            "elapsed_ms": self.elapsed_ms,
            "backend": self.backend,
        }
