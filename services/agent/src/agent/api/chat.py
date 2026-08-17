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

import base64
import binascii
import contextlib
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
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
    # 会话模型 override（2026-08-17）：输入框模型选择器选中的模型管理 backend 名；
    # 优先级最高（summarise 链置顶），None/缺省 = 按模型管理路由配置。
    model_override: str | None = Field(default=None, alias="modelOverride")
    # 历史压缩摘要（2026-08-17）：断点之前的旧对话已被 LLM 压缩成摘要，
    # 注入 graph 初始 messages 时作为 system 消息置于 history 之前。
    history_summary: str | None = Field(default=None, alias="historySummary")


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
    # 会话模型 override（2026-08-17）：选中模型优先级最高；未选（None）清除
    # override 回落模型管理配置。同 set_inference_mode 先例：会话级单例语义。
    override_setter = getattr(llm, "set_chat_model_override", None)
    if callable(override_setter):
        with contextlib.suppress(Exception):
            override_setter(body.model_override)

    # 会话上下文：把前端传来的历史消息清洗后放进 extra_state，
    # stream_graph_events 会在当前用户消息之前拼入初始 messages
    history_msgs = _sanitize_history(body.history)
    summary_text = (body.history_summary or "").strip()[:_HISTORY_SUMMARY_MAX_LEN] or None

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
                    # 历史压缩摘要（2026-08-17）：置于 history 之前的 system 消息
                    "history_summary": summary_text,
                    # 页面上下文（2026-08-14）：前端当前页签/场景，注入 intent /
                    # decompose prompt 消除“连接”这类模糊动词的歧义
                    "page_context": body.context or None,
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


# ---- 会话历史压缩（2026-08-17）-------------------------------------------
#
# 前端「压缩上下文」把断点之前（保留轮之外）的旧对话传过来，LLM 生成一段
# 精炼摘要替换旧 history。接触用户原始对话内容 → history_compress 属
# _LOCAL_ONLY_TASKS 本地红线（本地优先，不可用时逐级降级）。


class CompressHistoryRequest(BaseModel):
    messages: list[dict] = Field(default_factory=list)
    # 已有摘要（多次压缩增量合并；None/空 = 首次压缩）
    history_summary: str | None = Field(default=None, alias="historySummary")


def _sanitize_for_compress(raw: list) -> list[dict]:
    """压缩链路清洗：与 _sanitize_history 同规则，但单条/总量上限更宽。"""
    out: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = str(item.get("content") or "").strip()
        if role not in _HISTORY_ROLES or not content:
            continue
        out.append({"role": role, "content": content[:_COMPRESS_MAX_CONTENT_LEN]})
    return out[-_COMPRESS_MAX_MESSAGES:]


@router.post("/compress-history")
async def compress_history(body: CompressHistoryRequest) -> dict:
    """把旧对话压缩成一段摘要（保留关键事实/决策/结论/未完成事项）。"""
    msgs = _sanitize_for_compress(body.messages)
    if not msgs:
        raise HTTPException(400, "没有可压缩的对话内容")

    before_chars = sum(len(m["content"]) for m in msgs)
    prior = (body.history_summary or "").strip()[:_HISTORY_SUMMARY_MAX_LEN]

    lines: list[str] = []
    if prior:
        lines.append(f"【此前的对话摘要（请在此基础上合并更新）】\n{prior}")
    lines.append("【新增的对话记录】")
    for m in msgs:
        lines.append(f"[{m['role']}] {m['content']}")
    prompt = (
        "请把下面的对话记录压缩成一段不超过 500 字的中文摘要，用于替换原始历史对话。\n"
        "要求：\n"
        "1. 保留关键事实、用户诉求、已达成的结论与决策、未完成的事项；\n"
        "2. 删除寒暄、重复与无关内容；\n"
        "3. 只输出摘要正文，不要任何前缀、标题或解释。\n\n" + "\n".join(lines)
    )

    from agent.llm.router import LMRouter

    try:
        llm_router = LMRouter()
        summary = (await llm_router.route(task="history_compress", prompt=prompt)).strip()
    except Exception as exc:
        raise HTTPException(503, f"上下文压缩失败：{exc}") from exc
    if not summary:
        raise HTTPException(503, "上下文压缩失败：模型返回空内容")

    summary = summary[:_HISTORY_SUMMARY_MAX_LEN]
    return {
        "ok": True,
        "summary": summary,
        "beforeTokens": max(1, before_chars // 4),
        "afterTokens": max(1, len(summary) // 4),
        "messageCount": len(msgs),
    }


# ---- 附加文件（chat 上下文，2026-08-14）-------------------------------------
#
# 前端 ChatInput 的 📎 按钮把文件读成 base64 传到这里：
#   - 文本/代码类 → UTF-8/GBK 解码后原文返回
#   - docx / pdf / pptx / xlsx 等 → 走内置 file_to_markdown（markitdown）转 Markdown
# 红线：任何失败路径都返 ok=False，绝不上抛；转换自带超时保护，不阻塞主链路。

# 上传体积上限（解码后 20MB，防内存爆掉）
_ATTACH_MAX_BYTES = 20 * 1024 * 1024
# 单文件内容上限（防拼进 prompt 后超 Rust 侧 100KB 闸门；超出部分截断并标注）
_ATTACH_MAX_CHARS = 12_000
# 临时附件保留时长（秒）：每次请求顺带清理过期文件
_ATTACH_TTL_SEC = 24 * 3600

# 文本/代码类扩展名：直接解码返回原文（html 走 markitdown 提取正文更干净）
_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".log",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".conf",
    ".properties",
    ".env",
    ".py",
    ".pyi",
    ".ipynb",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".vue",
    ".svelte",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".scala",
    ".rb",
    ".php",
    ".cs",
    ".cpp",
    ".cc",
    ".cxx",
    ".c",
    ".h",
    ".hpp",
    ".swift",
    ".dart",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".css",
    ".scss",
    ".less",
    ".xml",
    ".proto",
}


class ChatAttachFileRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=512)
    content_base64: str = Field(min_length=1)


