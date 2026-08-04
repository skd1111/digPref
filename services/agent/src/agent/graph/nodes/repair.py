"""Auto-Repair 节点 —— 工具调用出错时，把错误信息塞回 prompt，让 LLM 重新生成调用参数。

对一次运行内的总重试次数设上限（默认 2）。用完后把错误传给 responder，
让 responder 老老实实告诉用户哪里挂了。"""
from __future__ import annotations

from agent.graph.state import AgentState, record_trace
from agent.llm.router import LMRouter


MAX_RETRIES = 2


async def repair_node(state: AgentState, llm: LMRouter) -> dict:
    error = state.get("tool_error")
    if not error:
        return {
            "tool_error": None,
            "trace": [record_trace("repair", "skipped", reason="no_error")],
        }

    retry_count = state.get("retry_count", 0)
    if retry_count >= MAX_RETRIES:
        return {
            "tool_error": error,  # leave as-is; responder will surface
            "trace": [record_trace(
                "repair", "fail",
                reason="retry_exhausted",
                retries=retry_count,
                error=error,
            )],
        }

    original = state.get("pending_tool_call") or {}
    idx = state.get("current_step_index", 0)
    history = state.get("messages", [])
    try:
        fixed = await llm.repair_call(
            original=original,
            error=error,
            history=history,
        )
    except Exception as exc:  # noqa: BLE001
        # LLM itself is unavailable — surface this error immediately
        # instead of silently looping until retries are exhausted
        new_err = f"repair LLM call failed: {exc} (original error: {error})"
        return {
            "retry_count": retry_count + 1,
            "tool_error": new_err,   # surface so user sees the real problem
            "trace": [record_trace("repair", "fail", error=str(exc))],
        }

    return {
        # Swap the plan entry in-place so the next tool_runner uses the fixed call
        "plan": _swap_plan_step(state.get("plan", []), idx, fixed),
        "pending_tool_call": fixed,
        "tool_error": None,         # cleared so tool_runner proceeds
        "tool_result": None,
        "retry_count": retry_count + 1,
        "trace": [record_trace(
            "repair", "ok",
            attempt=retry_count + 1,
            new_server=fixed.get("server"),
            new_name=fixed.get("name"),
        )],
    }


def _swap_plan_step(plan: list[dict], idx: int, new_step: dict) -> list[dict]:
    out = list(plan)
    if idx < len(out):
        out[idx] = new_step
    return out