"""Shared LLM types — defined here to avoid circular imports between
router.py and ollama.py / private_llm.py.

Phase 2G V1.1 (2026-07-28): TaskKind Literal 扩展加入 `biznav_extract`，
让 `_LOCAL_ONLY_TASKS` 的 frozenset 类型签名与实际值一致（不出现
`frozenset[TaskKind]` 含未声明字面量的"伪类型错误"）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Intent = Literal["query", "mutate", "orchestrate", "chitchat"]
TaskKind = Literal[
    "intent",
    "plan",
    # Phase 12 V2: 自动多智能体分解判定（接触用户原始内容 → 本地红线）
    "decompose",
    # 动态工具路由与调用编排（接触用户内容 + 工具结果 → 本地红线）
    "tool_orchestrate",
    "repair",
    "summarise",
    "toolspec",
    # Phase 2D V0: skill 路由（关键词命中 + LLM 兜底意图分类）
    "skill_router",
    # Phase 12 V0: 数据摘要
    "data_summary",
    # Phase 2G V1.1 (2026-07-28): 业务功能点提取
    "biznav_extract",
    # Phase 4 V0: 本地端侧任务
    "local_intent",
    "vision_understand",
    # Phase 2F+ V1 (2026-07-29): 日志级别分类
    "log_level_classify",
    # Phase 1B V1 (2026-07-30): 原生工具产出物汇总
    "builtin_tool_summary",
    "builtin_search_summarize",
    # Phase 14 V0 (2026-07-31): 图像处理 OCR 文本
    "image_processing_summary",
    # Phase 2B V0 (2026-07-31): SSH 命令输出
    "ssh_command_summary",
    # Phase 7 预留 (2026-07-31): 数据专家敏感任务
    "schema_link",
    "chart_reco",
    # 文档风险合规审核（2026-08-04）：文档分类 / 风险分析（路由链可配置，允许云端）
    "doc_classify",
    "doc_analyze",
    # mock 模式标记（非真实任务，不走 LLM 调度）
    "mock_mode",
]


# ---- 结构化意图分析（意图识别重构 2026-08-06）-------------------------------

_INTENT_CATEGORIES = (
    "chat",
    "knowledge_qa",
    "data_query",
    "task_execution",
    "calculation",
    "content_generation",
    "multi_step_task",
    "clarification_needed",
    "refusal",
)

# 细分类型 → 既有四分类映射（下游路由 / responder 兼容）
_CATEGORY_TO_INTENT: dict[str, Intent] = {
    "chat": "chitchat",
    "knowledge_qa": "query",
    "data_query": "query",
    "calculation": "query",
    "task_execution": "mutate",
    "content_generation": "query",
    "multi_step_task": "orchestrate",
    "clarification_needed": "query",
    "refusal": "chitchat",
}


@dataclass
class IntentAnalysis:
    """一次结构化意图分析结果（Intent Router 输出）。

    既保留既有四分类 intent（下游兼容），又携带计划规范要求的
    改写句 / 细分类型 / 实体 / 缺失字段 / 追问 / 风险等级。
    """

    intent: Intent = "query"
    rewritten_query: str = ""
    intent_category: str = "knowledge_qa"
    confidence: float = 0.5
    entities: dict[str, Any] = field(default_factory=dict)
    missing_fields: list[str] = field(default_factory=list)
    need_tool: bool = False
    need_clarification: bool = False
    clarification_message: str = ""
    risk_level: str = "low"
    reason: str = ""
    backend: str = ""  # 实际产出后端（ollama / private / fallback）

    @classmethod
    def from_raw(
        cls, raw: dict[str, Any], *, fallback_text: str = "", backend: str = ""
    ) -> IntentAnalysis:
        """宽容解析 LLM JSON；非法字段丢弃、缺失字段用安全默认值。"""
        intent = raw.get("intent")
        category = str(raw.get("intent_category") or "")
        if intent not in ("query", "mutate", "orchestrate", "chitchat"):
            intent = _CATEGORY_TO_INTENT.get(category, "query")
        if category not in _INTENT_CATEGORIES:
            category = "knowledge_qa"
        raw_entities = raw.get("entities")
        entities: dict[str, Any] = raw_entities if isinstance(raw_entities, dict) else {}
        missing = raw.get("missing_fields")
        confidence_raw = raw.get("confidence")
        try:
            confidence = (
                max(0.0, min(1.0, float(confidence_raw))) if confidence_raw is not None else 0.5
            )
        except (TypeError, ValueError):
            confidence = 0.5
        risk = str(raw.get("risk_level") or "low")
        if risk not in ("low", "medium", "high", "critical"):
            risk = "low"
        need_clarification = bool(raw.get("need_clarification"))
        return cls(
            intent=intent,
            rewritten_query=str(raw.get("rewritten_query") or fallback_text).strip()
            or fallback_text,
            intent_category=category,
            confidence=confidence,
            entities=entities,
            missing_fields=[str(f) for f in missing if str(f).strip()]
            if isinstance(missing, list)
            else [],
            need_tool=bool(raw.get("need_tool", intent in ("query", "mutate", "orchestrate"))),
            need_clarification=need_clarification,
            clarification_message=str(raw.get("clarification_message") or "").strip(),
            risk_level=risk,
            reason=str(raw.get("reason") or ""),
            backend=backend,
        )

    @classmethod
    def from_plain_intent(cls, intent: Intent, text: str, *, backend: str = "") -> IntentAnalysis:
        """旧式四分类结果包装为结构化分析（降级链兼容层）。"""
        return cls(
            intent=intent,
            rewritten_query=text,
            intent_category=(
                "chat"
                if intent == "chitchat"
                else "task_execution"
                if intent == "mutate"
                else "multi_step_task"
                if intent == "orchestrate"
                else "knowledge_qa"
            ),
            need_tool=intent in ("query", "mutate", "orchestrate"),
            backend=backend,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "rewritten_query": self.rewritten_query,
            "intent_category": self.intent_category,
            "confidence": self.confidence,
            "entities": self.entities,
            "missing_fields": self.missing_fields,
            "need_tool": self.need_tool,
            "need_clarification": self.need_clarification,
            "clarification_message": self.clarification_message,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "backend": self.backend,
        }