def _attach_dir() -> Path:
    """临时附件目录（运行时文件统一落 data_root，见 paths.py）。"""
    from agent.paths import data_root

    d = data_root() / "chat-attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cleanup_old_attachments(d: Path) -> None:
    """best-effort 清理超过保留期的临时附件，失败静默。"""
    now = time.time()
    for f in d.iterdir():
        with contextlib.suppress(OSError):
            if f.is_file() and now - f.stat().st_mtime > _ATTACH_TTL_SEC:
                f.unlink()


def _decode_text(raw: bytes) -> str:
    """UTF-8 优先，失败回退 GBK（Windows 中文环境常见编码），再失败替换解码。"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("gbk")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


@router.post("/attach-file")
async def chat_attach_file(body: ChatAttachFileRequest) -> dict:
    """把用户上传的文件转成可拼进 prompt 的文本（原文或 Markdown）。"""
    try:
        raw = base64.b64decode(body.content_base64)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, f"content_base64 解码失败: {exc}") from exc
    if len(raw) > _ATTACH_MAX_BYTES:
        raise HTTPException(400, "文件过大（上限 20MB）")

    # 文件名只保留最后一段并过滤路径分隔符，防目录穿越
    safe_name = Path(body.file_name).name.replace("/", "_").replace("\\", "_") or "attachment.bin"
    d = _attach_dir()
    _cleanup_old_attachments(d)
    path = d / f"{uuid.uuid4().hex[:12]}_{safe_name}"
    try:
        path.write_bytes(raw)
    except OSError as exc:
        raise HTTPException(500, f"附件落盘失败: {exc}") from exc

    suffix = path.suffix.lower()
    try:
        if suffix in _TEXT_SUFFIXES:
            content = _decode_text(raw)
            mode = "text"
            error = ""
        else:
            from agent.builtin.markdown_convert import builtin_file_to_markdown

            result = builtin_file_to_markdown(path=str(path))
            content = result.content or ""
            mode = "markdown"
            error = "" if result.ok else (result.error or result.hint or "convert_failed")
    except Exception as exc:  # 双保险：转换层自带全兜底，这里防万一
        content, mode, error = "", "markdown", str(exc)
    finally:
        # 转换完成即删临时文件（best-effort）
        with contextlib.suppress(OSError):
            path.unlink()

    truncated = len(content) > _ATTACH_MAX_CHARS
    return {
        "ok": not error,
        "file_name": body.file_name,
        "mode": mode,
        "content": content[:_ATTACH_MAX_CHARS],
        "chars": len(content),
        "truncated": truncated,
        "error": error,
    }


# ---- history 清洗 -----------------------------------------------------------

# 只保留 user / assistant（system 由后端注入，tool 消息依赖 tool_call_id 不能单独出现）
_HISTORY_ROLES = {"user", "assistant"}
# 最多回传最近 12 轮对话，避免超长上下文拖慢推理（PrivateLLM/Ollama 自身还会再截断）
_HISTORY_MAX_MESSAGES = 24
_HISTORY_MAX_CONTENT_LEN = 4000
# 历史压缩摘要上限（前端 UI 与注入两侧共用；超出截断）
_HISTORY_SUMMARY_MAX_LEN = 2000
# 压缩时单条历史内容上限（比发送链路宽，让摘要能看到更完整的旧对话）
_COMPRESS_MAX_CONTENT_LEN = 8000
_COMPRESS_MAX_MESSAGES = 60


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
