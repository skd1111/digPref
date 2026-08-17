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
import time
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import BaseMessage

from agent.config import settings
from agent.llm.cache_l1 import L1Cache
from agent.llm.fallback import (
    FallbackResult,
    LLMBackendError,
    LLMRateLimitError,
    LLMUnavailableError,
    with_fallback,
)
from agent.llm.gen_limits import load_gen_limits
from agent.llm.json_discipline import extract_json
from agent.llm.local_small import LocalSmallLLMClient
from agent.llm.metrics import emit_router_event
from agent.llm.mock import MockLLMClient
from agent.llm.normalize import build_response_cache_key
from agent.llm.ollama import OllamaClient, OllamaUnavailableError
from agent.llm.private_llm import PrivateLLMClient
from agent.llm.prompts import current_time_text
from agent.llm.types import Intent, TaskKind
from agent.prompts import (
    DYNAMIC_TOOL_ORCHESTRATOR_PROMPT,
    SUBAGENT_ENABLEMENT_DECISION_PROMPT,
)

logger = logging.getLogger(__name__)


# ---- 后端连接失败冷却（熔断）-------------------------------------------------
# 本地 Ollama / 内网网关 / 云端后端不可达时，每次调用都要等 TCP 连接超时
# （connect 10s，最长 300s）才失败；chat 链路多个节点反复尝试，前端表现为
# 长时间「执行中」无响应。connect 类失败时把该后端 base_url 标记进冷却，
# 冷却窗口内直接跳过走下一级降级，窗口过后自动恢复重试。
_BACKEND_DOWN_UNTIL: dict[str, float] = {}
_BACKEND_COOLDOWN_SECONDS = 15.0


def _connect_failure(exc: BaseException) -> bool:
    """判断是否 connect 类失败（含被包装的 OllamaUnavailableError）。"""
    import httpx

    cur: BaseException | None = exc
    for _ in range(3):  # 最多解包 3 层 __cause__ / __context__
        if cur is None:
            return False
        if isinstance(cur, (httpx.ConnectError, httpx.ConnectTimeout, ConnectionError)):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def _mark_backend_down(base_url: str) -> None:
    _BACKEND_DOWN_UNTIL[base_url] = time.monotonic() + _BACKEND_COOLDOWN_SECONDS
    logger.warning(
        "backend %s marked down for %.0fs after connect failure",
        base_url,
        _BACKEND_COOLDOWN_SECONDS,
    )


def _backend_down(base_url: str) -> bool:
    until = _BACKEND_DOWN_UNTIL.get(base_url)
    if until is None:
        return False
    if time.monotonic() >= until:
        _BACKEND_DOWN_UNTIL.pop(base_url, None)
        return False
    return True


# Re-exported for backward-compat with nodes/* and tests/* that import from here
__all__ = ["FallbackResult", "Intent", "LMRouter", "TaskKind"]


def load_enabled_local_backend() -> tuple[str, str]:
    """从 router.db 同步读已启用的端侧（local/Ollama）后端配置。

    Returns:
        (base_url, model) —— 模型管理里配了自定义地址/端口时优先用它；
        没有启用的 local 后端或读库失败时回退 settings.ollama_* 默认值。

    与 _load_max_context_from_db 同风格：sync sqlite3、不抛异常
    （启动期不应被一个 SQLite 错误阻塞 LLM 初始化）。
    """
    import sqlite3

    try:
        conn = sqlite3.connect(settings.llm_router_db_path, timeout=5)
        try:
            cur = conn.execute(
                "SELECT base_url, model_name FROM llm_backends "
                "WHERE enabled=1 AND type='local' LIMIT 1"
            )
            row = cur.fetchone()
            if row and (row[0] or "").strip():
                url = row[0].strip().rstrip("/")
                model = (row[1] or "").strip() or settings.ollama_model
                return url, model
        finally:
            conn.close()
    except Exception:
        pass
    return settings.ollama_base_url, settings.ollama_model


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
                # local 后端的 model 以模型管理为准（与 load_enabled_local_backend 同源），
                # 不再要求与 settings.ollama_model 同名，否则自定义模型时 max_context 失配
                local_model = load_enabled_local_backend()[1]
                from agent.llm.gen_limits import default_context_window

                ctx_fallback = default_context_window()
                for model_name, btype, max_ctx in cur.fetchall():
                    if btype == "local" and model_name == local_model:
                        # 行存在但 max_context 未显式设置（NULL）→ 全局默认回退（两级回退）
                        ollama_ctx = max_ctx if max_ctx is not None else ctx_fallback
                    elif btype == "private" and model_name == (settings.private_llm_model or ""):
                        private_ctx = max_ctx if max_ctx is not None else ctx_fallback
                return ollama_ctx, private_ctx
            finally:
                conn.close()
        except (sqlite3.OperationalError, sqlite3.DatabaseError, OSError):
            return None, None
    except Exception:
        return None, None


