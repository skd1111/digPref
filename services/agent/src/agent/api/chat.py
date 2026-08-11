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

import contextlib
import uuid
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.graph.stream import stream_graph_events

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    prompt: str
    context: dict | None = None
    # Phase 18 双框架：前端模式与自主性透传（非法值由 Literal 校验拒绝）
    work_mode: Literal["full", "operator", "auditor", "analyst"] = Field(
        default="full", alias="workMode"
    )
    autonomy: Literal["interactive", "auto"] = Field(default="interactive")
    # Phase 4 V0 推理模式：performance 时 mode_router 注入完整版双模式提示词
    inference_mode: Literal["normal", "performance"] = Field(
        default="normal", alias="inferenceMode"
    )
    # 会话上下文（2026-08-06）：每次发送都是全新 run，模型此前看不到任何历史对话
    # （用户说「转成农历」后补发「20260806」→ 模型反问干什么）。
    # 前端把当前 tab 最近几轮 {role, content} 传进来，注入 graph 初始 messages。
    history: list[dict] = Field(default_factory=list)


@router.post("/{run_id}/stream")
async def chat_stream(run_id: str, body: ChatRequest, request: Request):
    """Stream the agent's work for a given run_id as SSE.

    The graph is read from `app.state.graph` — set by the lifespan handler.
    """
    graph = request.app.state.graph

    # Phase 4 V0 补接（2026-08-05）：此前前端推理模式只进了 state（影响提示词注入），
    # LMRouter.set_inference_mode 从未被调用 → 「性能模式跳过端侧小模型」的
    # intent/plan 降级链始终按 normal 走。这里同步到单例路由器（桌面单用户，
    # 会话级语义成立）；mock/注入对象无该方法时静默跳过。
    runtime = getattr(request.app.state, "runtime", None)
    llm = getattr(runtime, "llm", None)
    setter = getattr(llm, "set_inference_mode", None)
    if callable(setter):
        # 模式切换失败不阻塞对话；mock/注入对象无该方法时静默跳过
        with contextlib.suppress(Exception):
            setter(body.inference_mode)

    # 会话上下文：把前端传来的历史消息清洗后放进 extra_state，
    # stream_graph_events 会在当前用户消息之前拼入初始 messages
    history_msgs = _sanitize_history(body.history)

    async def event_gen():
        try:
            async for evt in stream_graph_events(
                graph,
                run_id,
                body.prompt,
                extra_state={
                    "work_mode": body.work_mode,
                    "autonomy": body.autonomy,
                    "inference_mode": body.inference_mode,
                    "history": history_msgs,
                },
            ):
                yield _sse_format(evt)
        except Exception as exc:
            yield _sse_format({"event": "error", "data": {"kind": "error", "message": str(exc)}})
        finally:
            yield _sse_format({"event": "done", "data": {"kind": "done", "runId": run_id}})

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


# ---- 会话标题摘要（2026-08-07）---------------------------------------------


class SummarizeTitleRequest(BaseModel):
    user_prompt: str = Field(alias="userPrompt")
    assistant_reply: str = Field(default="", alias="assistantReply")


@router.post("/summarize-title")
async def summarize_title(body: SummarizeTitleRequest) -> dict:
    """把首轮对话摘要成短标题（TabBar AI 标题用）。

    非敏感任务：走 LMRouter 本地优先降级链（ollama → 内网 → 云端）。
    任何失败都返回空 title，前端保留截断标题兜底，不阻塞主链路。
    """
    from agent.llm.router import LMRouter

    snippet = (body.assistant_reply or "")[:500]
    prompt = (
        "把下面的对话概括成一个 6~12 字的短标题，只输出标题本身，"
        "不要引号、标点、编号或任何前缀说明。\n\n"
        f"用户问题：{body.user_prompt[:500]}"
    )
    if snippet:
        prompt += f"\nAI 回复节选：{snippet}"
    try:
        router = LMRouter()
        if router._mock_mode:
            # mock 模式没有真实摘要能力：直接用用户问题截断做标题，
            # 避免 mock.extract_chat 的 biznav JSON 数组被当成标题。
            text = (body.user_prompt or "").strip().replace("\n", " ")[:12]
        else:
            text = await router._route_local_first(task="title", prompt=prompt)
    except Exception:
        return {"title": ""}
    title = text.strip().strip("\"'「」").split("\n")[0][:24].strip()
    return {"title": title}


# ---- history 清洗 -----------------------------------------------------------

# 只保留 user / assistant（system 由后端注入，tool 消息依赖 tool_call_id 不能单独出现）
_HISTORY_ROLES = {"user", "assistant"}
# 最多回传最近 12 轮对话，避免超长上下文拖慢推理（PrivateLLM/Ollama 自身还会再截断）
_HISTORY_MAX_MESSAGES = 24
_HISTORY_MAX_CONTENT_LEN = 4000


def _sanitize_history(raw: list) -> list[dict]:
    """把前端传来的 history 清洗成 [{role, content}]（防御脏数据）。"""
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role not in _HISTORY_ROLES or not content:
            continue
        out.append({"role": role, "content": content[:_HISTORY_MAX_CONTENT_LEN]})
    return out[-_HISTORY_MAX_MESSAGES:]


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
