"""tool_runner node — execute the next pending tool call via MCP or builtin.

Wraps each invocation with:
    - timeout (config.tool_timeout_sec)
    - retry on transient failures (one retry, then surface to repair)
    - per-call row_limit injection
    - error capture (consumed by repair node)

Phase 1B V0 dispatch (CLAUDE.md §1 + builtin dispatcher):
    - call['server'] == 'builtin' → ToolDispatcher.dispatch()
    - 否则 → 按原 MCP 路径走 mcp.invoke()
"""

from __future__ import annotations

import asyncio

from agent.config import settings
from agent.graph.state import AgentState, advance, next_step, record_trace
from agent.mcp.client import McpClient

# Transient errors that warrant a retry inside tool_runner before giving up
# to the repair node. Connection / timeout / 5xx style errors.
_TRANSIENT_TOKENS = (
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "broken pipe",
    "5xx",
    "internal server",
)


async def tool_runner_node(state: AgentState, mcp: McpClient) -> dict:
    call = next_step(state)
    if call is None:
        # Nothing to run; let the edge router send us to responder.
        return {
            "pending_tool_call": None,
            "tool_result": None,
            "tool_error": None,
            "trace": [record_trace("tool_runner", "skipped", reason="no_pending_call")],
        }

    # Phase 1B V0: builtin 工具先走 ToolDispatcher,不走 MCP
    if call.get("server") == "builtin":
        from agent.builtin.dispatcher import dispatcher as _builtin_dispatcher

        result = await _builtin_dispatcher().dispatch(call, dict(state))
        if result is not None:
            # V2 HITL 前置闸门：等待审批时不推进步骤索引，
            # 审批通过后路由回 tool_runner 重跑同一 call（approval_decision=approve）
            if result.get("awaiting_approval"):
                return result
            # 步进 index（与 MCP 路径行为一致）
            return {**result, **advance(state)}
        # 显式 None 表示不能处理（理论上不会，因为已判断 server=='builtin'）
        return {
            "pending_tool_call": call,
            "tool_result": None,
            "tool_error": "builtin_dispatcher_returned_none",
            "trace": [
                record_trace(
                    "tool_runner",
                    "fail",
                    reason="dispatcher_none",
                    server="builtin",
                    name=call.get("name"),
                )
            ],
        }

    # 调用标识（根治 BUGFIX #164）：写回 call 字典，让 pending_tool_call 携带它，
    # SSE 的 tool_call / tool_result 两条事件据此配对（前端翻牌收尾）。
    # 用步骤索引而非 UUID —— 重试 / HITL 恢复后重跑同一步会得到同一 call_id，
    # 前端原地更新那张卡，不会再堆出第二张。
    call_id = call.get("call_id") or f"call_{state.get('current_step_index', 0)}"
    call["call_id"] = call_id
    timeout_sec = settings.tool_timeout_sec
    last_err: str | None = None

    for attempt in (1, 2):
        try:
            result = await asyncio.wait_for(
                mcp.invoke(
                    call,
                    timeout_sec=timeout_sec,
                    row_limit=settings.row_limit,
                ),
                timeout=timeout_sec + 2,  # outer safety net
            )
            truncated = bool(isinstance(result, dict) and result.get("truncated"))
            return {
                "pending_tool_call": call,
                "tool_result": result,
                "tool_error": None,
                "truncated_any": truncated,  # MUST set so responder can warn user
                "trace": [
                    record_trace(
                        "tool_runner",
                        "ok",
                        call_id=call_id,
                        server=call.get("server"),
                        name=call.get("name"),
                        attempt=attempt,
                        truncated=truncated,
                    )
                ],
                **advance(state),
            }
        except asyncio.TimeoutError as exc:
            last_err = f"timeout after {timeout_sec}s: {exc}"
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"

        if attempt == 1 and _is_transient(last_err or ""):
            continue  # retry once on transient
        break

    # Hard failure — surface to repair node
    return {
        "pending_tool_call": call,
        "tool_result": None,
        "tool_error": last_err or "unknown error",
        "trace": [
            record_trace(
                "tool_runner",
                "fail",
                call_id=call_id,
                server=call.get("server"),
                name=call.get("name"),
                error=last_err,
            )
        ],
    }


def _is_transient(msg: str) -> bool:
    low = msg.lower()
    return any(t in low for t in _TRANSIENT_TOKENS)