def _ollama_enabled_from_db() -> bool:
    """端侧 Ollama 是否已配置（BUGFIX #89）—— 未配置就完全不探测。

    判定优先级：
    - router.db.llm_backends 表已建（用过「模型管理」）：看 type='local' 行，
      无 local 行 = 未配置端侧；有则看是否有 enabled=1；
    - 无 db / 无表（纯环境变量用法）：回退 settings.ollama_enabled（默认 True 保持旧行为）。
    任何异常都回退 settings，绝不在启动期抛出。
    """
    import sqlite3

    try:
        conn = sqlite3.connect(settings.llm_router_db_path, timeout=5)
        try:
            cur = conn.execute("SELECT COUNT(*) FROM llm_backends WHERE type='local'")
            total = int(cur.fetchone()[0])
            if total == 0:
                return False  # 表已建但从未配过端侧后端 → 不探测
            cur = conn.execute("SELECT COUNT(*) FROM llm_backends WHERE type='local' AND enabled=1")
            return int(cur.fetchone()[0]) > 0
        finally:
            conn.close()
    except Exception:
        return settings.ollama_enabled


# Phase 12 V2：编排决策模式（与「子智能体启用决策提示词」一致）
_DECISION_MODES = frozenset(
    {
        "MAIN_AGENT",
        "TOOL_ONLY",
        "SINGLE_SUBAGENT",
        "MULTI_SUBAGENT",
        "ASK_USER",
        "REFUSE",
    }
)


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
    data = extract_json(text)
    if not isinstance(data, dict):
        return None

    decision = data.get("decision")
    if not isinstance(decision, dict):
        return None
    mode = decision.get("mode")
    if mode not in _DECISION_MODES:
        return None

    selected = data.get("selected_subagents")
    subagents = [s for s in selected if isinstance(s, dict)] if isinstance(selected, list) else []
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
        if isinstance(raw_questions, list)
        else []
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
            out.append(
                {
                    "role": str(message.get("role") or "user"),
                    "content": str(message.get("content") or "")[:200],
                }
            )
        else:
            out.append(
                {
                    "role": str(getattr(message, "role", "user")),
                    "content": str(getattr(message, "content", "") or "")[:200],
                }
            )
    return out


_ORCHESTRATION_ACTIONS = frozenset(
    {
        "SELECT_TOOLS",
        "TOOL_CALLS",
        "REQUEST_FULL_TOOLS",
        "ASK_USER",
        "FINAL_ANSWER",
    }
)


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
    data = extract_json(text)
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
        if isinstance(selected, list)
        else []
    )
    tool_calls = data.get("tool_calls")
    calls = [c for c in tool_calls if isinstance(c, dict)] if isinstance(tool_calls, list) else []

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
            normalised.append(
                {
                    "id": call_id,
                    "name": name,
                    "arguments": arguments,
                    "purpose": str(call.get("purpose") or ""),
                }
            )
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


# Tasks that *must* run locally first — they may receive sensitive payloads
# (raw DB rows, SQL errors, secrets accidentally echoed in tool output).
# 2026-08-05 调整：语义从「强制本地」放宽为「本地优先」——本地 Ollama 不可用
# （无模型 / 连接失败）时，route() 允许逐级降级到内网 private / 云端 cloud。
_LOCAL_ONLY_TASKS: frozenset[TaskKind] = frozenset(
    {
        "intent",
        "repair",
        "skill_router",
        "data_summary",
        # Phase 12 V2 (2026-08-03): 多智能体自动分解判定会接触用户原始任务内容
        # （可能含 SQL / PII / 敏感业务描述）→ 本地优先，不可用时才降级
        "decompose",
        # 动态工具路由：决策输入含用户请求 + 历史工具结果（可能含敏感数据）→ 本地优先
        "tool_orchestrate",
        # Phase 2G V1.1 (2026-07-28): 业务功能点提取本地优先（红线：本地可用时绝不出外）
        # V1.4 (2026-08-04): biznav/api.py::_make_llm_client 实现逐级降级：
        # 本地 Ollama 不可用 → 内网 private → 云端 cloud（仅提取链路，其他路径仍纯本地）
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
        "schema_link",  # 表结构 + 字段注释可能敏感
        "chart_reco",  # 图表推荐会看数据样本（可能含 PII）
        # "data_summary" 已在上方（Phase 12 时加入）
        # Phase 7 v2.87 (2026-08-13): MetricResolver 抽象层 —— 指标识别含业务字典翻译
        # （可能涉及敏感表结构 / 字段注释 / 客户口径），强制本地 Ollama 优先
        "metric_resolve",
        # 会话历史压缩 (2026-08-17)：旧对话含用户原始内容（可能含 PII / 业务敏感信息）
        # → 本地优先，不可用时才降级（与 intent / decompose 同红线语义）
        "history_compress",
    }
)


