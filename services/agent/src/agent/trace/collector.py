"""Phase 16 · TraceCollector —— 思维链收集器（LangGraph Hook）。

职责：
    - 在 LangGraph 每个节点执行后记录一条 ThinkingStep（中文思考内容）
    - 拦截工具调用 → 提取文件操作（read/write/edit/grep）
    - 写操作保存修改前后内容 → difflib 计算 unified diff
    - 结构化存储到 SQLite thinking_steps 表

设计红线：
    - best-effort：任何记录失败都不得影响主图执行（try/except 兜底）
    - 后端不区分工作模式一律记录（金融合规审计；前端负责模式隔离）
    - 中文思维链格式：【思考】/【行动】/【观察】/【决策】四段式
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from agent.trace import storage
from agent.trace.diff import build_diff_fields, estimate_tokens
from agent.trace.models import (
    OP_EDIT,
    OP_GREP,
    OP_READ,
    OP_WRITE,
    FileOperation,
    ThinkingStep,
)

logger = logging.getLogger(__name__)

# thinking / decision 入库截断上限（防止超大文本膨胀 trace.db）
_MAX_TEXT_CHARS = 4000

# 节点中文名（时间线展示用）
NODE_LABELS: dict[str, str] = {
    "intent": "意图识别",
    "planner": "任务规划",
    "decompose": "任务分解决策",
    "tool_orchestrator": "动态工具调用",
    "tool_runner": "工具执行",
    "hitl_gate": "人工审批闸门",
    "repair": "自动修复",
    "responder": "回答生成",
    "rag_retrieve": "知识检索",
    "vision_understand": "截图理解",
    "local_intent": "端侧意图识别",
    "builtin_tool": "内置工具",
}

# builtin 文件工具 → 操作类型
_FILE_TOOL_OP: dict[str, str] = {
    "read_file": OP_READ,
    "write_file": OP_WRITE,
    "edit_file": OP_EDIT,
    "grep": OP_GREP,
}


def _clip(text: str | None, limit: int = _MAX_TEXT_CHARS) -> str | None:
    if text is None:
        return None
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n…（已截断，原文共 {len(text)} 字符）"


def _safe_json_value(v: Any, limit: int = 300) -> str:
    try:
        s = json.dumps(v, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(v)
    return s if len(s) <= limit else s[:limit] + "…"


def node_label(node_name: str) -> str:
    return NODE_LABELS.get(node_name, node_name)


# ---- 中文思维链文本构建（【思考】/【行动】/【观察】/【决策】）-----------------


def build_thinking(node_name: str, delta: dict[str, Any]) -> tuple[str | None, str | None]:
    """从节点输出增量构建 (thinking, decision) 中文文本。"""
    parts: list[str] = []
    decision: str | None = None

    if node_name == "intent":
        intent = delta.get("intent")
        if intent:
            parts.append(f"【思考】解析用户输入，识别意图为 `{intent}`。")

    elif node_name in ("planner",):
        explanation = delta.get("plan_explanation")
        plan = delta.get("plan")
        if explanation:
            parts.append(f"【思考】{explanation}")
        if isinstance(plan, list) and plan:
            names = ", ".join(str(s.get("name", "?")) for s in plan if isinstance(s, dict))
            parts.append(f"【行动】规划 {len(plan)} 步工具调用：{names}。")

    elif node_name == "decompose":
        d = delta.get("decompose_decision")
        inner = d.get("decision") if isinstance(d, dict) else None
        if isinstance(inner, dict):
            mode = inner.get("mode")
            reason = inner.get("reason")
            if reason:
                parts.append(f"【思考】{reason}")
            if mode:
                decision = f"执行模式：{mode}"
                parts.append(f"【决策】判定执行模式为 {mode}。")

    elif node_name in ("tool_orchestrator", "tool_runner"):
        call = delta.get("pending_tool_call")
        if isinstance(call, dict):
            args_s = _safe_json_value(call.get("args") or {})
            parts.append(
                f"【行动】调用工具 `{call.get('name', '?')}`"
                f"（server={call.get('server', '?')}，参数={args_s}，"
                f"风险={call.get('risk_level', '?')}）。"
            )
        result = delta.get("tool_result")
        if isinstance(result, dict):
            ok = result.get("ok", True)
            content = result.get("content")
            obs = _safe_json_value(content) if content is not None else "(空)"
            parts.append(f"【观察】工具{'成功' if ok else '失败'}：{obs}")
        err = delta.get("tool_error")
        if err:
            parts.append(f"【观察】工具执行出错：{err}")

    elif node_name == "hitl_gate":
        appr = delta.get("approval_decision")
        if appr:
            parts.append(f"【观察】人工审批决策：{appr}。")
        elif delta.get("awaiting_approval"):
            parts.append("【行动】写操作需人工审批，已发起 HITL 审批请求。")

    elif node_name == "repair":
        err = delta.get("tool_error")
        if err:
            parts.append(f"【思考】工具执行失败（{err}），尝试自动修复重试。")

    elif node_name == "responder":
        answer = delta.get("final_answer")
        if answer:
            decision = _clip(str(answer), 500)
            parts.append(f"【决策】{_clip(str(answer), 800)}")

    elif node_name == "rag_retrieve":
        rag = delta.get("rag_context")
        if rag is not None:
            parts.append("【思考】已检索本地知识库，将相关上下文注入提示词。")

    thinking = "\n".join(parts) if parts else None
    return _clip(thinking), decision


def extract_tool_calls(delta: dict[str, Any]) -> list[dict[str, Any]]:
    """从节点增量提取工具调用记录 [{name, params, result}]。"""
    calls: list[dict[str, Any]] = []
    call = delta.get("pending_tool_call")
    if isinstance(call, dict) and call.get("name"):
        entry: dict[str, Any] = {
            "name": call.get("name"),
            "server": call.get("server"),
            "params": call.get("args") or {},
            "risk_level": call.get("risk_level"),
        }
        result = delta.get("tool_result")
        if isinstance(result, dict):
            entry["result"] = {
                "ok": result.get("ok", True),
                "content": _safe_json_value(result.get("content"), 500),
                "error": result.get("error"),
            }
        calls.append(entry)
    return calls


# ---- 文件操作提取 ------------------------------------------------------------


def read_text_best_effort(path: str, max_bytes: int = 2 * 1024 * 1024) -> str | None:
    """安全读取文件文本（失败 / 超限返 None，绝不抛异常）。"""
    try:
        p = Path(path)
        if not p.is_file() or p.stat().st_size > max_bytes:
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def extract_file_operation(
    tool_name: str,
    args: dict[str, Any],
    result: dict[str, Any] | None,
    *,
    before: str | None = None,
    after: str | None = None,
) -> FileOperation | None:
    """把一次 builtin 文件工具调用转为 FileOperation（含 diff）。

    Args:
        tool_name: builtin 工具名（read_file / write_file / edit_file / grep）。
        args: 工具参数（含 path）。
        result: 工具结果 dict（含 ok / error）。
        before: 修改前内容（write/edit 用；新建文件传 ""）。
        after: 修改后内容（write 直接是 args['content']；edit 建议回读文件）。
    """
    op_type = _FILE_TOOL_OP.get(tool_name)
    if op_type is None:
        return None
    path = str(args.get("path") or "")
    if not path:
        return None

    ok = True
    error: str | None = None
    if isinstance(result, dict):
        ok = bool(result.get("ok", True))
        error = result.get("error")

    op = FileOperation(type=op_type, path=path, ok=ok, error=error)

    if op_type == OP_READ and isinstance(result, dict):
        meta = result.get("meta") or {}
        start = meta.get("start_line")
        lines = meta.get("line_count")
        if isinstance(start, int):
            op.start_line = start
            if isinstance(lines, int):
                op.end_line = start + lines
    elif op_type == OP_GREP:
        op.start_line = None
        op.end_line = None
    elif op_type in (OP_WRITE, OP_EDIT) and ok:
        b = before if before is not None else ""
        a = after if after is not None else (read_text_best_effort(path) or "")
        fields = build_diff_fields(b, a, Path(path).name)
        op.diff = fields["diff"] or None
        op.preview = fields["preview"] or None
        op.lines_added = fields["lines_added"]
        op.lines_removed = fields["lines_removed"]
    return op


# ---- TraceCollector ----------------------------------------------------------


class TraceCollector:
    """思维链收集器单例。所有方法 best-effort，绝不向调用方抛异常。"""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path
        # 会话级步骤计数器（避免每次查库；库内 COUNT 作兜底）
        self._index: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def set_db_path(self, db_path: str | None) -> None:
        """测试 hook：重定向存储路径。"""
        self._db_path = db_path

    async def _next_index(self, session_id: str) -> int:
        async with self._lock:
            if session_id not in self._index:
                self._index[session_id] = await storage.count_steps(
                    session_id, db_path=self._db_path
                )
            idx = self._index[session_id]
            self._index[session_id] = idx + 1
            return idx

    async def record_node_step(
        self,
        session_id: str,
        node_name: str,
        delta: dict[str, Any],
        *,
        latency_ms: int | None = None,
        message_id: str | None = None,
    ) -> ThinkingStep | None:
        """LangGraph Hook：节点执行完成后记录一条思维链步骤。"""
        if not session_id or not isinstance(delta, dict):
            return None
        try:
            thinking, decision = build_thinking(node_name, delta)
            tool_calls = extract_tool_calls(delta)
            # 节点增量中无文件操作时，思考内容里的 📄 引用由前端识别高亮；
            # 真实文件操作由 dispatcher 通过 attach_file_operation 追加。
            if not thinking and not decision and not tool_calls:
                return None
            step = ThinkingStep(
                session_id=session_id,
                node_name=node_name,
                step_index=await self._next_index(session_id),
                message_id=message_id,
                thinking=thinking,
                thinking_tokens=estimate_tokens(thinking) if thinking else None,
                tool_calls=tool_calls,
                decision=decision,
                latency_ms=latency_ms,
            )
            await storage.insert_step(step, db_path=self._db_path)
            return step
        except Exception as exc:
            logger.debug("trace.record_node_step 失败（忽略）: %s", exc)
            return None

    async def attach_file_operation(
        self,
        session_id: str,
        op: FileOperation,
    ) -> bool:
        """把文件操作挂到会话最近一条 step 上；无 step 时新建一条。"""
        if not session_id:
            return False
        try:
            last = await _latest_step(session_id, self._db_path)
            if last is not None:
                last.file_operations.append(op)
                await _update_file_operations(last, self._db_path)
                return True
            # 尚无步骤 → 建一条工具执行步骤承载该文件操作
            step = ThinkingStep(
                session_id=session_id,
                node_name="builtin_tool",
                step_index=await self._next_index(session_id),
                thinking=f"【行动】执行文件操作：{op.type} {op.path}",
                file_operations=[op],
            )
            await storage.insert_step(step, db_path=self._db_path)
            return True
        except Exception as exc:
            logger.debug("trace.attach_file_operation 失败（忽略）: %s", exc)
            return False


async def _latest_step(session_id: str, db_path: str | None) -> ThinkingStep | None:
    """查询会话最近一条 step（step_index 最大）。"""
    import aiosqlite

    from agent.config import settings
    from agent.trace.storage import SCHEMA_CREATE_TABLE, _row_to_step

    target = db_path or settings.trace_db_path
    if not Path(target).exists():
        return None
    async with aiosqlite.connect(target) as db:
        await db.executescript(SCHEMA_CREATE_TABLE)
        cur = await db.execute(
            "SELECT id, session_id, message_id, step_index, node_name, thinking,"
            "       thinking_tokens, tool_calls, file_operations, decision,"
            "       tokens_used, latency_ms, created_at "
            "FROM thinking_steps WHERE session_id = ? "
            "ORDER BY step_index DESC, created_at DESC LIMIT 1",
            (session_id,),
        )
        row = await cur.fetchone()
    return _row_to_step(row) if row else None


async def _update_file_operations(step: ThinkingStep, db_path: str | None) -> None:
    """UPDATE 一条 step 的 file_operations JSON 字段。"""
    import aiosqlite

    from agent.config import settings

    target = db_path or settings.trace_db_path
    async with aiosqlite.connect(target) as db:
        await db.execute(
            "UPDATE thinking_steps SET file_operations = ? WHERE id = ?",
            (
                json.dumps(
                    [op.to_dict() for op in step.file_operations],
                    ensure_ascii=False,
                    default=str,
                ),
                step.id,
            ),
        )
        await db.commit()


# ---- 单例 --------------------------------------------------------------------

_COLLECTOR: TraceCollector | None = None


def get_collector() -> TraceCollector:
    global _COLLECTOR
    if _COLLECTOR is None:
        _COLLECTOR = TraceCollector()
    return _COLLECTOR


def reset_collector() -> None:
    """测试 hook：重置单例。"""
    global _COLLECTOR
    _COLLECTOR = None
