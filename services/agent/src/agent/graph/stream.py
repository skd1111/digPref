"""SSE 流适配器 —— 将 LangGraph 事件转换为 AgentStreamEvent 字典。

通道名与 `apps/desktop/src/ipc/events.ts::EVT.*` 保持一致。

V2 增量：
    - 补 3 个 LLM 路由事件通道（`llm_route_decided` / `llm_degraded` /
      `llm_budget_alert`）—— Rust 侧 `stream/sse_bridge.rs::channel` + TS 侧
      `ipc/events.ts::EVT` 已声明（CLAUDE.md §4 三处同步），Python 侧补齐。
    - 在流循环里消费 `agent.llm.metrics.consume_router_events()`，把 RouterEngine
      决策后 emit 的事件推到 SSE `data:` 行。

V1.3 (Phase 2G)：补 3 个 biznav 事件通道（`biznav_yaml_reloaded` /
`biznav_feature_affected` / `biznav_extraction_done`）—— biznav/events.py 进程内
deque → 本文件 `consume_biznav_events()` 拉出来转 SSE 推到前端。Rust 侧
`stream/sse_bridge.rs::channel` + TS 侧 `ipc/events.ts::EVT` 同步注册。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

# ---- 通道映射 ---------------------------------------------------------------

_CHANNEL_BY_KIND = {
    "message": "agent://message",
    "tool_call": "agent://tool_call",
    "tool_result": "agent://tool_result",
    "trace": "agent://trace",
    "approval": "agent://approval",
    "log": "agent://log",
    # Phase 2C V2.0：LLM 路由事件三处同步（CLAUDE.md §4）
    "llm_route_decided": "agent://llm_route_decided",
    "llm_degraded": "agent://llm_degraded",
    "llm_budget_alert": "agent://llm_budget_alert",
    # Phase 17：缓存命中统计 SSE 三处同步（CLAUDE.md §4）
    "llm_cache_stats": "agent://llm_cache_stats",
    # Phase 2G V1.3：业务功能点 SSE 三处同步（CLAUDE.md §4）
    "biznav_yaml_reloaded": "agent://biznav_yaml_reloaded",
    "biznav_feature_affected": "agent://biznav_feature_affected",
    "biznav_extraction_done": "agent://biznav_extraction_done",
    # Phase 2D V1：Skill YAML 热加载 SSE 三处同步（CLAUDE.md §4）
    "skill_matched": "agent://skill_matched",
    # Phase 4 V0：本地端侧模型 SSE 三处同步（CLAUDE.md §4）
    "localai_ready": "agent://localai_ready",
    "localai_error": "agent://localai_error",
    # Phase 2F+ V1：日志分析 SSE 三处同步（CLAUDE.md §4）
    "log_analysis_started": "agent://log_analysis_started",
    "log_analysis_done": "agent://log_analysis_done",
    "log_analysis_error": "agent://log_analysis_error",
    # Phase 12 V0/V1：多智能体调度 SSE 三处同步（CLAUDE.md §4）
    "sub_agent_spawn": "agent://sub_agent_spawn",
    "sub_agent_done": "agent://sub_agent_done",
    "sub_agent_progress": "agent://sub_agent_progress",
    # Phase 13 DSpark：推测解码 SSE 三处同步（CLAUDE.md §4）
    "dspark_acceleration_status": "agent://dspark_acceleration_status",
    # Phase 1B V1：原生工具 SSE 三处同步（CLAUDE.md §4）
    # started/done/denied 三事件：started 在 dispatcher 进入前 emit（含 tool_name + risk_level），
    # done 在 ToolResult 出来后 emit（含 ok + content_size + elapsed_ms），
    # denied 在 HITL 拒绝后 emit（含 reason）。
    "builtin_tool_started": "agent://builtin_tool_started",
    "builtin_tool_done": "agent://builtin_tool_done",
    "builtin_tool_denied": "agent://builtin_tool_denied",
    # Phase 14 V0：图像处理 SSE 三处同步（CLAUDE.md §4）
    # started/done/error 三事件：started 在 backend 执行前 emit（含 processing_type + task_id），
    # done 在 backend 返回后 emit（含 ok + elapsed_ms），
    # error 在异常分支 emit（含 error message）。
    "image_processing_started": "agent://image_processing_started",
    "image_processing_done": "agent://image_processing_done",
    "image_processing_error": "agent://image_processing_error",
    # Phase 2B V0：SSH 会话 SSE 三处同步（CLAUDE.md §4）
    # connected/disconnected/command_done/error 四事件
    "ssh_connected": "agent://ssh_connected",
    "ssh_disconnected": "agent://ssh_disconnected",
    "ssh_command_done": "agent://ssh_command_done",
    "ssh_error": "agent://ssh_error",
    # Phase 18 双框架 SSE 三处同步（CLAUDE.md §4）
    # mode_routed：ModeRouter 路由结果（前端路由徽标 + 偏离声明）
    # repair_attempt：Auto-Repair 循环进度（思维链实时展示）
    # auto_decision：自动模式决策（可跳审计详情）
    "mode_routed": "agent://mode_routed",
    "repair_attempt": "agent://repair_attempt",
    "auto_decision": "agent://auto_decision",
    # Phase 5 V0：审核专家 SSE 三处同步（CLAUDE.md §4）
    # task_pending/task_decided/evidence_added/compliance_done 四事件
    "audit_task_pending": "agent://audit_task_pending",
    "audit_task_decided": "agent://audit_task_decided",
    "audit_evidence_added": "agent://audit_evidence_added",
    "audit_compliance_done": "agent://audit_compliance_done",
    # 文档风险合规审核（审核专家 · 文档审核）SSE 三处同步（CLAUDE.md §4）
    # started/classified/findings_ready/failed 四事件
    "doc_review_started": "agent://doc_review_started",
    "doc_review_classified": "agent://doc_review_classified",
    "doc_review_findings_ready": "agent://doc_review_findings_ready",
    "doc_review_failed": "agent://doc_review_failed",
    # Phase 7 V0：数据专家 SSE 三处同步（CLAUDE.md §4）
    # data_query_result/data_python_result/data_chart_ready/data_export_done 四事件
    "data_query_result": "agent://data_query_result",
    "data_python_result": "agent://data_python_result",
    "data_chart_ready": "agent://data_chart_ready",
    "data_export_done": "agent://data_export_done",
    # Phase 6 V1.5：会话管理 SSE 三处同步（CLAUDE.md §4）
    # compression_applied：CompressionRouter 选策略后实际执行压缩 → 前端可显示压缩提示
    # memory_consolidated：L3 情景记忆 → 语义记忆蒸馏完成（后台任务）
    "session_compression_applied": "agent://session_compression_applied",
    "session_memory_consolidated": "agent://session_memory_consolidated",
    # Phase 15 V0：前端实时预览引擎 SSE 三处同步（CLAUDE.md §4）
    # hmr_connected：HMR WebSocket 连接成功（含 session_id + status）
    # hmr_disconnected：HMR 断开 / 重连中（含 session_id + status）
    # build_error：Vite 编译错误（含 session_id + error + file/line/column）
    "preview_hmr_connected": "agent://preview_hmr_connected",
    "preview_hmr_disconnected": "agent://preview_hmr_disconnected",
    "preview_build_error": "agent://preview_build_error",
}


def _sse_event(event: str, data: Any) -> dict:
    """构建 sse-starlette 兼容的字典。"""
    return {"event": event, "data": json.dumps(data, default=str, ensure_ascii=False)}


# 心跳保活间隔（秒）—— BUGFIX #118：Rust sse_bridge 的 reqwest 客户端 read_timeout=60s，
# LLM 慢调用 / HITL 等审批期间流长时间无字节，客户端会主动断开 → uvicorn 取消图任务 →
# CancelledError 穿透 except Exception → done/error 发不出，前端永久卡「思考中」。
# 每 15s 发一条 SSE 注释行（":" 开头，EventSource/reqwest-eventsource 均忽略）保活。
_HEARTBEAT_INTERVAL_SEC = 15.0


def _sse_heartbeat() -> dict[str, str]:
    """SSE 注释行心跳（event 为空时 api 层只发 `data:` 行）。"""
    return {"event": "", "data": "heartbeat"}


# ---- 公开入口 ---------------------------------------------------------------


async def stream_graph_events(
    graph,
    run_id: str,
    prompt: str,
    config: dict | None = None,
    extra_state: dict | None = None,
) -> AsyncIterator[dict]:
    """从编译后的 graph.astream 生成 SSE 事件字典。

    Usage:
        async for evt in stream_graph_events(graph, run_id, prompt):
            yield _sse_event(evt["event"], evt["data"])
    """
    cfg = {"configurable": {"thread_id": run_id}}
    if config:
        cfg.update(config)

    # 使用 empty_state() 确保初始状态与状态定义完全一致
    from agent.graph.state import empty_state

    initial_state = empty_state(prompt)
    # 携带 run_id 到状态机：自动多智能体节点用它作为子 Agent 的 parent_run_id
    initial_state["run_id"] = run_id
    # Phase 18：chat 请求透传的会话级字段（work_mode / autonomy 等）
    if extra_state:
        # 会话上下文（2026-08-06）：history 不是 state 字段，单独取出，
        # 拼在当前用户消息之前，让 intent/编排/工具循环都能看到历史对话
        history = extra_state.pop("history", None) or []
        # 历史压缩摘要（2026-08-17）：断点之前的旧对话已被压缩成摘要，
        # 作为 system 消息置于 history 之前（模型先读到背景再看近期对话）
        history_summary = extra_state.pop("history_summary", None)
        if history or history_summary:
            prefix: list[dict] = []
            if history_summary:
                prefix.append(
                    {
                        "role": "system",
                        "content": f"【前段对话摘要（更早的对话已被压缩，据此保持上下文连贯）】\n{history_summary}",
                    }
                )
            initial_state["messages"] = prefix + list(history) + initial_state["messages"]
        initial_state.update(extra_state)
    # 补充初始 trace 条目
    initial_state["trace"] = [
        _trace_entry("intent", "running", ts=datetime.now(timezone.utc).isoformat())
    ]

    # 跟踪已发送的 approval_id，避免每个 awaiting_approval=True 的快照
    # 都重复发送审批事件（导致 UI 出现多张审批卡片）
    emitted_approvals: set[str] = set()
    # 跟踪已发送的 final_answer 内容（BUGFIX #115）：values 模式下工具循环
    # 与 responder 的快照都带 final_answer，不去重会导致同一回答在 UI 出现两次
    emitted_final_answers: set[str] = set()
    # 已下发的 trace 条目数（用列表包装以便在 _convert_chunk 里更新）：
    # 增量下发所有新增条目，工具循环每步操作都能进思维链（2026-08-17）
    sent_trace_count: list[int] = [len(initial_state.get("trace") or [])]

    # 心跳保活（BUGFIX #118）：把 astream 包成单任务逐块 await，等待超过
    # _HEARTBEAT_INTERVAL_SEC 就先 yield 一条注释行保活再继续等（任务不取消，
    # 图执行不受影响）。防 Rust reqwest read_timeout=60s 静默断连。
    astream_iter = graph.astream(initial_state, cfg, stream_mode=["values", "updates"]).__aiter__()
    next_task: asyncio.Task[Any] | None = None

    try:
        while True:
            if next_task is None:
                next_task = asyncio.ensure_future(astream_iter.__anext__())
            try:
                chunk = await asyncio.wait_for(
                    asyncio.shield(next_task), timeout=_HEARTBEAT_INTERVAL_SEC
                )
            except asyncio.TimeoutError:
                yield _sse_heartbeat()
                continue
            except StopAsyncIteration:
                break
            next_task = None
            if not isinstance(chunk, tuple) or len(chunk) != 2:
                continue
            mode, payload = chunk
            for event in _convert_chunk(
                mode, payload, run_id, emitted_approvals, sent_trace_count, emitted_final_answers
            ):
                yield _sse_event(event["event"], event["data"])
            # V2 增量：消费 RouterEngine emit 的路由事件并推到 SSE 流
            for evt in await _drain_router_events():
                yield evt
            # V1.3 增量：消费 biznav 后台事件并推到 SSE 流
            for evt in await _drain_biznav_events():
                yield evt
            # Phase 2D V1：消费 skill 后台事件并推到 SSE 流
            for evt in await _drain_skill_events():
                yield evt
            # Phase 1B V1：消费 builtin 后台事件并推到 SSE 流
            for evt in await _drain_builtin_events():
                yield evt
            # Phase 14 V0：消费 image_processing 后台事件并推到 SSE 流
            for evt in await _drain_image_events():
                yield evt
            # Phase 2B V0：消费 SSH 后台事件并推到 SSE 流
            for evt in await _drain_ssh_events():
                yield evt
            # Phase 5 V0：消费 audit 后台事件并推到 SSE 流
            for evt in await _drain_audit_events():
                yield evt
            # 文档风险合规审核：消费 doc_review 后台事件并推到 SSE 流
            for evt in await _drain_doc_review_events():
                yield evt
            # Phase 12 V1.5：消费 orchestrator 子 Agent 事件 + HITL 反向 approval
            for evt in await _drain_orchestrator_events():
                yield evt
            # Phase 15 V0：消费 preview HMR / 编译错误事件并推到 SSE 流
            for evt in await _drain_preview_events():
                yield evt
    except asyncio.CancelledError:
        # BUGFIX #118：CancelledError 是 BaseException，下方 except Exception 接不住；
        # 客户端断开 / 超时取消时补发 error，让还活着的连接能收到终止信号
        # （done 由 finally 统一补发）
        yield _sse_event(
            "error",
            {"kind": "error", "message": "运行被取消（客户端断开或超时）"},
        )
    except Exception as exc:
        yield _sse_event(
            "error",
            {
                "kind": "error",
                "message": f"{type(exc).__name__}: {exc}",
            },
        )
    finally:
        # 提前退出（异常/消费方关闭）时收拾掉仍在等待的 astream 任务
        if next_task is not None and not next_task.done():
            next_task.cancel()
        # 最后再 drain 一次，确保所有 buffered 路由事件都被推完
        for evt in await _drain_router_events():
            yield evt
        for evt in await _drain_biznav_events():
            yield evt
        for evt in await _drain_skill_events():
            yield evt
        for evt in await _drain_builtin_events():
            yield evt
        for evt in await _drain_image_events():
            yield evt
        for evt in await _drain_ssh_events():
            yield evt
        for evt in await _drain_audit_events():
            yield evt
        for evt in await _drain_doc_review_events():
            yield evt
        for evt in await _drain_orchestrator_events():
            yield evt
        for evt in await _drain_preview_events():
            yield evt
        yield _sse_event("done", {"kind": "done", "runId": run_id})


async def _drain_router_events() -> list[dict]:
    """V2 增量：从 metrics in-process queue 拉已 emit 的路由事件，转为 SSE 事件列表。

    返回 sse-starlette 兼容的字典列表，调用方直接 yield。
    """
    events: list[dict] = []
    try:
        from agent.llm.metrics import consume_router_events

        raw = await consume_router_events(timeout_s=0.0)
        for kind, payload in raw:
            channel = _CHANNEL_BY_KIND.get(kind)
            if channel:
                events.append(
                    {"event": channel, "data": json.dumps(payload, default=str, ensure_ascii=False)}
                )
    except Exception:
        pass  # best-effort
    return events


async def _drain_biznav_events() -> list[dict]:
    """V1.3 增量：从 biznav.events in-process deque 拉已 emit 的事件，转 SSE。

    biznav 后台任务（hot_reload / incremental / extraction）通过
    `emit_biznav_event()` 写入 deque；本函数被 stream 循环 + finally 各调一次，
    把 buffered 事件全部推到 SSE 前端。
    """
    events: list[dict] = []
    try:
        from agent.biznav.events import consume_biznav_events

        raw = await consume_biznav_events(timeout_s=0.0)
        for kind, payload in raw:
            channel = _CHANNEL_BY_KIND.get(kind)
            if channel:
                events.append(
                    {"event": channel, "data": json.dumps(payload, default=str, ensure_ascii=False)}
                )
    except Exception:
        pass  # best-effort
    return events


async def _drain_skill_events() -> list[dict]:
    """Phase 2D V1：从 skills.events in-process deque 拉已 emit 的事件，转 SSE。

    SkillWatchdog（watchfiles 监听 .yaml 变更）通过 emit_skill_event() 写入
    deque；本函数被 stream 循环 + finally 各调一次。
    """
    events: list[dict] = []
    try:
        from agent.skills.events import consume_skill_events

        raw = await consume_skill_events(timeout_s=0.0)
        for kind, payload in raw:
            channel = _CHANNEL_BY_KIND.get(kind)
            if channel:
                events.append(
                    {"event": channel, "data": json.dumps(payload, default=str, ensure_ascii=False)}
                )
    except Exception:
        pass  # best-effort
    return events


async def _drain_builtin_events() -> list[dict]:
    """Phase 1B V1：从 builtin.events in-process deque 拉已 emit 的事件，转 SSE。

    ToolDispatcher 在 V1 调用 `emit_builtin_event("builtin_tool_started"/"done"/"denied")`
    写入 deque；本函数被 stream 循环 + finally 各调一次。
    """
    events: list[dict] = []
    try:
        from agent.builtin.events import consume_builtin_events

        raw = await consume_builtin_events(timeout_s=0.0)
        for kind, payload in raw:
            channel = _CHANNEL_BY_KIND.get(kind)
            if channel:
                events.append(
                    {"event": channel, "data": json.dumps(payload, default=str, ensure_ascii=False)}
                )
    except Exception:
        pass  # best-effort
    return events


async def _drain_image_events() -> list[dict]:
    """Phase 14 V0：从 image_processing.events in-process deque 拉已 emit 的事件，转 SSE。

    api.py 在每个处理前后 emit started / done / error；本函数被 stream 循环 + finally 各调一次。
    """
    events: list[dict] = []
    try:
        from agent.image_processing.events import consume_events

        raw = await consume_events(timeout_s=0.0)
        for kind, payload in raw:
            channel = _CHANNEL_BY_KIND.get(kind)
            if channel:
                events.append(
                    {"event": channel, "data": json.dumps(payload, default=str, ensure_ascii=False)}
                )
    except Exception:
        pass  # best-effort
    return events


async def _drain_ssh_events() -> list[dict]:
    """Phase 2B V0：从 ssh.events in-process deque 拉已 emit 的事件，转 SSE。

    api.py 在每次 SSH 操作 emit connected / disconnected / command_done / error；
    本函数被 stream 循环 + finally 各调一次。
    """
    events: list[dict] = []
    try:
        from agent.ssh.events import consume_events

        raw = await consume_events(timeout_s=0.0)
        for kind, payload in raw:
            channel = _CHANNEL_BY_KIND.get(kind)
            if channel:
                events.append(
                    {"event": channel, "data": json.dumps(payload, default=str, ensure_ascii=False)}
                )
    except Exception:
        pass  # best-effort
    return events


async def _drain_audit_events() -> list[dict]:
    """Phase 5 V0：从 audit_expert.events in-process deque 拉已 emit 的事件，转 SSE。

    api.py 在每次审批操作 emit task_pending / task_decided / evidence_added / compliance_done；
    本函数被 stream 循环 + finally 各调一次。
    """
    events: list[dict] = []
    try:
        from agent.audit_expert.events import consume_events

        raw = await consume_events(timeout_s=0.0)
        for kind, payload in raw:
            channel = _CHANNEL_BY_KIND.get(kind)
            if channel:
                events.append(
                    {"event": channel, "data": json.dumps(payload, default=str, ensure_ascii=False)}
                )
    except Exception:
        pass  # best-effort
    return events


async def _drain_doc_review_events() -> list[dict]:
    """文档审核：从 doc_review.events in-process deque 拉事件转 SSE。"""
    events: list[dict] = []
    try:
        from agent.doc_review.events import consume_events

        raw = await consume_events(timeout_s=0.0)
        for kind, payload in raw:
            channel = _CHANNEL_BY_KIND.get(kind)
            if channel:
                events.append(
                    {"event": channel, "data": json.dumps(payload, default=str, ensure_ascii=False)}
                )
    except Exception:
        pass  # best-effort
    return events


async def _drain_orchestrator_events() -> list[dict]:
    """Phase 12 V1.5：从 orchestrator.events in-process deque 拉已 emit 的事件，转 SSE。

    覆盖 4 类通道：
      - `sub_agent_spawn` / `sub_agent_progress` / `sub_agent_done`（V0/V1 已声明通道）
      - `approval`（V1.5 新增 —— 复用主图既有 approval 通道，前端 ApprovalCard 零改动）
    """
    events: list[dict] = []
    try:
        from agent.orchestrator.events import consume_orchestrator_events

        raw = await consume_orchestrator_events()
        for kind, payload in raw:
            channel = _CHANNEL_BY_KIND.get(kind)
            if channel:
                events.append(
                    {
                        "event": channel,
                        "data": json.dumps(payload, default=str, ensure_ascii=False),
                    }
                )
    except Exception:
        pass  # best-effort
    return events


async def _drain_preview_events() -> list[dict]:
    """Phase 15 V0：从 preview.events in-process deque 拉已 emit 的事件，转 SSE。

    VitePreviewManager 在解析子进程 stdout/stderr 时 emit hmr_connected /
    hmr_disconnected / build_error；本函数被 stream 循环 + finally 各调一次。
    """
    events: list[dict] = []
    try:
        from agent.preview.events import consume_events

        raw = await consume_events(timeout_s=0.0)
        for kind, payload in raw:
            channel = _CHANNEL_BY_KIND.get(kind)
            if channel:
                events.append(
                    {"event": channel, "data": json.dumps(payload, default=str, ensure_ascii=False)}
                )
    except Exception:
        pass  # best-effort
    return events


# ---- Chunk → events --------------------------------------------------------


def _convert_chunk(
    mode: str,
    payload: Any,
    run_id: str,
    emitted_approvals: set[str],
    sent_trace_count: list[int] | None = None,
    emitted_final_answers: set[str] | None = None,
) -> list[dict]:
    """将一个 LangGraph 流块转换为 0..N 个 AgentStreamEvent 字典。"""
    events: list[dict] = []

    if mode == "values":
        # 全量状态快照 —— 只发送有意义的叶子变更
        if isinstance(payload, dict):
            trace = payload.get("trace") or []
            if trace:
                # 增量下发所有新增 trace 条目（2026-08-17）：工具循环一次节点
                # 执行可能产生多条 per-tool 条目（read/write/grep…），每条都要进
                # 思维链；无计数器时退化为旧行为（只发最后一条）
                # Phase 16：携带 runId —— 前端思维链面板用它查 /trace/session/{runId}
                if sent_trace_count is not None:
                    fresh = trace[sent_trace_count[0] :]
                    sent_trace_count[0] = len(trace)
                else:
                    fresh = [trace[-1]]
                for step in fresh:
                    events.append(
                        {
                            "event": "trace",
                            "data": {"kind": "trace", "step": step, "runId": run_id},
                        }
                    )
            if payload.get("awaiting_approval"):
                approval_id = payload.get("approval_id") or ""
                # 防止重复发送同一审批的卡片（每个快照都会触发此分支）
                if approval_id and approval_id not in emitted_approvals:
                    emitted_approvals.add(approval_id)
                    approval_payload = {
                        "id": approval_id,
                        "runId": run_id,
                        "plan": payload.get("pending_tool_call"),
                        "riskLevel": (payload.get("pending_tool_call") or {}).get(
                            "risk_level", "medium"
                        ),
                        "createdAt": datetime.now(timezone.utc).isoformat(),
                    }
                    # Phase 18：推荐选项（为空时前端保持二元审批）
                    opts = payload.get("approval_options")
                    if opts:
                        approval_payload["options"] = opts.get("options") or []
                        approval_payload["recommendedOptionId"] = opts.get("recommendedOptionId")
                        approval_payload["recommendationReason"] = opts.get("recommendationReason")
                    events.append(
                        {
                            "event": "approval",
                            "data": {
                                "kind": "approval",
                                "approval": approval_payload,
                            },
                        }
                    )
            final_answer = payload.get("final_answer")
            # 去重（BUGFIX #115）：同一内容只发一次；无去重集合时退化为旧行为。
            # 内容变化（如后续节点覆写为新终答）仍会照常发送。
            if final_answer and (
                emitted_final_answers is None or final_answer not in emitted_final_answers
            ):
                if emitted_final_answers is not None:
                    emitted_final_answers.add(final_answer)
                events.append(
                    {
                        "event": "message",
                        "data": {
                            "kind": "message",
                            "message": {
                                "id": str(uuid.uuid4()),
                                "role": "assistant",
                                "content": final_answer,
                            },
                        },
                    }
                )

    elif mode == "updates":
        # 每节点增量 —— 转换为类型化事件
        if not isinstance(payload, dict):
            return events
        for node_name, delta in payload.items():
            if not isinstance(delta, dict):
                continue
            if delta.get("pending_tool_call"):
                events.append(
                    {
                        "event": "tool_call",
                        "data": {
                            "kind": "tool_call",
                            "id": str(uuid.uuid4()),
                            "call": delta["pending_tool_call"],
                        },
                    }
                )
            if delta.get("tool_result") is not None:
                events.append(
                    {
                        "event": "tool_result",
                        "data": {
                            "kind": "tool_result",
                            "id": str(uuid.uuid4()),
                            "result": delta["tool_result"],
                        },
                    }
                )
            if delta.get("tool_error"):
                events.append(
                    {
                        "event": "log",
                        "data": {
                            "kind": "log",
                            "line": f"[{node_name}] error: {delta['tool_error']}",
                        },
                    }
                )
            # Phase 2D V0：检测 intent_node 路由出的 skill（C2 fix）
            if delta.get("active_skill_id"):
                routing = delta.get("skill_routing")
                events.append(
                    {
                        "event": "skill_matched",
                        "data": {
                            "kind": "skill_matched",
                            "skill_id": delta["active_skill_id"],
                            "skill_name": delta.get("active_skill_name", ""),
                            "confidence": getattr(routing, "confidence", 0) if routing else 0,
                            "matched_keywords": getattr(routing, "matched_keywords", [])
                            if routing
                            else [],
                        },
                    }
                )
            # Phase 18：mode_routed —— ModeRouter 节点产出路由结果
            if node_name == "mode_router" and delta.get("routing"):
                events.append(
                    {
                        "event": "mode_routed",
                        "data": {
                            "kind": "mode_routed",
                            "routing": delta.get("routing"),
                            "overridden": bool(delta.get("routing_overridden")),
                            "declaration": delta.get("routing_declaration"),
                            "runId": run_id,
                        },
                    }
                )
            # Phase 18：repair_attempt / auto_decision —— 从节点 trace 条目提取
            for entry in delta.get("trace") or []:
                if not isinstance(entry, dict):
                    continue
                if entry.get("kind") == "repair_attempt":
                    events.append(
                        {
                            "event": "repair_attempt",
                            "data": {
                                "kind": "repair_attempt",
                                "attempt": entry.get("attempt"),
                                "maxAttempts": entry.get("max_attempts"),
                                "validatorLevel": entry.get("validator_level"),
                                "errorSummary": entry.get("error_summary"),
                                "runId": run_id,
                            },
                        }
                    )
                reason = str(entry.get("reason") or "")
                if reason == "auto_mode:recommended" or reason.startswith("auto_approved:"):
                    events.append(
                        {
                            "event": "auto_decision",
                            "data": {
                                "kind": "auto_decision",
                                "reason": reason,
                                "option": entry.get("option"),
                                "riskLevel": entry.get("risk_level"),
                                "runId": run_id,
                            },
                        }
                    )

    return events


def _trace_entry(node: str, status: str, **extra: Any) -> dict:
    e: dict[str, Any] = {
        "node": node,
        "status": status,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    e.update(extra)
    return e