_DOC_CLASSIFY_MOCK = (
    '{"doc_category": "contract", "risk_types": ["compliance", "legal"], "reason": "mock"}'
)
_DOC_ANALYZE_MOCK = '{"findings": []}'

# ---- Phase 17 V0: L1 精确响应缓存（模块级单例，跨 LMRouter 重建存活）----
# summarise 是最终答案生成点（最贵的一步）：相同问题 + 相同 plan/results
# 短时间重复调用（用户连点 / 重试 / 重发）直接返回，省一次全链 LLM 调用。
# 红线（docs/design/phase-17-cache-hit-rate.md §3.2）：
#   - 含写工具的 plan 绝不缓存（写操作结果不可复用）；
#   - mock 模式不缓存；凭证 / DSN 不进 key（normalize.py 约束）。
_L1_RESPONSE_CACHE = L1Cache(max_size=256, ttl_sec=300.0)
_L1_ENABLED = True


def set_l1_cache_enabled(enabled: bool) -> None:
    """一键回滚开关（Phase 17）：关闭后 summarise 链路等价无缓存现状。"""
    global _L1_ENABLED
    _L1_ENABLED = bool(enabled)
    logger.info("l1_response_cache_toggle enabled=%s", _L1_ENABLED)


def get_l1_cache() -> L1Cache:
    """暴露 L1 实例（供 cache_stats 统计 / 测试清理）。"""
    return _L1_RESPONSE_CACHE


def is_l1_cache_enabled() -> bool:
    return _L1_ENABLED


def _plan_contains_write(plan: list) -> bool:
    """红线检查：plan 含写工具则结果不可缓存（写操作绝不被缓存复用）。"""
    from agent.safety.write_detector import is_write_call

    for item in plan:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool") or item.get("name") or item.get("tool_name") or "")
        if name and is_write_call({"name": name}):
            return True
    return False


# 原生 function calling 探测缓存哨兵（区别于缓存值 None）
_NATIVE_UNSET = object()


async def _async_text(value: str) -> str:
    return value


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
    router: LMRouter,
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
    router: LMRouter,
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


async def _local_small_intent(router: LMRouter):
    """调 local_small.classify_intent，把 unavailable 翻译成异常。"""
    from agent.llm.local_small import LocalSmallUnavailableError

    try:
        return await router.local_small.classify_intent("__probe__")
    except LocalSmallUnavailableError as e:
        raise LLMBackendError(str(e)) from e


