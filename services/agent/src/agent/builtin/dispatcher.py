"""Phase 1B · ToolDispatcher —— 统一调度内置工具。

V2 职责（2026-07-31）：
  1. 路由：call['server'] == 'builtin' 走 builtin；否则返回 None 让上游走 mcp
  2. 工具分类：Python 工具走本地执行；Rust 工具（stat_file/mkdir/delete_file/...）
     通过 tauri_bridge 调 Rust 端 Tauri Command（V2 已 9/9 实现）
  3. 风险评估：复用 safety/policy.py::policy_for()
  4. HITL 前置闸门：needs_hitl 且未审批 → 不执行、返 awaiting_approval=True，
     由 hitl_gate_node 发起审批；审批通过（approval_decision=approve）后再执行
  5. 执行：Rust 桥 → 不可用时 3 高危工具（delete/move/shell）走 Python 原生兜底，
     其余 6 工具返 not_implemented（Agent 独立运行场景）
  6. 审计：双写 audit(action='builtin_tool', payload={...}) + tool_calls 结构化表
  7. SSE 三处同步：emit builtin_tool_started / done / denied
  8. 返 AgentState 增量
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.builtin.models import ToolResult, is_rust_tool
from agent.builtin.registry import get_default_registry
from agent.llm.prompts import normalize_message

# 兼容 V0 import（外部代码 `from agent.builtin.dispatcher import dispatcher` 不变）
__all__ = ["ToolDispatcher", "dispatcher", "reset_default_dispatcher"]

# 底层规则（用户要求 2026-08-17 / 2026-08-26）：创建类工具的输出路径默认落当前任务目录，
# 按类型自动分类建目录；用户显式指定绝对路径（出现在对话原文）时尊重用户。
# 路径键名（write_file/edit_file=path；pdf_split 的 output_dir 是目录同理）。
# 注：office_edit 是对已有文件的元素级修改（路径必须指向真实存在的文件），不参与改写。
_CREATE_TOOL_PATH_KEYS: dict[str, tuple[str, ...]] = {
    "write_file": ("path",),
    "edit_file": ("path",),
    "word_generate": ("path",),
    "excel_export": ("path",),
    "pdf_merge": ("output",),
    "pdf_split": ("output_dir",),
    "office_create": ("path",),
}


def _user_context_texts(state: dict | None) -> tuple[str, ...]:
    """从 state 提取用户对话原文（user_prompt + history 的 user 消息），
    用于判定绝对路径是否用户显式指定。"""
    if not isinstance(state, dict):
        return ()
    texts: list[str] = []
    up = state.get("user_prompt")
    if isinstance(up, str) and up:
        texts.append(up)
    for m in state.get("messages") or []:
        parsed = normalize_message(m)
        if parsed is None:
            continue
        role, content = parsed
        if role == "user":
            texts.append(content)
    return tuple(texts)


def _apply_workspace_rule(name: str, args: dict, state: dict | None = None) -> dict:
    """创建类工具输出路径 → 当前任务目录/工作空间（分类建目录）。

    在 dispatch 入口统一改写 args，Rust 桥 / Python 执行 / 兜底三条链路
    一并生效，且 SSE / 审计 / 思维链记录的都是改写后的真实落盘路径。
    任务级工作目录（2026-08-26）：有 task_id 时落任务目录；模型自造的绝对路径
    （不在用户对话原文中）一并重定向，防产物散落用户目录。
    """
    keys = _CREATE_TOOL_PATH_KEYS.get(name)
    if not keys:
        return args
    try:
        from agent.paths import resolve_output_path, task_dir

        tid = state.get("task_id") if isinstance(state, dict) else None
        ttitle = state.get("task_title") if isinstance(state, dict) else None
        root = task_dir(tid, ttitle)
        texts = _user_context_texts(state)
        rewritten = dict(args)
        for key in keys:
            raw = rewritten.get(key)
            if isinstance(raw, str) and raw.strip():
                rewritten[key] = str(
                    resolve_output_path(raw.strip(), task_root=root, context_texts=texts)
                )
        return rewritten
    except Exception:
        # 工作空间解析失败不阻断工具执行（回退原路径，沙箱校验仍会兜底）
        return args


class ToolDispatcher:
    """内置工具统一调度器。

    流程（V2）：
      1. 路由：call['server'] == 'builtin' 走 builtin；否则返 None
      2. 工具分类：Rust 工具 → Tauri IPC（V2 9/9）；Python 工具 → 本地执行
      3. 风险评估：policy_for() → approve / needs_hitl
      4. HITL 前置闸门：未审批不执行 → hitl_gate_node 发起审批 → 通过后执行
      5. SSE emit：started → execute → done/denied
      6. 审计：双写 audit() + tool_calls 结构化表
      7. 返 AgentState 增量
    """

    def __init__(self, registry=None) -> None:
        self._registry = registry or get_default_registry()

    async def _exec_python_fallback(self, name: str, args: dict) -> ToolResult:
        """Rust 工具的 Python 原生兜底（3 高危 + 5 只读）。"""
        if name == "delete_file":
            from agent.builtin.files import builtin_delete_file

            return await builtin_delete_file(**args)
        if name == "move_file":
            from agent.builtin.files import builtin_move_file

            return await builtin_move_file(**args)
        if name == "shell":
            from agent.builtin.shell import builtin_shell

            return await builtin_shell(**args)
        if name == "stat_file":
            from agent.builtin.fallbacks import builtin_stat_file_py

            return await asyncio.to_thread(builtin_stat_file_py, **args)
        if name == "find":
            from agent.builtin.fallbacks import builtin_find_py

            return await asyncio.to_thread(builtin_find_py, **args)
        if name == "glob":
            from agent.builtin.fallbacks import builtin_glob_py

            return await asyncio.to_thread(builtin_glob_py, **args)
        if name == "hash":
            from agent.builtin.fallbacks import builtin_hash_py

            return await asyncio.to_thread(builtin_hash_py, **args)
        if name == "base64":
            from agent.builtin.fallbacks import builtin_base64_py

            return await asyncio.to_thread(builtin_base64_py, **args)
        if name == "mkdir":
            from agent.builtin.fallbacks import builtin_mkdir_py

            return await asyncio.to_thread(builtin_mkdir_py, **args)
        return ToolResult(
            ok=False,
            error=f"no_python_fallback: {name}",
            risk_level="high",
        )

    async def dispatch(self, call: dict, state: dict) -> dict | None:
        """统一调度内置工具。

        Args:
            call: {"server": "builtin", "name": "read_file", "args": {...}}
            state: AgentState（用于 retry_count / run_id）

        Returns:
            None if call 不是 builtin（上游应走 mcp）
            dict: AgentState 增量（tool_result / tool_error / trace / needs_hitl）
        """
        if call.get("server") != "builtin":
            return None

        name = call.get("name") or ""
        args = call.get("args") or {}
        # 底层规则：创建类文件默认落当前任务目录（用户指定绝对路径除外）
        args = _apply_workspace_rule(name, args, state)
        run_id = state.get("run_id") if isinstance(state, dict) else None
        operator = state.get("operator") if isinstance(state, dict) else None
        task_id = state.get("task_id") if isinstance(state, dict) else None

        # 调用标识（一次调度 = 一个 UUID；HITL resume 用同一 call_id 关联）
        # 根治 BUGFIX #164：必须**写回 call 字典**。此前只在局部变量里用，
        # pending_tool_call 不带 call_id → SSE 的 tool_call / tool_result 两条
        # 事件没有共享标识，前端无法配对（卡片永久转圈）；HITL 审批后重跑
        # 同一调用也会拿到新 UUID，前端多出一张卡。
        call_id = call.get("call_id") or uuid.uuid4().hex
        call["call_id"] = call_id

        # ---- 1. 工具存在性（含 Rust 占位）----
        # 先识别 Rust 工具（占位 — dispatcher 不需要 registry 收录也能跑通路径）
        is_rust = is_rust_tool(name)
        if not is_rust and not self._registry.has(name):
            return {
                "pending_tool_call": call,
                "tool_result": None,
                "tool_error": f"unknown_builtin_tool: {name}",
                "trace": [_trace_entry("builtin_tool", "fail", name=name, error="unknown_tool")],
            }

        risk_level = self._registry.risk_level(name)
        needs_hitl = await _evaluate_hitl(call, risk_level, state)
        approved = (
            bool(state.get("approval_decision") == "approve") if isinstance(state, dict) else False
        )

        # ---- 2. SSE started（before execution）----
        started_ts = time.monotonic()
        await _emit_started(
            tool_name=name,
            args=args,
            risk_level=risk_level,
            needs_hitl=needs_hitl,
            call_id=call_id,
            run_id=run_id,
        )

        # ---- 2.5 HITL 前置闸门：未审批的写 / 高危调用不执行，等 hitl_gate_node ----
        # 真实 HITL interrupt（V2）：返回 awaiting_approval=True 且不 advance，
        # 审批通过后 tool_runner 重新进入本 dispatch（approval_decision=approve）。
        if needs_hitl and not approved:
            # 执行过程可视化（阶段四）：暂停审批前先下发写前 unified diff，
            # 让用户在审批卡侧看清「将改什么」（FullDiffModal 红绿对比）；
            # best-effort，失败不阻断审批闸门（HITL 红线不回退）。
            if name in ("write_file", "edit_file"):
                await _emit_write_preview(name=name, args=args, call_id=call_id, run_id=run_id)
            return {
                "pending_tool_call": call,
                "tool_result": None,
                "tool_error": None,
                "awaiting_approval": True,
                "approval_id": None,
                "trace": [
                    _trace_entry(
                        "builtin_tool",
                        "running",
                        name=name,
                        risk_level=risk_level,
                        needs_hitl=True,
                        reason="awaiting_hitl",
                        call_id=call_id,
                    )
                ],
            }

        # ---- 3. 执行（Rust 工具 V1 占位 / V1.5 已实现的 6 工具通过 IPC 远端调用）----
        # Phase 16：写操作先捕获修改前内容（用于 unified diff 计算）
        trace_before: str | None = None
        if name in ("write_file", "edit_file"):
            from agent.trace.collector import read_text_best_effort

            trace_before = read_text_best_effort(str(args.get("path") or "")) or ""

        # 执行过程可视化（阶段三）：绑定当前执行上下文（call_id / run_id），
        # 工具实现（如 shell 流式输出）据此给细粒度事件盖章，与 tool_call /
        # tool_result 同源配对；执行完毕无论成败都还原，防串台。
        from agent.builtin.exec_context import bind_exec_scope, reset_exec_scope

        _exec_scope_token = bind_exec_scope(call_id, run_id)
        if is_rust and name == "shell":
            # 执行过程可视化（阶段三）：shell 走 Python 异步流式路径（边跑边发
            # shell_chunk），安全语义与 Rust 端严格镜像（白名单 / 危险操作符 /
            # 超时强杀，BUGFIX #165）；其余 8 个 Rust 工具仍走 bridge/executor。
            try:
                result = await self._exec_python_fallback(name, args)
            except Exception as exc:
                result = ToolResult.from_exception(exc, risk_level=risk_level)
                result.error = f"exec_failed: {type(exc).__name__}: {exc}"
        elif is_rust:
            from agent.builtin.tauri_bridge import (
                has_python_fallback,
                invoke_rust_tool_sync,
            )

            bridge_result = await invoke_rust_tool_sync(
                tool_name=name,
                args=args,
                risk_level=risk_level,
                require_hitl=not approved,
            )
            if bridge_result is not None:
                result = bridge_result
            elif has_python_fallback(name):
                # V2：Agent 独立运行（无 Tauri 注入）→ 3 高危工具走 Python 原生兜底
                try:
                    result = await self._exec_python_fallback(name, args)
                except Exception as exc:
                    result = ToolResult.from_exception(exc, risk_level=risk_level)
                    result.error = f"exec_failed: {type(exc).__name__}: {exc}"
            else:
                # bridge 不可用（无 Tauri runtime）且无 Python 兜底 → not_implemented
                result = ToolResult(
                    ok=False,
                    error=(
                        f"rust_tool_not_implemented: {name} "
                        f"(no Tauri runtime injected; standalone agent has no "
                        f"Rust path for {name})"
                    ),
                    hint="run inside the EAIDE desktop shell (Tauri) or inject runtime",
                    risk_level=risk_level,
                )
        else:
            fn = self._registry.get(name)
            try:
                # 工具函数可能是 sync 或 async；统一 asyncio.to_thread 包装
                if asyncio.iscoroutinefunction(fn):
                    result_obj = await fn(**args)
                else:
                    result_obj = await asyncio.to_thread(fn, **args)
                if not isinstance(result_obj, ToolResult):
                    # 防御性：万一工具返非 ToolResult（理论不应发生），包一层
                    result = ToolResult(
                        ok=bool(result_obj),
                        content=result_obj,
                        risk_level=risk_level,
                    )
                else:
                    result = result_obj
            except Exception as exc:
                result = ToolResult.from_exception(exc, risk_level=risk_level)
                result.error = f"exec_failed: {type(exc).__name__}: {exc}"
        reset_exec_scope(_exec_scope_token)

        elapsed_ms = int((time.monotonic() - started_ts) * 1000)

        # content_size 计算（content 是 str/int/list/dict/None）
        content_size = _safe_content_size(result.content)

        # ---- 4. SSE done ----
        await _emit_done(
            tool_name=name,
            call_id=call_id,
            ok=result.ok,
            error=result.error,
            elapsed_ms=elapsed_ms,
            risk_level=risk_level,
            content_size=content_size,
            result_meta=result.meta,
            run_id=run_id,
        )

        # ---- 4.5 任务台账（2026-08-26）：创建类工具成功落盘 → 记为交付产物 ----
        if result.ok and task_id:
            for key in _CREATE_TOOL_PATH_KEYS.get(name, ()):
                raw = args.get(key)
                if isinstance(raw, str) and raw.strip():
                    try:
                        from agent.paths import ledger_record

                        ledger_record(task_id, raw.strip(), "artifact")
                    except Exception:
                        pass  # best-effort

        # ---- 5. 审计（双写 audit() + tool_calls 表）----
        await _audit_builtin_call(
            call_id=call_id,
            name=name,
            args=args,
            result=result,
            risk_level=risk_level,
            needs_hitl=needs_hitl,
            run_id=run_id,
            operator=operator,
            elapsed_ms=elapsed_ms,
            content_size=content_size,
            approval_id=call.get("approval_id") if needs_hitl else None,
        )

        # ---- 5.5 Phase 16：文件操作追踪（read/write/edit/grep → 思维链）----
        await _trace_file_operation(
            name=name, args=args, result=result, state=state, before=trace_before
        )

        # ---- 6. 组装 AgentState 增量 ----
        trace_status = "ok" if result.ok else "fail"
        # 配对字段回填（根治 BUGFIX #164）：工具实现只关心业务结果，
        # name / call_id 由 dispatcher 统一盖章，保证 SSE 一定带得上。
        result.name = result.name or name
        result.call_id = result.call_id or call_id
        return {
            "pending_tool_call": call,
            "tool_result": result.to_dict(),
            "tool_error": None if result.ok else result.error,
            "trace": [
                _trace_entry(
                    "builtin_tool",
                    trace_status,
                    name=name,
                    risk_level=risk_level,
                    needs_hitl=needs_hitl,
                    error=result.error if not result.ok else None,
                    elapsed_ms=elapsed_ms,
                    call_id=call_id,
                )
            ],
            # 执行路径（含审批后放行）不再等待审批；同时消费掉 approval_decision，
            # 防止同一个 approval_decision 放行后续其他高危调用
            "awaiting_approval": False,
            "approval_id": None,
            "approval_decision": None,
        }


# ---- 内部辅助 ----------------------------------------------------------------


async def _trace_file_operation(
    *,
    name: str,
    args: dict,
    result: ToolResult,
    state: dict,
    before: str | None,
) -> None:
    """Phase 16：把 builtin 文件工具调用写入思维链（best-effort）。

    write_file：after = args['content']；edit_file：after = 回读文件。
    后端不区分工作模式一律记录（金融合规审计）。
    """
    try:
        from agent.config import settings

        if not getattr(settings, "trace_enabled", True):
            return
        session_id = state.get("run_id") if isinstance(state, dict) else None
        if not session_id:
            return
        from agent.trace.collector import extract_file_operation, get_collector

        after: str | None = None
        if name == "write_file" and result.ok:
            after = str(args.get("content") or "")
        op = extract_file_operation(name, args, result.to_dict(), before=before, after=after)
        if op is None:
            return
        await get_collector().attach_file_operation(session_id, op)
    except Exception:
        pass  # best-effort


async def _evaluate_hitl(call: dict, risk_level: str, state: dict) -> bool:
    """risk ≥ medium 且 require_hitl_for_write=True 时返 True。

    critical（shell）永远返 True —— 即使全局开关关闭也不能自动批准
    （与 Rust 端 evaluate_hitl 严格镜像）。
    """
    if risk_level == "read":
        return False
    if risk_level == "critical":
        return True
    if risk_level in ("medium", "high", "critical"):
        try:
            from agent.config import settings

            if settings.require_hitl_for_write:
                return True
        except Exception:
            return True
        return False
    return False


def _scrub_args(args: dict) -> dict:
    """脱敏：路径字段只保留 basename + file size，避免泄漏敏感路径。"""
    scrubbed: dict = {}
    for k, v in args.items():
        if k in ("path", "file", "file_path") and isinstance(v, str):
            p = Path(v)
            try:
                size = p.stat().st_size if p.exists() else None
            except OSError:
                size = None
            scrubbed[k] = {"basename": p.name, "size": size}
        elif isinstance(v, str) and len(v) > 1000:
            scrubbed[k] = v[:1000] + "..."
        else:
            scrubbed[k] = v
    return scrubbed


def _safe_content_size(content: Any) -> int:
    """计算 content 体积（字节）。None / 异常 → 0。"""
    if content is None:
        return 0
    try:
        if isinstance(content, str):
            return len(content.encode("utf-8"))
        if isinstance(content, (int, float)):
            return len(str(content).encode("utf-8"))
        if isinstance(content, (list, dict, tuple)):
            return len(json.dumps(content, default=str, ensure_ascii=False).encode("utf-8"))
        return len(str(content).encode("utf-8"))
    except Exception:
        return 0


async def _audit_builtin_call(
    *,
    call_id: str,
    name: str,
    args: dict,
    result: ToolResult,
    risk_level: str,
    needs_hitl: bool,
    run_id: str | None,
    operator: str | None,
    elapsed_ms: int,
    content_size: int,
    approval_id: str | None,
) -> None:
    """V1 双写：audit(action='builtin_tool', payload={...}) + tool_calls 结构化表。

    两者任一失败不影响主流程；不抛异常（fire-and-forget 风格）。
    """
    safe_args = _scrub_args(args)
    ts = datetime.now(timezone.utc).isoformat()
    args_json = json.dumps(safe_args, default=str, ensure_ascii=False)

    # ---- 1. audit() 通用表（向后兼容 + 已有索引）----
    try:
        from agent.audit.store import audit

        await audit(
            "builtin_tool",
            {
                "call_id": call_id,
                "name": name,
                "args": safe_args,
                "ok": result.ok,
                "error": result.error,
                "risk_level": risk_level,
                "needs_hitl": needs_hitl,
                "elapsed_ms": elapsed_ms,
                "content_size": content_size,
                "approval_id": approval_id,
                "meta": result.meta,
            },
            run_id=run_id,
        )
    except Exception:
        pass  # best-effort

    # ---- 2. tool_calls 结构化表（V1 新增）----
    try:
        await _write_tool_calls_row(
            call_id=call_id,
            tool_name=name,
            risk_level=risk_level,
            needs_hitl=1 if needs_hitl else 0,
            ok=1 if result.ok else 0,
            error=result.error,
            args_json=args_json,
            run_id=run_id,
            operator=operator,
            elapsed_ms=elapsed_ms,
            content_size=content_size,
            approval_id=approval_id,
            ts=ts,
        )
    except Exception:
        pass  # best-effort


async def _write_tool_calls_row(
    *,
    call_id: str,
    tool_name: str,
    risk_level: str,
    needs_hitl: int,
    ok: int,
    error: str | None,
    args_json: str,
    run_id: str | None,
    operator: str | None,
    elapsed_ms: int,
    content_size: int,
    approval_id: str | None,
    ts: str,
) -> None:
    """写入 tool_calls 表（与 Rust 端 schema 完全镜像，CLAUDE.md §6）。

    复用 audit.store 的 aiosqlite 连接（同一 audit.sqlite 文件）。
    不导出新模块入口以保持 audit.store 公开 API 不变；通过 to_thread 避免阻塞事件循环。
    """
    import aiosqlite

    from agent.audit.store import SCHEMA_CREATE_TABLE, SCHEMA_INDEXES
    from agent.config import settings

    target = settings.audit_db_path
    Path(target).parent.mkdir(parents=True, exist_ok=True)

    # tool_calls 表追加到 audit 通用 schema 后（CLAUDE.md §6 双 schema 同步）
    extended_schema = (
        SCHEMA_CREATE_TABLE
        + SCHEMA_INDEXES
        + """
