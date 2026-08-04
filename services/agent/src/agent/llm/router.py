"""LMRouter —— 按任务复杂度挑选合适的后端。

路由规则：
    - 简单 / 数据敏感类任务（intent / repair）→ 走 Ollama（mock 模式下走 mock）
    - 复杂任务（plan / summarise）→ 优先内部 LLM，未配置则回退 Ollama
    - mock 模式：所有任务都走内置规则引擎，无需任何外部服务

Phase 2C 升级：每种 task kind 用 Fallback 链包装：
    intent    : mock → ollama → private
    repair    : ollama → private → mock
    plan      : private → ollama → mock
    summarise : private → ollama → mock

Phase 4 V0 升级：端侧模型 + 推理模式
    正常模式 (inference_mode="normal"):
        intent    : local_small → ollama → mock
        plan      : local_small → ollama → private → mock
        summarise : ollama → private → mock          (端侧不执行)
        repair    : ollama → mock                     (端侧不修复)
    性能模式 (inference_mode="performance"):
        intent    : ollama → mock
        plan      : ollama → private → mock
        summarise : ollama → private → mock
        repair    : ollama → mock
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from langchain_core.messages import BaseMessage

from agent.config import settings
from agent.llm.ollama import OllamaClient, OllamaUnavailableError
from agent.llm.private_llm import PrivateLLMClient
from agent.llm.mock import MockLLMClient
from agent.llm.local_small import LocalSmallLLMClient
from agent.llm.types import Intent, TaskKind
from agent.llm.fallback import (
    FallbackResult,
    LLMBackendError,
    LLMRateLimitError,
    LLMUnavailableError,
    with_fallback,
)
from agent.prompts import (
    DYNAMIC_TOOL_ORCHESTRATOR_PROMPT,
    SUBAGENT_ENABLEMENT_DECISION_PROMPT,
)


logger = logging.getLogger(__name__)


# Re-exported for backward-compat with nodes/* and tests/* that import from here
__all__ = ["Intent", "TaskKind", "LMRouter", "FallbackResult"]


def _load_max_context_from_db() -> tuple[int | None, int | None]:
    """从 router.db 同步读 ollama + private 后端的 max_context 配置。

    Returns:
        (ollama_max_ctx, private_max_ctx) —— 都可能为 None（未配置时）。

    失败/无表/无行时返回 (None, None) —— 让客户端走模型默认窗口。
    该函数不抛异常（启动期不应被一个 SQLite 错误阻塞 LLM 初始化）。

    为什么用 sync sqlite3 而不是 aiosqlite：
        LMRouter 在 FastAPI lifespan 内被构造（事件循环已运行），
        aiosqlite 会与运行中的 loop 冲突。同步 sqlite3 + 5ms 超时是更安全的选择。
    """
    import sqlite3
    try:
        db_path = settings.llm_router_db_path
        # 确保 schema 存在（首次访问由 storage.upsert_backend 时建表，
        # 但 LMRouter 可能先于第一次 upsert 被构造 → 试建表）
        try:
            schema_path = Path(__file__).parent / "schema.sql"
            conn = sqlite3.connect(db_path, timeout=5)
            try:
                try:
                    conn.executescript(schema_path.read_text(encoding="utf-8"))
                    conn.commit()
                except sqlite3.OperationalError:
                    pass  # 表已存在
                cur = conn.execute(
                    "SELECT model_name, type, max_context FROM llm_backends WHERE enabled=1"
                )
                ollama_ctx: int | None = None
                private_ctx: int | None = None
                for model_name, btype, max_ctx in cur.fetchall():
                    if btype == "local" and model_name == settings.ollama_model:
                        ollama_ctx = max_ctx
                    elif btype == "private" and model_name == (settings.private_llm_model or ""):
                        private_ctx = max_ctx
                return ollama_ctx, private_ctx
            finally:
                conn.close()
        except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError):
            return None, None
    except Exception:
        return None, None


# Phase 12 V2：编排决策模式（与「子智能体启用决策提示词」一致）
_DECISION_MODES = frozenset({
    "MAIN_AGENT", "TOOL_ONLY", "SINGLE_SUBAGENT", "MULTI_SUBAGENT",
    "ASK_USER", "REFUSE",
})


def _fallback_decision(plan: list[dict]) -> dict:
    """保守兜底：不启用子智能体；有工具计划走 TOOL_ONLY，否则主智能体直接回答。"""
    mode = "TOOL_ONLY" if plan else "MAIN_AGENT"
    return {
        "decision": {
            "mode": mode,
            "should_enable_subagent": False,
            "execution_allowed": True,
            "user_confirmation_required": False,
            "confidence": 0.0,
            "reason": "fallback: single-agent (conservative)",
            "clarifying_questions": [],
            "confirmation_message": None,
            "refusal_message": None,
        },
        "scoring": {},
        "selected_subagents": [],
        "tool_calls": [],
        "plan": [],
        "fallback": "single-agent fallback",
        "_fallback": True,
    }


def _parse_orchestration_decision(text: str) -> dict | None:
    """解析并校验编排决策 JSON（严格模式：格式或一致性不合规 → None）。"""
    if not text:
        return None
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    decision = data.get("decision")
    if not isinstance(decision, dict):
        return None
    mode = decision.get("mode")
    if mode not in _DECISION_MODES:
        return None

    selected = data.get("selected_subagents")
    subagents = (
        [s for s in selected if isinstance(s, dict)]
        if isinstance(selected, list) else []
    )
    # 模式 ↔ 子智能体数量一致性（§9.8）
    if mode == "SINGLE_SUBAGENT" and len(subagents) != 1:
        return None
    if mode == "MULTI_SUBAGENT" and len(subagents) < 2:
        return None
    if mode in ("MAIN_AGENT", "TOOL_ONLY", "ASK_USER", "REFUSE"):
        subagents = []

    raw_questions = decision.get("clarifying_questions")
    questions = (
        [str(q) for q in raw_questions if isinstance(q, str) and q.strip()]
        if isinstance(raw_questions, list) else []
    )
    if mode == "ASK_USER" and not questions:
        return None

    confirmation = decision.get("user_confirmation_required") is True
    confirmation_message = str(decision.get("confirmation_message") or "")
    if confirmation and not confirmation_message.strip():
        return None

    refusal = str(decision.get("refusal_message") or "")
    if mode == "REFUSE" and not refusal.strip():
        return None

    return {
        "decision": {
            "mode": mode,
            "should_enable_subagent": mode in ("SINGLE_SUBAGENT", "MULTI_SUBAGENT"),
            "execution_allowed": decision.get("execution_allowed") is True,
            "user_confirmation_required": confirmation,
            "confidence": float(decision.get("confidence") or 0.0),
            "reason": str(decision.get("reason") or ""),
            "clarifying_questions": questions,
            "confirmation_message": confirmation_message,
            "refusal_message": refusal,
        },
        "scoring": data.get("scoring") if isinstance(data.get("scoring"), dict) else {},
        "selected_subagents": subagents,
        "tool_calls": data.get("tool_calls") if isinstance(data.get("tool_calls"), list) else [],
        "plan": data.get("plan") if isinstance(data.get("plan"), list) else [],
        "fallback": str(data.get("fallback") or ""),
    }


def _conversation_summary(history: list) -> str:
    """把最近几轮对话压缩成决策器可用的摘要文本。"""
    lines: list[str] = []
    for message in history[-6:]:
        if isinstance(message, dict):
            role = str(message.get("role") or "user")
            content = str(message.get("content") or "")[:200]
        else:
            role = str(getattr(message, "role", "user"))
            content = str(getattr(message, "content", "") or "")[:200]
        lines.append(f"{role}: {content}")
    return "\n".join(lines)[:1500]


def _compact_messages(messages: list) -> list[dict]:
    """把最近几轮对话压成 [{role, content}]，供动态工具编排器使用。"""
    out: list[dict] = []
    for message in messages[-6:]:
        if isinstance(message, dict):
            out.append({
                "role": str(message.get("role") or "user"),
                "content": str(message.get("content") or "")[:200],
            })
        else:
            out.append({
                "role": str(getattr(message, "role", "user")),
                "content": str(getattr(message, "content", "") or "")[:200],
            })
    return out


_ORCHESTRATION_ACTIONS = frozenset({
    "SELECT_TOOLS", "TOOL_CALLS", "REQUEST_FULL_TOOLS", "ASK_USER", "FINAL_ANSWER",
})


def _fallback_orchestrate_action() -> dict:
    """保守兜底：不加载、不调用任何工具，直接结束（由 responder 给出保守回答）。"""
    return {
        "action": "FINAL_ANSWER",
        "reason": "fallback: no tool orchestration (conservative)",
        "confidence": 0.0,
        "selected_tool_names": [],
        "desired_capabilities": [],
        "missing_capability": "",
        "tool_calls": [],
        "final_answer": "",
        "ask_user_message": "",
        "need_full_toolset": False,
        "_fallback": True,
    }


def _parse_orchestration_action(
    text: str,
    *,
    summary_names: set[str],
    registered_names: set[str],
    full_loaded: bool,
    max_selected: int,
) -> dict | None:
    """解析并校验动态工具编排动作 JSON（严格模式：违规一律返回 None → 保守兜底）。"""
    if not text:
        return None
    cleaned = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL)
    if fence:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    action = data.get("action")
    if action not in _ORCHESTRATION_ACTIONS:
        return None
    reason = str(data.get("reason") or "")
    confidence = max(0.0, min(1.0, float(data.get("confidence") or 0.0)))
    selected = data.get("selected_tool_names")
    selected_names = (
        [str(n) for n in selected if isinstance(n, str) and n.strip()]
        if isinstance(selected, list) else []
    )
    tool_calls = data.get("tool_calls")
    calls = (
        [c for c in tool_calls if isinstance(c, dict)]
        if isinstance(tool_calls, list) else []
    )

    if action == "SELECT_TOOLS":
        if not selected_names:
            return None
        if any(n not in summary_names for n in selected_names):
            return None  # 不能编造 / 不能选摘要之外的工具
        return {
            "action": action,
            "reason": reason,
            "confidence": confidence,
            "selected_tool_names": selected_names[:max_selected],
            "desired_capabilities": _str_list(data.get("desired_capabilities")),
            "missing_capability": str(data.get("missing_capability") or ""),
            "tool_calls": [],
            "final_answer": "",
            "ask_user_message": "",
            "need_full_toolset": False,
        }

    if action == "TOOL_CALLS":
        if not calls:
            return None
        seen_ids: set[str] = set()
        normalised: list[dict] = []
        for call in calls:
            call_id = str(call.get("id") or "")
            name = str(call.get("name") or "")
            arguments = call.get("arguments")
            if not call_id or call_id in seen_ids:
                return None  # id 必须唯一
            if name not in registered_names:
                return None  # 不能调用未注册工具
            if not isinstance(arguments, dict):
                return None  # 参数必须是对象
            seen_ids.add(call_id)
            normalised.append({
                "id": call_id,
                "name": name,
                "arguments": arguments,
                "purpose": str(call.get("purpose") or ""),
            })
        return {
            "action": action,
            "reason": reason,
            "confidence": confidence,
            "selected_tool_names": [],
            "desired_capabilities": [],
            "missing_capability": "",
            "tool_calls": normalised,
            "final_answer": "",
            "ask_user_message": "",
            "need_full_toolset": False,
        }

    if action == "REQUEST_FULL_TOOLS":
        if full_loaded:
            return None  # 全量已加载不得再请求
        return {
            "action": action,
            "reason": reason,
            "confidence": confidence,
            "selected_tool_names": [],
            "desired_capabilities": _str_list(data.get("desired_capabilities")),
            "missing_capability": str(data.get("missing_capability") or ""),
            "tool_calls": [],
            "final_answer": "",
            "ask_user_message": "",
            "need_full_toolset": True,
        }

    if action == "ASK_USER":
        message = str(data.get("ask_user_message") or "")
        if not message.strip():
            return None
        return {
            "action": action,
            "reason": reason,
            "confidence": confidence,
            "selected_tool_names": [],
            "desired_capabilities": _str_list(data.get("desired_capabilities")),
            "missing_capability": str(data.get("missing_capability") or ""),
            "tool_calls": [],
            "final_answer": "",
            "ask_user_message": message,
            "need_full_toolset": False,
        }

    # FINAL_ANSWER
    return {
        "action": action,
        "reason": reason,
        "confidence": confidence,
        "selected_tool_names": [],
        "desired_capabilities": [],
        "missing_capability": "",
        "tool_calls": [],
        "final_answer": str(data.get("final_answer") or ""),
        "ask_user_message": "",
        "need_full_toolset": False,
    }


def _str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(v) for v in value if isinstance(v, str) and v.strip()]


# Tasks that *must* run locally — they may receive sensitive payloads (raw DB
# rows, SQL errors, secrets accidentally echoed in tool output).
_LOCAL_ONLY_TASKS: frozenset[TaskKind] = frozenset({
    "intent", "repair", "skill_router", "data_summary",
    # Phase 12 V2 (2026-08-03): 多智能体自动分解判定会接触用户原始任务内容
    # （可能含 SQL / PII / 敏感业务描述）→ 强制本地，绝不外发
    "decompose",
    # 动态工具路由：决策输入含用户请求 + 历史工具结果（可能含敏感数据）→ 本地
    "tool_orchestrate",
    # Phase 2G V1.1 (2026-07-28): 业务功能点提取走 Ollama（红线）
    "biznav_extract",
    # Phase 4 V0: 本地端侧任务（截图含敏感 UI，意图分类敏感）
    "local_intent",
    "vision_understand",
    # Phase 2F+ V1 (2026-07-29): 日志级别分类走本地 0.3B（日志可能含 PII / 内网 IP / 业务敏感词）
    # 注：log_root_cause 不在此处 —— 走内网 LLM（已脱敏）
    "log_level_classify",
    # Phase 1B V1 (2026-07-30): 原生工具产出物可能携带敏感上下文
    # builtin_tool_summary —— 工具结果汇总（路径 / 内容片段 / error 串可能含敏感信息）
    # builtin_search_summarize —— 搜索结果聚合（同上）
    "builtin_tool_summary",
    "builtin_search_summarize",
    # Phase 14 V0 (2026-07-31): 图像处理 OCR 文本可能含敏感信息（身份证 / 银行卡 / 合同金额）
    "image_processing_summary",
    # Phase 2B V0 (2026-07-31): SSH 命令输出可能含敏感信息（系统配置 / 数据库连接串 / 业务数据）
    "ssh_command_summary",
    # Phase 7 V0 (2026-07-31): 数据专家敏感任务（表结构 + 字段注释 + 数据样本可能含 PII）
    "schema_link",      # 表结构 + 字段注释可能敏感
    "chart_reco",       # 图表推荐会看数据样本（可能含 PII）
    # "data_summary" 已在上方（Phase 12 时加入）
})


def _is_mock_mode() -> bool:
    """`EAIDE_LLM_BACKEND=mock` 走内置 mock 后端（不调外部 LLM）。

    ⚠️ 生产环境严禁此模式。mock 模式会绕过 _LOCAL_ONLY_TASKS 的安全约束，
    所有任务（包括 intent / repair）静默走 mock，不产生任何真实 LLM 调用。
    """
    return os.environ.get("EAIDE_LLM_BACKEND", "").lower() == "mock"


def _classify_http_error(exc: Exception) -> type[LLMBackendError]:
    """把 httpx / 解析错误映射到 LLMBackendError 子类。"""
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code == 429:
            return LLMRateLimitError
        if exc.response.status_code >= 500:
            return LLMUnavailableError
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, OllamaUnavailableError)):
        return LLMUnavailableError
    if isinstance(exc, (ValueError, json_err())):
        from agent.llm.fallback import LLMParseError
        return LLMParseError
    return LLMBackendError


def json_err():
    """延迟引用避免循环（json.JSONDecodeError 是类型不是值）"""
    import json
    return json.JSONDecodeError


# ---- Phase 4 V0: 推理模式感知的 chain 构造器 --------------------------------


def _build_intent_chain(
    router: "LMRouter",
    engine_chain: list[str] | None,
) -> list[tuple[str, callable]]:
    """构造 intent 任务的 fallback 链。

    正常模式 + 无 engine: local_small → ollama → mock
    性能模式 + 无 engine: ollama → mock
    有 engine_chain 时: engine 决定顺序（V2.5），但正常模式在前面加 local_small
    """
    chain: list[tuple[str, callable]] = []

    # 正常模式：端侧模型优先（仅在无 engine 覆盖时）
    if router.inference_mode == "normal" and not engine_chain:
        chain.append(("local_small", lambda: _local_small_intent(router)))

    if engine_chain:
        for backend_name in engine_chain:
            if backend_name == "ollama":
                chain.append(("ollama", lambda: router._ollama_intent()))
            elif backend_name == "private" and router.private is not None:
                chain.append(("private", lambda: router._private_intent()))
            elif backend_name == "local_small":
                chain.append(("local_small", lambda: _local_small_intent(router)))
    else:
        chain.append(("ollama", lambda: router._ollama_intent()))

    # Mock 兜底
    chain.append(("mock", lambda: router.mock.classify_intent("")))
    return chain


def _build_plan_chain(
    router: "LMRouter",
    engine_chain: list[str] | None,
) -> list[tuple[str, callable]]:
    """构造 plan 任务的 fallback 链。

    正常模式 + 无 engine 覆盖: local_small → ollama → private → mock
    性能模式 + 无 engine 覆盖: ollama → private → mock
    有 engine_chain 时: engine 决定顺序（V2.5），但正常模式在前面加 local_small
    内网模型(private) = 云端模型地位
    """
    chain: list[tuple[str, callable]] = []

    # 正常模式：端侧模型优先列计划（仅在无 engine 覆盖时，或 engine_chain 不包含 local_small）
    if router.inference_mode == "normal" and not engine_chain:
        chain.append(("local_small", lambda: _local_small_plan(router)))

    if engine_chain:
        # V2.5: engine 五维评分决定顺序
        for backend_name in engine_chain:
            if backend_name == "ollama":
                chain.append(("ollama", lambda: router._ollama_dummy()))
            elif backend_name == "private" and router.private is not None:
                chain.append(("private", lambda: router._private_dummy()))
            elif backend_name == "local_small":
                chain.append(("local_small", lambda: _local_small_plan(router)))
    else:
        # 硬编码 fallback
        chain.append(("ollama", lambda: router._ollama_dummy()))
        if router.private is not None:
            chain.append(("private", lambda: router._private_dummy()))

    # Mock 兜底
    chain.append(("mock", lambda: router.mock_dispatch("plan")))
    return chain


async def _local_small_intent(router: "LMRouter"):
    """调 local_small.classify_intent，把 unavailable 翻译成异常。"""
    from agent.llm.local_small import LocalSmallUnavailableError
    try:
        return await router.local_small.classify_intent("__probe__")
    except LocalSmallUnavailableError as e:
        raise LLMBackendError(str(e)) from e


async def _local_small_plan(router: "LMRouter"):
    """调 local_small.plan，把 unavailable 翻译成异常。"""
    from agent.llm.local_small import LocalSmallUnavailableError
    raise LLMBackendError("local_small plan proxy — 端侧只列大纲，具体执行交云端")


class LMRouter:
    """多后端：mock / ollama / private，按 task kind 选取，并支持 Fallback 链。

    使用示例（向后兼容老 API）：
        router = LMRouter()
        intent = await router.classify_intent("查询订单")
        # ↑ 内部实际跑了 fallback 链，但返回的是最终值

    进阶：想拿到降级轨迹？
        router = LMRouter()
        result = await router.classify_intent_with_fallback("查询订单")
        # ↑ result.attempts / result.trail 可写审计

    V2 增量：
        - `__init__(engine=None)` 接受可选 RouterEngine 注入（V1.5 兼容 None）
        - 4 公开方法加 `spark_enabled=None` keyword（向后兼容，默认 None = V1.5 行为）
        - spark_enabled=True 时调 engine.spark_route()（V2.0 是 placeholder，真 LLM
          串联 V2.5 接 llama.cpp / Ollama 真实推理）
    """

    def __init__(self, engine=None) -> None:
        # V2 增量：engine 注入（默认 None 走 V1.5 兼容路径）
        # engine_api.py::_get_engine() 在 V2 会传 RouterEngine 实例进来
        self._engine = engine
        # V2 增量：Spark 模式开关（前端 toggle 调 set_spark_mode 改这里）
        # 保持 4 公开方法 keyword 签名完全冻结（test_router_backcompat 锁）
        self._spark_mode = getattr(engine, "spark_enabled", False) if engine else False
        self.mock = MockLLMClient()
        # 从 router.db 读后端配置（含 max_context）；失败走 settings 默认
        self._ollama_max_ctx, self._private_max_ctx = _load_max_context_from_db()
        self.ollama = OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            max_context=self._ollama_max_ctx,
        )
        self.private = (
            PrivateLLMClient(
                base_url=settings.private_llm_base_url,
                api_key=settings.private_llm_api_key,
                model=settings.private_llm_model or "",
                max_context=self._private_max_ctx,
            )
            if settings.private_llm_base_url and settings.private_llm_api_key
            else None
        )
        # Phase 4 V0：本地端侧模型（文本 / 视觉 / embedding）
        self.local_small = LocalSmallLLMClient(
            base_url=settings.local_small_base_url,
            model=settings.local_small_model,
        )
        # Phase 4 V0：推理模式 —— "normal"（端侧优先）或 "performance"（直走云端）
        self._inference_mode: Literal["normal", "performance"] = "normal"
        self._mock_mode = _is_mock_mode()

    def reload_max_context(self) -> None:
        """热重载 max_context：在 Settings → 模型管理保存新值后调一次即可生效，无需重启 Agent。

        同步读 router.db，更新两个 client 实例的 max_context 字段。
        失败保持原值不报错。
        """
        ollama_ctx, private_ctx = _load_max_context_from_db()
        if ollama_ctx is not None:
            self.ollama.max_context = ollama_ctx
        if private_ctx is not None and self.private is not None:
            self.private.max_context = private_ctx

    # ---- V2 增量：Spark 模式 toggle（保持 4 公开方法 keyword 签名冻结） ----

    def set_spark_mode(self, enabled: bool) -> None:
        """运行时切换 Spark 模式（前端 RouterDashboard toggle 直连）。

        True 时 4 公开方法内部走 engine.spark_route()（V2.0 placeholder）；
        False 时回 V1.5 行为（chain_for + with_fallback）。
        """
        self._spark_mode = enabled
        if self._engine is not None:
            self._engine.set_spark_enabled(enabled)
        logger.info("router_spark_toggle enabled=%s", enabled)

    @property
    def spark_mode(self) -> bool:
        return self._spark_mode

    # ---- Phase 4 V0：推理模式 toggle ---------------------------------------

    @property
    def inference_mode(self) -> Literal["normal", "performance"]:
        return self._inference_mode

    def set_inference_mode(self, mode: Literal["normal", "performance"]) -> None:
        """切换推理模式。

        normal:      简单任务（intent/plan）端侧优先 → 失败回退 Ollama/云端
        performance: 所有任务直走 Ollama/云端（跳过端侧）
        """
        if mode not in ("normal", "performance"):
            raise ValueError(f"invalid inference_mode: {mode}")
        self._inference_mode = mode
        logger.info("router_inference_mode mode=%s", mode)

    # ---- Chain construction -------------------------------------------------

    def _chain_for(self, kind: TaskKind) -> list[tuple[str, callable]]:
        """构造 (backend_name, callable) 链。

        V2.5 增量（Phase 2C V2.5 收尾）：
            - 若 engine 可用 + kind 不在 _LOCAL_ONLY_TASKS 且 kind 不是 'mock_mode'，
              先调 engine.route_request() 拿五维评分决定的 fallback_chain，
              用此顺序覆盖硬编码链。
            - 硬编码 fallback（V1.5）作为兜底：engine 不可用时退回原逻辑。
            - mock 模式压倒：永远只走 mock（语义等价，但保留链便于测试降级提示）
        """
        if self._mock_mode:
            return [("mock", lambda: self.mock_dispatch(kind))]

        # V2.5 增量：让 engine.route_request 五维评分真影响 chain 顺序
        engine_chain: list[str] | None = None
        if self._engine is not None and kind not in _LOCAL_ONLY_TASKS:
            try:
                # sensitivity 用 LOW（不影响 _LOCAL_ONLY_TASKS hard_rules）
                decision = self._engine.route_request(
                    task_kind=kind,
                    category="balanced",  # V2.5 简化：固定 balanced
                    sensitivity="low",
                    request_id=f"chain-{kind}-{hash((kind,))}",
                )
                # 仅在 engine 给出非空 primary 时采用
                if decision.actual_backend:
                    engine_chain = [decision.actual_backend] + [
                        n for n in decision.fallback_chain if n != decision.actual_backend
                    ]
                    logger.debug(
                        "router_engine_chain kind=%s engine_chain=%s",
                        kind, engine_chain,
                    )
            except Exception as e:
                # engine 决策失败不阻塞（V1.5 兜底）
                logger.debug("router_engine_route_failed kind=%s err=%s", kind, e)

        chain: list[tuple[str, callable]] = []

        if kind == "intent":
            # Phase 4 V0: 正常模式端侧优先
            chain = _build_intent_chain(self, engine_chain)
        elif kind == "repair":
            order = engine_chain or ["ollama", "private"]
            for backend_name in order:
                if backend_name == "ollama":
                    chain.append(("ollama", lambda: self._ollama_repair_dummy()))
                elif backend_name == "private" and self.private is not None:
                    chain.append(("private", lambda: self._private_repair_dummy()))
            chain.append(("mock", lambda: self.mock.repair_call(original={}, error="", history=[])))

        elif kind in ("plan", "summarise"):
            # Phase 4 V0: plan 正常模式端侧优先；summarise 不做端侧
            if kind == "plan":
                chain = _build_plan_chain(self, engine_chain)
            else:
                order = engine_chain or ["private", "ollama"]
                for backend_name in order:
                    if backend_name == "private" and self.private is not None:
                        chain.append(("private", lambda: self._private_dummy()))
                    elif backend_name == "ollama":
                        chain.append(("ollama", lambda: self._ollama_dummy()))
                chain.append(("mock", lambda: self.mock_dispatch(kind)))

        else:
            chain.append(("mock", lambda: self.mock_dispatch(kind)))

        return chain

    def mock_dispatch(self, kind: TaskKind):
        """根据 kind 返回对应 mock 方法的协程。"""
        if kind == "intent":
            return self.mock.classify_intent("")
        if kind == "repair":
            return self.mock.repair_call(original={}, error="", history=[])
        if kind == "plan":
            return self.mock.plan(intent="query", user_prompt="", history=[], tool_specs=[])
        if kind == "summarise":
            return self.mock.summarise(intent="query", user_prompt="", plan=[], results=[])
        raise ValueError(f"unknown kind: {kind}")

    # ---- "Raise" 包装：把客户端内部静默降级转换成异常 -----------------------

    async def _ollama_intent(self):
        """调 Ollama.classify_intent，但把 unavailable 翻译成异常。"""
        import asyncio
        try:
            result = await self.ollama.classify_intent("__probe__")
            return result
        except OllamaUnavailableError as e:
            raise LLMBackendError(str(e)) from e

    async def _private_intent(self):
        """调 Private.classify_intent。私有 LLM 内部已 try/except 兜底返回 query，
        这里要做的是：如果连 HTTP 都打不通（连接拒绝/超时），切下一级。

        私有 LLM 的内部 try/except 会捕获所有错误 → 总是返回 "query"，
        这种情况下 fallback 链不会触发 —— 这是已知取舍（Phase 2C 后续：让私有 LLM
        也支持 raise_errors=True 模式）。
        """
        if self.private is None:
            raise LLMBackendError("private LLM not configured")
        return await self.private.classify_intent("__probe__")

    async def _ollama_repair_dummy(self):
        raise LLMBackendError("Phase 2C: repair chain 当前为占位")

    async def _private_repair_dummy(self):
        raise LLMBackendError("Phase 2C: repair chain 当前为占位")

    async def _ollama_dummy(self):
        raise LLMBackendError("Phase 2C: plan/summarise chain 当前为占位")

    async def _private_dummy(self):
        raise LLMBackendError("Phase 2C: plan/summarise chain 当前为占位")

    # ---- Backward-compatible API (原有调用方无感) --------------------------

    def pick(self, kind: TaskKind):
        """旧 API：直接返回单个 client。已弃用但保留兼容。

        新代码请用 classify_intent_with_fallback / plan_with_fallback / 等。
        """
        if self._mock_mode:
            return self.mock
        if kind in _LOCAL_ONLY_TASKS:
            return self.ollama
        if self.private is not None:
            return self.private
        return self.ollama

    async def route(self, *, task: str, prompt: str) -> str:
        """Phase 12 V1 orchestrator 入口：按 task 类型路由 + 执行。

        返回 LLM 生成的文本；失败时抛 LLMBackendError。
        """
        client = self.pick(task)
        result, _ = await client.summarise(
            intent="query",
            user_prompt=prompt,
            plan=[],
            results=[],
        )
        return result

    async def classify_intent(self, text: str) -> Intent:
        """向后兼容：返回最终值。降级过程静默（不抛异常）。

        V2 增量：当 `self._spark_mode=True` 时调 engine.spark_route（V2.0 placeholder）。
        保持 keyword 签名完全冻结（test_router_backcompat 锁）。
        """
        if self._spark_mode and self._engine is not None:
            await self._engine.spark_route(
                task_kind="intent",
                user_prompt=text,
                history=[],
                tool_specs=[],
                request_id=f"intent-{hash((text,))}",
            )
            # Spark V0 placeholder：固定返回 query（最常见 Intent）
            return "query"
        result = await self.classify_intent_with_fallback(text)
        if result.final_status == "ok":
            return result.value  # type: ignore[return-value]
        # 全失败兜底为 query（最常见，最安全）
        return "query"

    async def plan(
        self,
        *,
        intent: Intent,
        user_prompt: str,
        history: list[BaseMessage],
        tool_specs: list[dict],
    ) -> tuple[list[dict], str]:
        """Return (plan, plan_explanation).

        V2 增量：当 `self._spark_mode=True` 时调 engine.spark_route（V2.0 placeholder）。
        """
        if self._spark_mode and self._engine is not None:
            decision = await self._engine.spark_route(
                task_kind="plan",
                user_prompt=user_prompt,
                history=history,
                tool_specs=tool_specs,
                request_id=f"plan-{hash((user_prompt,))}",
            )
            return ([], "[Spark V0 placeholder] reasoning=" + str(decision.spark_reasoning_backend))
        return await self.pick("plan").plan(
            intent=intent,
            user_prompt=user_prompt,
            history=history,
            tool_specs=tool_specs,
        )

    async def repair_call(
        self,
        *,
        original: dict,
        error: str,
        history: list[BaseMessage],
    ) -> dict:
        """Re-generate a failed tool call's arguments given the error.

        V2 增量：当 `self._spark_mode=True` 时调 engine.spark_route（V2.0 placeholder）。
        """
        if self._spark_mode and self._engine is not None:
            decision = await self._engine.spark_route(
                task_kind="repair",
                user_prompt=str(error),
                history=history,
                tool_specs=[],
                request_id=f"repair-{hash((error,))}",
            )
            return {**original, "_spark_trace": decision.spark_draft}
        return await self.pick("repair").repair_call(
            original=original,
            error=error,
            history=history,
        )

    async def summarise(
        self,
        *,
        intent: Intent,
        user_prompt: str,
        plan: list[dict],
        results: list[dict],
    ) -> tuple[str, list[str]]:
        """Return (final_answer, sources_referenced).

        V2 增量：当 `self._spark_mode=True` 时调 engine.spark_route（V2.0 placeholder）。
        """
        if self._spark_mode and self._engine is not None:
            decision = await self._engine.spark_route(
                task_kind="summarise",
                user_prompt=user_prompt,
                history=[],
                tool_specs=[],
                request_id=f"summarise-{hash((user_prompt, plan))}",
            )
            return ("[Spark V0 placeholder] execution=" + str(decision.spark_execution_backend), [])
        return await self.pick("summarise").summarise(
            intent=intent,
            user_prompt=user_prompt,
            plan=plan,
            results=results,
        )

    async def decompose(
        self,
        *,
        user_prompt: str,
        plan: list[dict],
        history: list[BaseMessage],
        available_subagents: list[dict] | None = None,
        available_tools: list[dict] | None = None,
        user_permissions: dict | None = None,
        cost_latency_policy: dict | None = None,
        safety_policy: dict | None = None,
    ) -> dict:
        """编排决策器：自动判断任务由谁执行（Phase 12 V2）。

        Returns:
            完整决策 JSON：
            decision.mode ∈ {MAIN_AGENT, TOOL_ONLY, SINGLE_SUBAGENT, MULTI_SUBAGENT,
            ASK_USER, REFUSE}，附 scoring / selected_subagents / tool_calls / plan / fallback。

        保守策略：mock 模式 / LLM 不可用 / 输出无法解析 → 返回 `_fallback=True` 的
        单 Agent 决策（调用方按单 Agent 路径执行）。
        判定接触用户原始内容 → task 固定走 `decompose`（_LOCAL_ONLY_TASKS 本地红线）。
        """
        if self._mock_mode:
            return _fallback_decision(plan)
        prompt = (
            SUBAGENT_ENABLEMENT_DECISION_PROMPT
            .replace("{{CURRENT_TIME}}", datetime.now(timezone.utc).isoformat(timespec="seconds"))
            .replace("{{USER_INPUT}}", user_prompt[:4000])
            .replace("{{CONVERSATION_SUMMARY}}", _conversation_summary(history))
            .replace(
                "{{AVAILABLE_SUBAGENTS}}",
                json.dumps(available_subagents or [], ensure_ascii=False),
            )
            .replace(
                "{{AVAILABLE_TOOLS}}",
                json.dumps(available_tools or [], ensure_ascii=False)[:3000],
            )
            .replace(
                "{{USER_PERMISSIONS}}",
                json.dumps(user_permissions or {}, ensure_ascii=False),
            )
            .replace(
                "{{COST_LATENCY_POLICY}}",
                json.dumps(cost_latency_policy or {}, ensure_ascii=False),
            )
            .replace(
                "{{SAFETY_POLICY}}",
                json.dumps(safety_policy or {}, ensure_ascii=False),
            )
        )
        try:
            text = await self.route(task="decompose", prompt=prompt)
            decision = _parse_orchestration_decision(text)
            if decision is not None:
                return decision
            logger.warning(
                "decompose output unparseable, fallback single-agent: %.200s", text,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "decompose LLM call failed, fallback single-agent: %s", exc,
            )
        return _fallback_decision(plan)

    async def orchestrate_tools(
        self,
        *,
        load_stage: str,
        user_input: str,
        messages: list,
        tool_summaries: list[dict],
        registered_tools: list[dict],
        full_toolset_loaded: bool,
        tool_results: list[dict],
        max_selected_tools: int = 5,
        extra_rules: str = "",
        work_mode: str = "",
        autonomy: str = "",
        routing: str = "",
    ) -> dict:
        """动态工具路由与调用编排（一次决策，不包含循环本身）。

        按「动态工具加载与工具调用提示词」输出动作 JSON：
        action ∈ {SELECT_TOOLS, TOOL_CALLS, REQUEST_FULL_TOOLS, ASK_USER, FINAL_ANSWER}。

        保守策略：mock 模式 / LLM 不可用 / 输出无法解析 / 违规动作 → 返回
        `_fallback=True` 的 FINAL_ANSWER（不加载、不调用任何工具）。
        决策输入含用户内容 + 历史工具结果 → task 固定走 `tool_orchestrate`
        （_LOCAL_ONLY_TASKS 本地红线）。
        """
        if self._mock_mode:
            return _fallback_orchestrate_action()
        summary_names = {str(t.get("name")) for t in tool_summaries}
        registered_names = {str(t.get("name")) for t in registered_tools}
        prompt = (
            DYNAMIC_TOOL_ORCHESTRATOR_PROMPT
            .replace("{{LOAD_STAGE}}", str(load_stage))
            .replace("{{USER_INPUT}}", user_input[:4000])
            .replace(
                "{{MESSAGES}}",
                json.dumps(_compact_messages(messages), ensure_ascii=False)[:4000],
            )
            .replace(
                "{{TOOL_SUMMARIES}}",
                json.dumps(tool_summaries, ensure_ascii=False)[:4000],
            )
            .replace(
                "{{REGISTERED_TOOLS}}",
                json.dumps(registered_tools, ensure_ascii=False)[:8000],
            )
            .replace(
                "{{FULL_TOOLSET_LOADED}}",
                "true" if full_toolset_loaded else "false",
            )
            .replace(
                "{{TOOL_RESULTS}}",
                json.dumps(tool_results, ensure_ascii=False)[:6000],
            )
            .replace("{{MAX_SELECTED_TOOLS}}", str(max_selected_tools))
            .replace(
                "{{CURRENT_TIME}}",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            .replace("{{WORK_MODE}}", work_mode or "未知")
            .replace("{{AUTONOMY}}", autonomy or "interactive")
            .replace("{{ROUTING}}", routing or "未知")
            .replace("{{EXTRA_RULES}}", extra_rules or "（无）")
        )
        try:
            text = await self.route(task="tool_orchestrate", prompt=prompt)
            action = _parse_orchestration_action(
                text,
                summary_names=summary_names,
                registered_names=registered_names,
                full_loaded=full_toolset_loaded,
                max_selected=max_selected_tools,
            )
            if action is not None:
                return action
            logger.warning(
                "tool orchestration output unparseable, fallback FINAL_ANSWER: %.200s",
                text,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "tool orchestration LLM call failed, fallback FINAL_ANSWER: %s",
                exc,
            )
        return _fallback_orchestrate_action()

    # ---- 新的 Fallback-aware API ------------------------------------------

    async def classify_intent_with_fallback(self, text: str) -> FallbackResult:
        """分类意图 + 返回降级轨迹。

        当前实现：
            - 仍走原 ollama.classify_intent（向后兼容，内部 try/except 兜底）
            - 但额外提供 chain/trail 让调用方知道走了哪一级
            - 真实多级降级需要客户端支持 raise_errors=True（后续 phase）
        """
        # Phase 2C v1: 仅做 chain 报告 + 兼容 fallback 包装（不影响现有行为）
        chain = [(name, call) for name, call in self._chain_for("intent")]

        async def primary_call() -> str:
            # 走原 path（客户端内部兜底），但从 ollama 开始而不是 mock
            if self._mock_mode:
                return await self.mock.classify_intent(text)
            try:
                return await self.ollama.classify_intent(text)
            except OllamaUnavailableError as e:
                raise LLMBackendError(str(e)) from e

        # 用 with_fallback 包装一层，让 trail 可观测
        # 注意：chain 第一项是 primary_call，后面是兜底
        full_chain: list[tuple[str, callable]] = [("primary", primary_call)]
        if not self._mock_mode and self.private is not None:
            full_chain.append(("private", lambda: self.private.classify_intent(text)))  # type: ignore[union-attr]
        full_chain.append(("mock", lambda: self.mock.classify_intent(text)))

        return await with_fallback(
            chain=full_chain,
            label="intent",
            raise_on_all_fail=False,
        )