async def _local_small_plan(router: LMRouter):
    """调 local_small.plan，把 unavailable 翻译成异常。"""
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
        # 全局生成限制（最大输出长度 / 默认上下文长度，llm_kv.gen_limits）
        self._gen_limits = load_gen_limits()
        # 端侧 Ollama 地址/模型优先用「模型管理」（router.db 已启用的 local 后端），
        # 支持自定义 URL/端口；未配置时回退 settings.ollama_*（环境变量/默认）
        _local_url, _local_model = load_enabled_local_backend()
        self.ollama = OllamaClient(
            base_url=_local_url,
            model=_local_model,
            max_context=self._ollama_max_ctx,
            # 未配置端侧模型 → 零探测零等待，降级链直接走下一级（BUGFIX #89）
            enabled=_ollama_enabled_from_db(),
            max_output_tokens=self._gen_limits["max_output_tokens"],
        )
        self.private = (
            PrivateLLMClient(
                base_url=settings.private_llm_base_url,
                api_key=settings.private_llm_api_key,
                model=settings.private_llm_model or "",
                max_context=self._private_max_ctx,
                max_output_tokens=self._gen_limits["max_output_tokens"],
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
        # chat 会话模型 override（2026-08-17）：输入框模型选择器选中时由
        # /chat/{run_id}/stream 设入；None = 按模型管理路由配置（默认行为）
        self._chat_model_override: str | None = None
        self._mock_mode = _is_mock_mode()

    def reload_max_context(self) -> None:
        """热重载端侧/内网配置：在 Settings → 模型管理保存后调一次即可生效，无需重启 Agent。

        同步读 router.db，更新 ollama / private client 的 max_context，
        以及端侧 Ollama 的 base_url / model（自定义 URL/端口热生效）。
        失败保持原值不报错。
        """
        ollama_ctx, private_ctx = _load_max_context_from_db()
        if ollama_ctx is not None:
            self.ollama.max_context = ollama_ctx
        if private_ctx is not None and self.private is not None:
            self.private.max_context = private_ctx
        # 全局生成限制热重载（最大输出长度 / 默认上下文长度）
        self._gen_limits = load_gen_limits()
        self.ollama.max_output_tokens = self._gen_limits["max_output_tokens"]
        if self.private is not None:
            self.private.max_output_tokens = self._gen_limits["max_output_tokens"]
        local_url, local_model = load_enabled_local_backend()
        self.ollama.base_url = local_url.rstrip("/")
        self.ollama.model = local_model

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

    # ---- chat 会话模型 override（2026-08-17）---------------------------------

    @property
    def chat_model_override(self) -> str | None:
        return self._chat_model_override

    def set_chat_model_override(self, name: str | None) -> None:
        """设置 chat 会话级模型 override（模型管理注册表 backend 名）。

        非空时 summarise（回答智能）降级链置顶该模型，优先级高于模型管理
        路由配置；失败仍降级到默认链（对话不断摆）。None/空串 = 清除，
        回落模型管理配置。intent/decompose 属 _LOCAL_ONLY_TASKS 敏感红线，
        不受 override 影响；mock 模式压倒一切（既有语义不变）。
        桌面单用户，会话级语义与 set_inference_mode 同先例。
        """
        cleaned = (name or "").strip() or None
        if cleaned != self._chat_model_override:
            logger.info("router_chat_model_override name=%s", cleaned)
        self._chat_model_override = cleaned

    async def _build_override_client(self) -> Any | None:
        """按 override 名从模型管理注册表构建客户端；未设置/未启用/查不到返 None。

        local → OllamaClient；private/cloud → PrivateLLMClient（OpenAI 兼容，
        与 _build_private_client / _build_cloud_client 同款构建）。
        """
        name = self._chat_model_override
        if not name or self._mock_mode:
            return None
        from agent.llm.storage import list_backends

        try:
            backends = await list_backends(enabled_only=True)
        except Exception as exc:
            logger.warning("chat_model_override registry lookup failed: %s", exc)
            return None
        for backend in backends:
            if backend.name != name:
                continue
            base_url = backend.base_url.rstrip("/")
            if base_url.endswith("/chat/completions"):
                base_url = base_url[: -len("/chat/completions")]
            if backend.type == "local":
                return OllamaClient(
                    base_url=base_url,
                    model=backend.model_name,
                    max_context=backend.max_context,
                    enabled=True,
                    max_output_tokens=self._gen_limits["max_output_tokens"],
                )
            client = PrivateLLMClient(
                base_url=base_url,
                api_key=backend.api_key_ref or "",
                model=backend.model_name,
                max_context=backend.max_context,
                max_output_tokens=self._gen_limits["max_output_tokens"],
            )
            # Token 计量区分来源（与 _build_cloud_client 同款做法）
            client.usage_label = f"override:{name}"
            return client
        logger.warning("chat_model_override backend not found/enabled: %s", name)
        return None

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
                        kind,
                        engine_chain,
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
        if kind in ("doc_classify", "doc_analyze"):
            value = _DOC_CLASSIFY_MOCK if kind == "doc_classify" else _DOC_ANALYZE_MOCK
            return _async_text(value)
        raise ValueError(f"unknown kind: {kind}")

    # ---- "Raise" 包装：把客户端内部静默降级转换成异常 -----------------------

    async def _ollama_intent(self):
        """调 Ollama.classify_intent，但把 unavailable 翻译成异常。"""
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

        降级策略（2026-08-05 调整）：
            - _LOCAL_ONLY_TASKS：本地 Ollama 优先；本地不可用（连接失败 /
              无模型 / 返回空）时逐级降级到内网 private → 云端 cloud
              （数据源 router.db.llm_backends 已启用后端）。
            - 其他任务：保持原 pick() 行为。
        """
        client = self.pick(task)
        if not self._mock_mode and task in _LOCAL_ONLY_TASKS and client is self.ollama:
            return await self._route_local_first(task=task, prompt=prompt)
        if self._mock_mode and task == "history_compress":
            # mock 分支：返回确定性占位摘要（不走 summarise 模板，避免 JSON 包裹）
            return "【mock】历史对话已压缩为摘要占位文本。"
        answer, _ = await self.summarise(
            intent="query",
            user_prompt=prompt,
            plan=[],
            results=[],
        )
        return answer

    async def _route_local_first(self, *, task: str, prompt: str) -> str:
        """本地优先 + 逐级降级：ollama → 内网 private → 云端 cloud。

        红线语义调整：本地模型仍是第一选择；只有本地不可用时才允许出内网/
        云端（与 biznav 提取链、doc_review 链的降级策略对齐）。全链失败抛
        LLMBackendError，由调用方保守兜底。

        降级链统一走 extract_chat 原始透传（不注入 summarise 汇总模板、不包
        {"answer": ...}），让编排 / 分解提示词要求的 JSON 能被原样解析。
        每级后端 connect 失败时进冷却（见 _mark_backend_down），避免 chat
        链路反复等待连接超时导致前端长时间无响应。
        """
        errors: list[str] = []
        ollama_url = self.ollama.base_url
        if _backend_down(ollama_url):
            errors.append("ollama: 冷却中（近期连接失败）")
        else:
            try:
                result = str(
                    await self.ollama.extract_chat(
                        [{"role": "user", "content": prompt}],
                        timeout=120.0,
                    )
                    or ""
                )
                if result.strip():
                    return result
                errors.append("ollama: empty response")
            except Exception as exc:
                if _connect_failure(exc):
                    _mark_backend_down(ollama_url)
                errors.append(f"ollama: {exc}")

        fallbacks: list[tuple[str, Any]] = [
            ("private", self._build_private_client),
            ("cloud", self._build_cloud_client),
        ]
        for backend_name, builder in fallbacks:
            try:
                backend = await builder()
            except Exception as exc:
                errors.append(f"{backend_name}: registry error {exc}")
                continue
            if backend is None and backend_name == "private":
                backend = self.private  # 注册表未配置时回退 settings 配置
            if backend is None:
                errors.append(f"{backend_name}: not configured")
                continue
            base_url = str(getattr(backend, "base_url", "") or backend_name)
            if _backend_down(base_url):
                errors.append(f"{backend_name}: 冷却中（近期连接失败）")
                continue
            try:
                text = str(await backend.extract_chat([{"role": "user", "content": prompt}]) or "")
                if text.strip():
                    logger.warning(
                        "local LLM unavailable, local-only task=%s fell back to %s backend: %s",
                        task,
                        backend_name,
                        "; ".join(errors),
                    )
                    return text
                errors.append(f"{backend_name}: empty response")
            except Exception as exc:
                if _connect_failure(exc):
                    _mark_backend_down(base_url)
                errors.append(f"{backend_name}: {exc}")
        raise LLMBackendError(f"local-first route failed (task={task}): {'; '.join(errors)}")

    async def generate_review(self, *, kind: TaskKind, prompt: str) -> str:
        """文档审核生成：按 settings.doc_review_llm_chain 顺序调用。

        支持后端：mock / ollama / private / cloud。
        private / cloud 都从「模型管理」注册表取已启用后端（router.db.llm_backends）。
        默认链 ["cloud", "private", "ollama"]（云端优先，不可用逐级回退）；全失败抛 LLMBackendError。

        ❗ 必须走 extract_chat 原始对话透传，不能用 summarise：summarise 会注入
        汇总模板并把输出包成 {"answer": ...}，审核要求的 JSON 永远解析不出来
        （与 biznav V1.5 同源问题）。
        """
        if self._mock_mode:
            return await self.mock_dispatch(kind)
        errors: list[str] = []
        for backend_name in settings.doc_review_llm_chain:
            backend = None
            if backend_name == "ollama":
                backend = self.ollama
            elif backend_name == "private":
                # 对齐模型管理：优先注册表已启用 private 后端，无则回退 settings 配置
                try:
                    backend = await self._build_private_client() or self.private
                except Exception:  # 注册表查询失败回退 settings
                    backend = self.private
            elif backend_name == "cloud":
                backend = await self._build_cloud_client()
            if backend is None:
                errors.append(f"{backend_name}: not configured")
                continue
            base_url = str(getattr(backend, "base_url", "") or backend_name)
            if _backend_down(base_url):
                errors.append(f"{backend_name}: 冷却中（近期连接失败）")
                continue
            try:
                text = str(await backend.extract_chat([{"role": "user", "content": prompt}]) or "")
                if text.strip():
                    return text
                errors.append(f"{backend_name}: empty response")
            except Exception as exc:  # 降级链逐级尝试
                if _connect_failure(exc):
                    _mark_backend_down(base_url)
                errors.append(f"{backend_name}: {exc}")
        raise LLMBackendError(
            f"doc_review generate_review failed: {'; '.join(errors) or 'empty chain'}"
        )

    async def _build_cloud_client(self):
        """从模型管理注册表取已启用云端后端；无则返回 None。

        注意：当前 llm_backends.api_key_ref 存的是明文 API Key（模型管理现状）。
        若未来按 CLAUDE.md 切 keyring 占位符语义，此处必须先解析占位符再传入，
        否则会静默变成认证失败。
        """
        from agent.llm.storage import list_backends

        for backend in await list_backends(enabled_only=True):
            if backend.type == "cloud":
                client = PrivateLLMClient(
                    base_url=backend.base_url,
                    api_key=backend.api_key_ref or "",
                    model=backend.model_name,
                    max_context=backend.max_context,
                    max_output_tokens=load_gen_limits()["max_output_tokens"],
                )
                client.usage_label = "cloud"  # Token 计量区分内网 / 云端
                return client
        return None

    async def _build_private_client(self):
        """从模型管理注册表取已启用内网后端（type='private'）；无则返回 None。

        与 _build_cloud_client 同一层级：数据源是 router.db.llm_backends，
        不读 settings.private_llm_*（环境变量/配置）——是否走内网只看
        注册表里有没有启用的 private 后端。

        兼容：base_url 已带 /chat/completions 后缀时先去掉（客户端会再拼）。
        """
        from agent.llm.storage import list_backends

        for backend in await list_backends(enabled_only=True):
            if backend.type == "private":
                base_url = backend.base_url.rstrip("/")
                if base_url.endswith("/chat/completions"):
                    base_url = base_url[: -len("/chat/completions")]
                return PrivateLLMClient(
                    base_url=base_url,
                    api_key=backend.api_key_ref or "",
                    model=backend.model_name,
                    max_context=backend.max_context,
                    max_output_tokens=load_gen_limits()["max_output_tokens"],
                )
        return None

    async def resolve_native_backend(self) -> tuple[str, Any] | None:
        """探测支持原生 function calling 的后端（OpenAI 模式，2026-08-07）。

        顺序与既有降级策略一致：内网 private 优先，无则云端 cloud（均由
        router.db 启用状态驱动）。本地 Ollama 不参与（小模型原生 tool_calls
        不稳定，继续走提示词协议）；敏感任务红线不变（_LOCAL_ONLY_TASKS）。

        Returns:
            ("private" | "cloud", 客户端实例) 或 None（回退提示词协议）。
            探测总耗时封顶 15s，绝不抛异常；结果进程内缓存（每次 chat 运行
            首节点探测一次，后续调用直接读缓存）。
        """
        import asyncio

        if self._mock_mode:
            return None
        cached = getattr(self, "_native_probe_cache", _NATIVE_UNSET)
        if cached is not _NATIVE_UNSET:
            return cached

        async def _probe() -> tuple[str, Any] | None:
            candidates: list[tuple[str, Any]] = []
            try:
                private = await self._build_private_client() or self.private
            except Exception:  # 注册表查询失败不阻塞
                private = self.private
            if private is not None and hasattr(private, "chat_with_tools"):
                candidates.append(("private", private))
            try:
                cloud = await self._build_cloud_client()
            except Exception:  # 注册表查询失败不阻塞
                cloud = None
            if cloud is not None and hasattr(cloud, "chat_with_tools"):
                candidates.append(("cloud", cloud))
            for name, backend in candidates:
                try:
                    if await backend.supports_tool_calling():
                        return (name, backend)
                except Exception:
                    continue
            return None

        try:
            result = await asyncio.wait_for(_probe(), timeout=15.0)
        except Exception as exc:  # 超时/任何异常 → 提示词协议
            logger.debug("native tool calling probe failed: %s", exc)
            result = None
        self._native_probe_cache = result
        return result

    async def analyze_intent(self, text: str, history: list | None = None, page_context: str = ""):
        """结构化意图分析（Intent Router，2026-08-06）。

        返回 IntentAnalysis.to_dict()（dict，与 semantic_route 返回契约一致）：
        四分类 intent（下游兼容）+ 改写句 + 细分类型 + 实体 + 缺失字段 +
        追问信号 + 风险等级。接触用户原始内容 → 与 intent 同属
        _LOCAL_ONLY_TASKS 本地红线（只走 ollama / private）。

        page_context（2026-08-14）：当前页签/场景的一行描述，非空时拼进
        user 消息，帮模型消除“连接”这类模糊动词的场景歧义。

        降级链：ollama → private → 旧式 classify_intent 包装（绝不抛异常）。

        BUGFIX（2026-08-17）：此前返回 IntentAnalysis 对象，而调用方
        intent_node 用 isinstance(analysis, dict) 判定 → 分析结果被静默丢弃，
        state.intent_analysis 永远缺失，decompose 意图快速路径从未生效；
        统一改为返回 dict。
        """
        from agent.llm.types import IntentAnalysis  # 避免循环导入

        if not text or not text.strip():
            return IntentAnalysis(
                intent="chitchat", rewritten_query="", intent_category="chat", backend="empty"
            ).to_dict()
        chain: list[tuple[str, Any]] = []
        if not self._mock_mode:
            chain.append(
                ("ollama", lambda: self.ollama.analyze_intent(text, history, page_context))
            )
            if self.private is not None:
                chain.append(
                    ("private", lambda: self.private.analyze_intent(text, history, page_context))
                )

        async def _plain_fallback() -> dict:
            intent = await self.classify_intent(text)
            return IntentAnalysis.from_plain_intent(intent, text, backend="plain").to_dict()

        chain.append(("plain", _plain_fallback))
        from agent.observability.cot_log import cot as cot_log

        cot_log(
            "analyze_intent.enter", text=text, chain=[name for name, _ in chain[:-1]] + ["plain"]
        )
        result = await with_fallback(
            chain=chain,
            label="intent_analysis",
            raise_on_all_fail=False,
        )
        cot_log(
            "analyze_intent.result",
            text=text,
            final_status=result.final_status,
            trail=result.trail,
            value=result.value,
        )
        if result.final_status == "ok" and isinstance(result.value, dict):
            return IntentAnalysis.from_raw(
                result.value, fallback_text=text, backend=result.value.get("backend") or ""
            ).to_dict()
        # 理论上不可达（plain 兜底不抛异常）——防御性返回 query
        return IntentAnalysis.from_plain_intent("query", text, backend="defensive").to_dict()

    async def classify_intent(self, text: str) -> Intent:
        """向后兼容：返回最终值。降级过程静默（不抛异常）。

        V2 增量：当 `self._spark_mode=True` 时调 engine.spark_route（V2.0 placeholder）。
        保持 keyword 签名完全冻结（test_router_backcompat 锁）。
        """
        from agent.observability.cot_log import cot as cot_log

        if self._spark_mode and self._engine is not None:
            await self._engine.spark_route(
                task_kind="intent",
                user_prompt=text,
                history=[],
                tool_specs=[],
                request_id=f"intent-{hash((text,))}",
            )
            # Spark V0 placeholder：固定返回 query（最常见 Intent）
            cot_log("classify_intent.spark", text=text, result="query (spark placeholder)")
            return "query"
        result = await self.classify_intent_with_fallback(text)
        cot_log(
            "classify_intent.result",
            text=text,
            final_status=result.final_status,
            trail=result.trail,
            intent=result.value,
        )
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
        if self._mock_mode:
            return await self.mock.summarise(
                intent=intent,
                user_prompt=user_prompt,
                plan=plan,
                results=results,
            )
        # Phase 17 V0: L1 精确缓存 —— 相同请求直接返回，跳过整条降级链。
        # 红线：含写工具的 plan 不查不写（_plan_contains_write）。
        cache_key: str | None = None
        if _L1_ENABLED and not _plan_contains_write(plan):
            cache_key = build_response_cache_key(
                task_kind="summarise",
                intent=intent,
                user_prompt=user_prompt,
                plan=plan,
                results=results,
            )
            cached = _L1_RESPONSE_CACHE.get(cache_key)
            if cached is not None:
                try:
                    payload = json.loads(cached)
                    logger.info("l1_response_cache_hit task=summarise")
                    # Phase 17：命中统计推 SSE（三处同步：stream.py / sse_bridge.rs / events.ts）
                    emit_router_event(
                        "llm_cache_stats",
                        {
                            "layer": "l1",
                            "task_kind": "summarise",
                            "hits": _L1_RESPONSE_CACHE.hits,
                            "misses": _L1_RESPONSE_CACHE.misses,
                            "hit_rate": round(_L1_RESPONSE_CACHE.hit_rate, 4),
                        },
                    )
                    return (
                        str(payload.get("answer") or ""),
                        [str(s) for s in (payload.get("sources") or [])],
                    )
                except (ValueError, TypeError):
                    logger.warning("l1_response_cache_corrupt_drop task=summarise")
        # 真实降级链：会话 override 模型（最高优先级，2026-08-17）→ 内网 private
        # → 本地 ollama → 云端 cloud（本地/内网不可用就用云端；全部不可用抛
        # 「无可用模型」错误）。
        errors: list[str] = []
        candidates: list[tuple[str, Any]] = []
        try:
            override = await self._build_override_client()
        except Exception as exc:
            override = None
            errors.append(f"override: registry error {exc}")
        if override is not None:
            candidates.append((f"override:{self._chat_model_override}", override))
        if self.private is not None:
            candidates.append(("private", self.private))
        candidates.append(("ollama", self.ollama))
        try:
            cloud = await self._build_cloud_client()
        except Exception as exc:
            cloud = None
            errors.append(f"cloud: registry error {exc}")
        if cloud is not None:
            candidates.append(("cloud", cloud))
        for backend_name, backend in candidates:
            base_url = str(getattr(backend, "base_url", "") or backend_name)
            if _backend_down(base_url):
                errors.append(f"{backend_name}: 冷却中（近期连接失败）")
                continue
            try:
                answer, sources = await backend.summarise(
                    intent=intent,
                    user_prompt=user_prompt,
                    plan=plan,
                    results=results,
                )
                if cache_key is not None:
                    _L1_RESPONSE_CACHE.put(
                        cache_key,
                        json.dumps(
                            {"answer": answer, "sources": sources},
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                return answer, sources
            except Exception as exc:
                if _connect_failure(exc):
                    _mark_backend_down(base_url)
                errors.append(f"{backend_name}({base_url}): {exc or type(exc).__name__}")
        raise LLMBackendError(
            "无可用模型：本地 Ollama / 内网 / 云端 LLM 后端均不可用。"
            "请在「设置 → 模型管理」中配置可用模型。详情：" + "; ".join(errors)
        )

    async def decompose(
        self,
        *,
        user_prompt: str,
        plan: list[dict],
        history: list[BaseMessage],
        available_subagents: list[dict] | None = None,
        available_tools: list[dict] | None = None,
        page_context: str = "",
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
            SUBAGENT_ENABLEMENT_DECISION_PROMPT.replace("{{CURRENT_TIME}}", current_time_text())
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
            .replace("{{PAGE_CONTEXT}}", page_context or "（未提供）")
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
                "decompose output unparseable, fallback single-agent: %.200s",
                text,
            )
        except Exception as exc:
            logger.warning(
                "decompose LLM call failed, fallback single-agent: %s",
                exc,
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
        decision_hint: str = "",
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
        （本地优先；本地不可用时由 route() 逐级降级到内网 / 云端）。
        """
        if self._mock_mode:
            return _fallback_orchestrate_action()
        summary_names = {str(t.get("name")) for t in tool_summaries}
        registered_names = {str(t.get("name")) for t in registered_tools}
        prompt = (
            DYNAMIC_TOOL_ORCHESTRATOR_PROMPT.replace("{{LOAD_STAGE}}", str(load_stage))
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
            .replace("{{DECISION_HINT}}", decision_hint or "（无）")
            .replace(
                # 本地时间（BUGFIX #113）：旧实现用 UTC，国内凌晨时段日期会差一天，
                # 与 summarise / native 循环的注入口径统一为 current_time_text()
                "{{CURRENT_TIME}}",
                current_time_text(),
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
        except Exception as exc:
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
        [(name, call) for name, call in self._chain_for("intent")]

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
        if (
            not self._mock_mode
            and self.private is not None
            and not _backend_down(self.private.base_url)
        ):
            full_chain.append(("private", lambda: self.private.classify_intent(text)))  # type: ignore[union-attr]
        full_chain.append(("mock", lambda: self.mock.classify_intent(text)))

        return await with_fallback(
            chain=full_chain,
            label="intent",
            raise_on_all_fail=False,
        )