CREATE TABLE IF NOT EXISTS tool_calls (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    call_id         TEXT NOT NULL,
    tool_name       TEXT NOT NULL,
    risk_level      TEXT NOT NULL,
    needs_hitl      INTEGER NOT NULL DEFAULT 0,
    ok              INTEGER NOT NULL,
    error           TEXT,
    args_json       TEXT NOT NULL,
    run_id          TEXT,
    operator        TEXT,
    elapsed_ms      INTEGER NOT NULL DEFAULT 0,
    content_size    INTEGER NOT NULL DEFAULT 0,
    approval_id     TEXT,
    ts              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_run    ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool   ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_calls_ts     ON tool_calls(ts);
CREATE INDEX IF NOT EXISTS idx_tool_calls_call   ON tool_calls(call_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_risk   ON tool_calls(risk_level, ts);
"""
    )

    async with aiosqlite.connect(target) as db:
        await db.executescript(extended_schema)
        await db.execute(
            """
            INSERT INTO tool_calls (
                call_id, tool_name, risk_level, needs_hitl, ok, error,
                args_json, run_id, operator, elapsed_ms, content_size,
                approval_id, ts
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                call_id,
                tool_name,
                risk_level,
                needs_hitl,
                ok,
                error,
                args_json,
                run_id,
                operator,
                elapsed_ms,
                content_size,
                approval_id,
                ts,
            ),
        )
        await db.commit()


async def _emit_started(
    *,
    tool_name: str,
    args: dict,
    risk_level: str,
    needs_hitl: bool,
    call_id: str,
    run_id: str | None = None,
) -> None:
    """SSE emit: builtin_tool_started."""
    try:
        from agent.builtin.events import emit_tool_started

        await emit_tool_started(
            tool_name=tool_name,
            args=args,
            risk_level=risk_level,
            needs_hitl=needs_hitl,
            call_id=call_id,
            run_id=run_id,
        )
    except Exception:
        pass  # best-effort


async def _emit_done(
    *,
    tool_name: str,
    call_id: str,
    ok: bool,
    error: str | None,
    elapsed_ms: int,
    risk_level: str,
    content_size: int,
    result_meta: dict,
    run_id: str | None = None,
) -> None:
    """SSE emit: builtin_tool_done."""
    try:
        from agent.builtin.events import emit_tool_done

        await emit_tool_done(
            tool_name=tool_name,
            call_id=call_id,
            ok=ok,
            error=error,
            elapsed_ms=elapsed_ms,
            risk_level=risk_level,
            content_size=content_size,
            result_meta=result_meta,
            run_id=run_id,
        )
    except Exception:
        pass  # best-effort


def _trace_entry(node: str, status: str, **extra: Any) -> dict:
    """构造 trace 条目（与 graph/state.py::record_trace 字段一致）。"""
    entry: dict[str, Any] = {
        "node": node,
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    entry.update(extra)
    return entry


# ---- 写前 Diff 预览（执行过程可视化 · 阶段四）--------------------------------

# 预览 diff 体积上限（超大改动只截前段，审批仍可继续）
_PREVIEW_DIFF_MAX_CHARS = 65536


def _preview_unified_diff(name: str, args: dict, path: str) -> str:
    """计算写前 unified diff（只读，不落盘；读原文件失败按新建文件处理）。"""
    import difflib
    from pathlib import Path

    def _read_original() -> str:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    original = _read_original()
    if name == "write_file":
        new_content = str(args.get("content") or "")
    else:  # edit_file：镜像工具语义（非 replace_all 多匹配会被工具拒绝 → 不出预览）
        search_text = str(args.get("search_text") or "")
        replace_text = str(args.get("replace_text") or "")
        if not search_text or search_text not in original:
            return ""
        if not bool(args.get("replace_all", False)) and original.count(search_text) > 1:
            return ""
        new_content = original.replace(search_text, replace_text)
    lines = difflib.unified_diff(
        original.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=f"a/{Path(path).name or path}",
        tofile=f"b/{Path(path).name or path}",
        n=3,
    )
    diff = "".join(lines)
    return diff[:_PREVIEW_DIFF_MAX_CHARS]


async def _emit_write_preview(
    *,
    name: str,
    args: dict,
    call_id: str,
    run_id: str | None = None,
) -> None:
    """SSE emit: file_write_preview（写类工具落盘前的 unified diff 预览）。"""
    try:
        from agent.builtin.events import emit_file_write_preview

        path = str(args.get("path") or "")
        diff = _preview_unified_diff(name, args, path)
        if not diff and name == "edit_file":
            return  # 无法构造有意义预览（无匹配 / 多匹配）→ 不发，审批照旧
        await emit_file_write_preview(
            call_id=call_id,
            path=path,
            diff=diff,
            risk_level="medium",
            run_id=run_id,
        )
    except Exception:
        pass  # best-effort：预览失败不阻断审批闸门


# ---- 单例工厂（测试可重置）---------------------------------------------------

_DISPATCHER: ToolDispatcher | None = None


def dispatcher() -> ToolDispatcher:
    """返回默认 dispatcher（单例）。"""
    global _DISPATCHER
    if _DISPATCHER is None:
        _DISPATCHER = ToolDispatcher()
    return _DISPATCHER


def reset_default_dispatcher() -> None:
    """测试 hook：重置单例。"""
    global _DISPATCHER
    _DISPATCHER = None
