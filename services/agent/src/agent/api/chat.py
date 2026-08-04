"""/chat 一组接口 —— 接收用户 prompt、跑 LangGraph 状态机，
把事件以 SSE 流式回吐。

事件类型（必须与 apps/desktop/src/ipc/events.ts 保持一致）：
    - message      ：助手文本的增量片段
    - tool_call    ：MCP 工具调用
    - tool_result  ：MCP 工具返回（可能已截断）
    - trace        ：思维链步骤
    - approval     ：HITL 闸门请求
    - log          ：自由格式日志（Xterm 显示）
    - done         ：流正常结束
    - error        ：流异常结束
"""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.graph.stream import _sse_event, stream_graph_events


router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    prompt: str
    context: dict | None = None
    # Phase 18 双框架：前端模式与自主性透传（非法值由 Literal 校验拒绝）
    work_mode: Literal["full", "operator", "auditor", "analyst"] = Field(
        default="full", alias="workMode"
    )
    autonomy: Literal["interactive", "auto"] = Field(default="interactive")


@router.post("/{run_id}/stream")
async def chat_stream(run_id: str, body: ChatRequest, request: Request):
    """Stream the agent's work for a given run_id as SSE.

    The graph is read from `app.state.graph` — set by the lifespan handler.
    """
    graph = request.app.state.graph

    async def event_gen():
        try:
            async for evt in stream_graph_events(
                graph,
                run_id,
                body.prompt,
                extra_state={"work_mode": body.work_mode, "autonomy": body.autonomy},
            ):
                yield _sse_format(evt)
        except Exception as exc:  # noqa: BLE001
            yield _sse_format({"event": "error",
                               "data": {"kind": "error", "message": str(exc)}})
        finally:
            yield _sse_format({"event": "done",
                               "data": {"kind": "done", "runId": run_id}})

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("")
async def chat_create(body: ChatRequest) -> dict:
    """Non-streaming variant — useful for tests / scripts."""
    return {"run_id": str(uuid.uuid4()), "prompt": body.prompt}


# ---- SSE framing -----------------------------------------------------------

def _sse_format(evt: dict) -> str:
    """Render one SSE event to the wire format.

    sse-starlette expects a dict with `event` and `data`; for our
    adapter, we return the raw string and use `media_type=text/event-stream`.
    """
    event = evt.get("event", "message")
    data = evt.get("data")
    if not isinstance(data, str):
        import json
        data = json.dumps(data, default=str, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"