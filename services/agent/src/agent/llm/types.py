"""Shared LLM types — defined here to avoid circular imports between
router.py and ollama.py / private_llm.py.

Phase 2G V1.1 (2026-07-28): TaskKind Literal 扩展加入 `biznav_extract`，
让 `_LOCAL_ONLY_TASKS` 的 frozenset 类型签名与实际值一致（不出现
`frozenset[TaskKind]` 含未声明字面量的"伪类型错误"）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    # 本地知识库 RAG 的 LLM 增强阶段（2026-09-03）：HyDE / 查询扩展 / 上下文前缀。
    # 默认关；启用时走「已启用模型」链（generate_review，同 doc_review 敏感文档姿态）。
    "kb_hyde",
    "kb_expand",
    "kb_contextual",
    # 会话历史压缩（2026-08-17）：LLM 摘要旧对话（含用户原始内容 → 本地红线）
    "history_compress",
    # Phase 19 V0：失败轨迹反思（输入含用户任务内容 → 本地红线）
    "reflection",
    # Phase 19 V1：技能蒸馏 / 主对话终答 Judge（输入含用户内容 → 本地红线）
    "skill_distill",
    "answer_judge",
    # Phase 19 V1.5：Few-shot 影子优化（候选生成 + 影子回放均含用户历史 → 本地红线）
    "prompt_optimize",
    # mock 模式标记（非真实任务，不走 LLM 调度）
    "mock_mode",
]


# ---- 结构化意图分析（意图识别重构 2026-08-06）-------------------------------


class IntentAnalysisSchema(BaseModel):
    """LLM 意图分析输出的 Pydantic 硬校验层（2026-08-31）。

    参考 Instructor/Outlines 思路：用 Schema 强制约束字段类型与枚举，
    替代纯正则/容错解析。校验失败时 from_raw 回退既有宽容降级逻辑，
    行为向后兼容；未知字段丢弃（extra="ignore"）。

    注：intent / intent_category / risk_level 不在此层枚举报错——
    非法值的映射/兜底规则（_CATEGORY_TO_INTENT 等）由 from_raw 统一处理，
    两层职责分离，避免合法新细分类型被硬校验误杀。
    """

    model_config = ConfigDict(extra="ignore")

    rewritten_query: str = ""
    intent: str = ""
    intent_category: str = ""
    confidence: float = Field(default=0.5)
    entities: dict[str, Any] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    need_tool: bool = False
    need_clarification: bool = False
    clarification_message: str = ""
    risk_level: str = "low"
    reason: str = ""


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
    # 操作类细分意图（2026-08-14）：四分类 enum 冻结不动，操作型请求靠
    # 细分类型表达——model_onboard=接入/连接/添加模型端点（通常带模型名+URL），
    # conn_test=测试某地址/模型是否可达。
    "model_onboard",
    "conn_test",
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
    "model_onboard": "mutate",
    "conn_test": "query",
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
        """宽容解析 LLM JSON；非法字段丢弃、缺失字段用安全默认值。

        2026-08-31：先经 IntentAnalysisSchema 硬校验（类型/结构强制）；
        校验失败（如 confidence 为非法字符串）回退原始 dict 宽容处理。
        """
        try:
            data: dict[str, Any] = IntentAnalysisSchema.model_validate(raw).model_dump()
        except (ValidationError, ValueError, TypeError):
            data = raw if isinstance(raw, dict) else {}
        intent = data.get("intent")
        category = str(data.get("intent_category") or "")
        if intent not in ("query", "mutate", "orchestrate", "chitchat"):
            intent = _CATEGORY_TO_INTENT.get(category, "query")
        if category not in _INTENT_CATEGORIES:
            category = "knowledge_qa"
        raw_entities = data.get("entities")
        entities: dict[str, Any] = raw_entities if isinstance(raw_entities, dict) else {}
        missing = data.get("missing_fields")
        confidence_raw = data.get("confidence")
        try:
            confidence = (
                max(0.0, min(1.0, float(confidence_raw))) if confidence_raw is not None else 0.5
            )
        except (TypeError, ValueError):
            confidence = 0.5
        risk = str(data.get("risk_level") or "low")
        if risk not in ("low", "medium", "high", "critical"):
            risk = "low"
        need_clarification = bool(data.get("need_clarification"))
        return cls(
            intent=intent,
            rewritten_query=str(data.get("rewritten_query") or fallback_text).strip()
            or fallback_text,
            intent_category=category,
            confidence=confidence,
            entities=entities,
            missing_fields=[str(f) for f in missing if str(f).strip()]
            if isinstance(missing, list)
            else [],
            need_tool=bool(data.get("need_tool", intent in ("query", "mutate", "orchestrate"))),
            need_clarification=need_clarification,
            clarification_message=str(data.get("clarification_message") or "").strip(),
            risk_level=risk,
            reason=str(data.get("reason") or ""),
            backend=backend,
        )

    @classmethod
    def from_plain_intent(cls, intent: Intent, text: str, *, backend: str = "") -> IntentAnalysis:
        """旧式四分类结果包装为结构化分析（降级链兼容层）。

        置信度策略（2026-08-17）：明确的操作型意图（query / mutate /
        orchestrate，need_tool=True）给 0.6 —— 刚好过 decompose 快速路径门槛
        （confidence >= 0.6），明确操作不需要再花一次编排决策器 LLM 规划；
        此前固定 0.5 低于门槛，本地模型缺席时所有请求都掉进 30s+ 的 LLM
        决策兜底。chitchat 保持 0.5（decompose 节点对闲聊本就前置跳过）。
        """
        need_tool = intent in ("query", "mutate", "orchestrate")
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
            confidence=0.6 if need_tool else 0.5,
            need_tool=need_tool,
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
