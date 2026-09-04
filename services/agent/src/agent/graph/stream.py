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
import time
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
    # 执行过程可视化（Claude Code 式） SSE 三处同步（CLAUDE.md §4）
    # run_started：流建立后第一帧；tool_progress：长耗时工具阶段文案；
    # shell_chunk：shell 执行期间流式输出片段（结束帧带 exit_code）；
    # file_write_preview：写类工具落盘前下发 unified diff 供前端预览+审批。
    "run_started": "agent://run_started",
    "tool_progress": "agent://tool_progress",
    "shell_chunk": "agent://shell_chunk",
    "file_write_preview": "agent://file_write_preview",
    # 回答逐字流式（2026-09-03）SSE 三处同步（CLAUDE.md §4）
    # answer_delta：responder 终答路径的 token 增量（含 msgId，终答 message 同 id 覆盖）
    "answer_delta": "agent://answer_delta",
    # Phase 19 V0：自进化闭环 SSE 三处同步（CLAUDE.md §4）
    # evolution_insight_created：失败反思产出新经验（前端经验库页刷新）
    # Phase 19 V1：skill_draft_ready 技能蒸馏草稿待审（前端技能页草稿区刷新）
    # Phase 19 V1.5：evolution_experiment_done Prompt 优化实验完成（前端实验面板刷新）
    "evolution_insight_created": "agent://evolution_insight_created",
    "skill_draft_ready": "agent://skill_draft_ready",
    "evolution_experiment_done": "agent://evolution_experiment_done",
}


def _sse_event(event: str, data: Any) -> dict:
    """构建 sse-starlette 兼容的字典。"""
    return {"event": event, "data": json.dumps(data, default=str, ensure_ascii=False)}


# 心跳保活间隔（秒）—— BUGFIX #118：Rust sse_bridge 的 reqwest 客户端 read_timeout=60s，
# LLM 慢调用 / HITL 等审批期间流长时间无字节，客户端会主动断开 → uvicorn 取消图任务 →
# CancelledError 穿透 except Exception → done/error 发不出，前端永久卡「思考中」。
# 每 15s 发一条具名 heartbeat 事件（BUGFIX #161：原为 SSE 注释行，reqwest 层面保活但
# 前端不可见；改具名事件后前端看门狗能感知流静默，断连超阈主动解锁防永久卡死）。
_HEARTBEAT_INTERVAL_SEC = 15.0

# 无图块熔断（BUGFIX #152）：心跳只能防「客户端静默断连」，防不了「图在终止环节
# 挂死而心跳照常发」（实测：responder 已产出终答但 astream 不退出，流被心跳保活，
# read_timeout 永不触发 → 前端转圈几分钟不停）。合法场景里 HITL 等待也有 0.25s 一次的
# 快照块，超过该阈值零块必属病态 → 主动收尾发 done 让前端解锁。
_MAX_SILENCE_SEC = 600.0

# 过程事件轮询间隔（执行过程可视化）：图块到达间隔内（如单条长耗时工具执行期间）
# 细粒度事件（shell_chunk / tool_progress）也不能被卡到节点结束才下发，
# wait_for 超时改用小间隔轮询，心跳仍按 15s 节奏。
_EVENT_POLL_INTERVAL_SEC = 0.4

# Phase 19 V0：收尾后台任务强引用集合 —— 事件循环对 create_task 产物只持弱引用，
# 轨迹抽取/反思任务挂起在 I/O 上时可能被 GC 回收导致进化链路静默丢失；
# 与 evolution/api.py 的 _BACKGROUND_TASKS 同范式，完成后 discard。
_EVOLUTION_BG_TASKS: set[asyncio.Task[Any]] = set()


# ---- 协作式取消旗标（执行过程可视化）--------------------------------------
#
# 前端停止按钮既有链路是「关 SSE 连接 → uvicorn 取消图任务」，但取消信号在
# HITL 等待 / 图节点内部长操作时不一定及时穿透。这里维护 run 级旗标：
#   - api/chat.py 的 /chat/{run_id}/cancel 写入旗标；
#   - 本文件流循环每次轮询检查，命中即提前收尾发 done；
#   - DynamicToolLoop 每轮工具边界检查，命中即短路出循环。
_CANCELLED_RUNS: set[str] = set()


def request_run_cancel(run_id: str) -> None:
    """置 run 取消旗标（幂等；空 run_id 忽略）。"""
    rid = str(run_id or "").strip()
    if rid:
        _CANCELLED_RUNS.add(rid)


def is_run_cancelled(run_id: str) -> bool:
    """查 run 是否已被请求取消（图节点工具循环边界调用）。"""
    return str(run_id or "").strip() in _CANCELLED_RUNS


def clear_run_cancel(run_id: str) -> None:
    """清除旗标（流收尾时调用，防集合无界增长与旧旗标误伤复用 run_id）。"""
    _CANCELLED_RUNS.discard(str(run_id or "").strip())


def _sse_heartbeat(run_id: str) -> dict[str, str]:
    """具名心跳事件（BUGFIX #161）：前端看门狗据此感知流存活。"""
    return _sse_event("heartbeat", {"kind": "heartbeat", "runId": run_id})


def _task_artifact_note(task_id: Any) -> str | None:
    """任务台账锚点（2026-08-26）：本任务此前交付的文件是硬事实，
    以 system 消息注入初始 messages，防模型对「太丑了/改一下」类追问反问「看不到你说的内容」。
    事实不可压（上下文锚点策略）：文件路径永远原样保留。
    """
    tid = str(task_id or "").strip()
    if not tid:
        return None
    try:
        from agent.paths import ledger_read

        artifacts = ledger_read(tid).get("artifacts") or []
    except Exception:
        return None
    if not artifacts:
        return None
    lines = [
        "【本任务已交付的文件（用户提到“这个/那份/上面的文件”时指以下路径，禁止反问看不到内容）】"
    ]
    lines.extend(f"- {p}" for p in artifacts[-10:])
    return "\n".join(lines)


def _task_done_payload(extra_state: dict | None) -> dict:
    """done 事件附带的任务级工作目录信息（2026-08-26）：前端据此弹验收清理卡。"""
    payload: dict = {}
    tid = str((extra_state or {}).get("task_id") or "").strip()
    if not tid:
        return payload
    try:
        from agent.paths import task_dir

        d = task_dir(tid, str((extra_state or {}).get("task_title") or ""))
        if d is not None:
            payload["taskId"] = tid
            payload["taskDir"] = str(d)
    except Exception:
        pass
    return payload


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
        # 任务台账锚点（2026-08-26）：此前交付的文件路径注入为 system 事实，
        # 让终答/工具链都知道「太丑了」指的是哪个文件（不依赖模型自己翻历史）
        artifact_note = _task_artifact_note(extra_state.get("task_id"))
        if artifact_note:
            initial_state["messages"] = [
                {"role": "system", "content": artifact_note},
            ] + initial_state["messages"]
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
    # 同一 run 内终答消息的固定 id（BUGFIX #142）：终答会被后续节点精修
    # （工具循环先出原文 → responder 补 clarify 选项块 / 中文重写），内容变化会
    # 逃过 #115 的内容去重；复用同一 message id 让前端按 id update 原地覆盖，
    # 而不是 append 出第二条几乎相同的 assistant 消息。空列表 = 尚未分配。
    final_answer_msg_id: list[str] = []
    # 已下发的 trace 条目数（用列表包装以便在 _convert_chunk 里更新）：
    # 增量下发所有新增条目，工具循环每步操作都能进思维链（2026-08-17）
    sent_trace_count: list[int] = [len(initial_state.get("trace") or [])]
    # Phase 19 V0：跟踪最近一次全量状态快照（done 前抽任务轨迹用）
    last_values: dict | None = None

    # 回答逐字流式（2026-09-03）：从首条 answer_delta 种子化 final_answer_msg_id，
    # 终答 message 事件即复用同一 id（#142 原地覆盖机制），流式草稿与终稿收敛为同一条气泡。
    _answer_delta_channel = _CHANNEL_BY_KIND["answer_delta"]

    def _seed_answer_msg_id(events: list[dict]) -> None:
        if final_answer_msg_id:
            return
        for evt in events:
            if evt.get("event") != _answer_delta_channel:
                continue
            try:
                payload = json.loads(evt.get("data") or "{}")
            except ValueError:
                continue
            mid = str(payload.get("msgId") or "")
            if mid:
                final_answer_msg_id.append(mid)
                return

    # 心跳保活（BUGFIX #118）：把 astream 包成单任务逐块 await，等待超过
    # _HEARTBEAT_INTERVAL_SEC 就先 yield 一条注释行保活再继续等（任务不取消，
    # 图执行不受影响）。防 Rust reqwest read_timeout=60s 静默断连。
    astream_iter = graph.astream(initial_state, cfg, stream_mode=["values", "updates"]).__aiter__()
    next_task: asyncio.Task[Any] | None = None
    last_chunk_ts = time.monotonic()
    # 执行过程可视化：心跳独立计时（轮询间隔缩小到 0.4s 后不能每次都发心跳）
    last_heartbeat_ts = time.monotonic()
    cancelled_by_flag = False

    # 执行过程可视化：run 显式开始 —— 流建立后第一帧，前端据此锁定页签忙碌态，
    # 与 done 配对形成 run 生命周期闭环（多会话并发时不靠流建立隐式感知）。
    yield _sse_event("run_started", {"kind": "run_started", "runId": run_id})

    try:
        while True:
            # 协作式取消（执行过程可视化）：前端 POST /chat/{run_id}/cancel 置旗标，
            # 这里在图块边界命中即提前收尾（下方安全路径发 done）。
            if is_run_cancelled(run_id):
                cancelled_by_flag = True
                break
            if next_task is None:
                next_task = asyncio.ensure_future(astream_iter.__anext__())
            try:
                chunk = await asyncio.wait_for(
                    asyncio.shield(next_task),
                    # 轮询间隔取两者较小：生产态 = 0.4s（过程事件及时下发）；
                    # 测试 monkeypatch 心跳间隔时随动（回归套依赖心跳节奏）。
                    timeout=min(_EVENT_POLL_INTERVAL_SEC, _HEARTBEAT_INTERVAL_SEC),
                )
            except asyncio.TimeoutError:
                # BUGFIX #152：超过熔断阈值仍零图块 → 图已病态挂死，主动收尾（下方
                # 安全路径发 done）；否则心跳会把这条死流永远保活，前端永久转圈。
                if time.monotonic() - last_chunk_ts > _MAX_SILENCE_SEC:
                    break
                # 执行过程可视化：图块间隔内（单条长耗时工具执行期间）也要把已
                # emit 的细粒度事件（shell_chunk / tool_progress / answer_delta）推出去，
                # 不能卡到节点结束才下发。
                pending_evts = await _drain_process_events()
                _seed_answer_msg_id(pending_evts)
                for evt in pending_evts:
                    yield evt
                if time.monotonic() - last_heartbeat_ts >= _HEARTBEAT_INTERVAL_SEC:
                    last_heartbeat_ts = time.monotonic()
                    yield _sse_heartbeat(run_id)
                continue
            except StopAsyncIteration:
                break
            next_task = None
            last_chunk_ts = time.monotonic()
            if not isinstance(chunk, tuple) or len(chunk) != 2:
                continue
            mode, payload = chunk
            # Phase 19 V0：留存最近全量快照（任务收尾轨迹抽取用，best-effort）
            if mode == "values" and isinstance(payload, dict):
                last_values = payload
            # 回答逐字流式（2026-09-03）：先 drain builtin 队列（含 answer_delta）并种子化
            # msgId，再转换图块——保证终答 message 事件永远复用草稿 id（delta 与节点
            # 完成落在同一批时也不会发出两个 id，前端不会拼出第二条气泡）。
            builtin_evts = await _drain_builtin_events()
            _seed_answer_msg_id(builtin_evts)
            for evt in builtin_evts:
                yield evt
            for event in _convert_chunk(
                mode,
                payload,
                run_id,
                emitted_approvals,
                sent_trace_count,
                emitted_final_answers,
                final_answer_msg_id,
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
            # Phase 19 V0：消费自进化后台事件（新经验产出）并推到 SSE 流
            for evt in await _drain_evolution_events():
                yield evt
            # （builtin 队列已前置到图块转换之前 drain，含 answer_delta 种子化）
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
        # 正常结束：缓冲事件收尾 + done 放在主流程安全路径（BUGFIX #152：
        # 此前放 finally 里，消费方一关连接 yield 抛 GeneratorExit → done 永远
        # 发不出，前端 busy 永久锁死；现只在生成器正常活着时发）
        for evt in await _drain_router_events():
            yield evt
        for evt in await _drain_biznav_events():
            yield evt
        for evt in await _drain_skill_events():
            yield evt
        for evt in await _drain_evolution_events():
            yield evt
        builtin_tail = await _drain_builtin_events()
        _seed_answer_msg_id(builtin_tail)
        for evt in builtin_tail:
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
        # Phase 19 V0：任务收尾轨迹抽取（自进化闭环起点）——后台 best-effort，
        # 失败只记日志；不阻塞 done 下发（反思也在其内部后台执行）
        if last_values is not None:
            try:
                from agent.evolution.trajectory import record_run_outcome

                task = asyncio.get_running_loop().create_task(
                    record_run_outcome(
                        run_id=run_id,
                        user_prompt=prompt,
                        state=dict(last_values),
                    )
                )
                _EVOLUTION_BG_TASKS.add(task)
                task.add_done_callback(_EVOLUTION_BG_TASKS.discard)
            except Exception:
                pass  # best-effort：进化失败绝不影响主链路
        yield _sse_event(
            "done",
            {
                "kind": "done",
                "runId": run_id,
                # 协作式取消命中时标记（前端据此展示「已停止」而非正常完结）
                **({"cancelled": True} if cancelled_by_flag else {}),
                **_task_done_payload(extra_state),
            },
        )
    except asyncio.CancelledError:
        # BUGFIX #152：取消源自消费方关连接（客户端断开 / Rust 桥 cancel），
        # 此时 yield 要么写不出去、要么违反 async 生成器 GeneratorExit 协议
        # （catch 后继续执行会 RuntimeError）——直接重抛；终止信号由 Rust 桥
        # 自己的 DONE(cancelled)/ERROR 兑底（sse_bridge 两条分支都有）。
        raise
    except Exception as exc:
        # 图执行异常：连接通常还活着，error + done 都发（前端靠 done 解 busy）
        yield _sse_event(
            "error",
            {
                "kind": "error",
                "message": f"{type(exc).__name__}: {exc}",
                "runId": run_id,
            },
        )
        yield _sse_event(
            "done", {"kind": "done", "runId": run_id, **_task_done_payload(extra_state)}
        )
    finally:
        # BUGFIX #152：finally 只做同步清理，不 yield 不 await —— 消费方已关闭时
        # 这里 yield 会抛 GeneratorExit 打断收尾，是本次 done 丢失的根因。
        # 执行过程可视化：清除取消旗标，防集合无界增长与旧旗标误伤复用 run_id。
        clear_run_cancel(run_id)
        if next_task is not None and not next_task.done():
            next_task.cancel()


async def _drain_process_events() -> list[dict]:
    """一次性排空所有进程内事件队列（执行过程可视化轮询排空用）。

    顺序与主循环 / 收尾处的逐个 drain 一致；每个 drain 自带 best-effort 兜底。
    """
    events: list[dict] = []
    for drain in (
        _drain_router_events,
        _drain_biznav_events,
        _drain_skill_events,
        _drain_builtin_events,
        _drain_image_events,
        _drain_ssh_events,
        _drain_audit_events,
        _drain_doc_review_events,
        _drain_orchestrator_events,
        _drain_preview_events,
    ):
        events.extend(await drain())
    return events


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


async def _drain_evolution_events() -> list[dict]:
    """Phase 19 V0：从 evolution.events in-process deque 拉已 emit 的事件，转 SSE。

    反思后台任务通过 `emit_evolution_event()` 写入 deque；本函数被 stream 循环
    + 收尾各调一次，把 buffered 事件全部推到 SSE 前端。
    """
    events: list[dict] = []
    try:
        from agent.evolution.events import consume_evolution_events

        raw = await consume_evolution_events(timeout_s=0.0)
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
    final_answer_msg_id: list[str] | None = None,
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
                                # 多会话并发（2026-08-26）：审批卡也按 run 归属页签路由，
                                # 避免并发时 A 会话的审批弹到 B 会话
                                "runId": run_id,
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
                # 固定消息 id（BUGFIX #142）：同一 run 内终答被后续节点精修时内容变化，
                # 会逃过 #115 的内容去重；首次分配一个 id，后续精修复用它，前端
                # 按 id update 原地覆盖 → 对话里始终只有一条终答，不会 append 出重复消息。
                if final_answer_msg_id is None:
                    msg_id = str(uuid.uuid4())
                elif final_answer_msg_id:
                    msg_id = final_answer_msg_id[0]
                else:
                    msg_id = str(uuid.uuid4())
                    final_answer_msg_id.append(msg_id)
                events.append(
                    {
                        "event": "message",
                        "data": {
                            "kind": "message",
                            "message": {
                                "id": msg_id,
                                "role": "assistant",
                                "content": final_answer,
                                # 多会话并发（2026-08-26）：消息带 run 归属，
                                # 前端按 runId→页签路由，两个会话的流不串台
                                "runId": run_id,
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
            # 工具调用配对标识（根治 BUGFIX #164）：tool_call 与 tool_result
            # 此前各自 uuid4，前端无从配对 → running 卡片永久转圈。call_id 由
            # tool_runner / builtin dispatcher 写进 call 字典，两条事件同源读取。
            _pending_call = delta.get("pending_tool_call")
            _call_id = None
            _call_name = None
            if isinstance(_pending_call, dict):
                _call_id = _pending_call.get("call_id")
                _call_name = _pending_call.get("name")
            if delta.get("pending_tool_call"):
                events.append(
                    {
                        "event": "tool_call",
                        "data": {
                            "kind": "tool_call",
                            "id": str(uuid.uuid4()),
                            "callId": _call_id,
                            "call": delta["pending_tool_call"],
                            # 多会话并发（2026-08-26）：携带 run 归属供前端按页签路由
                            "runId": run_id,
                        },
                    }
                )
            if delta.get("tool_result") is not None:
                _result = delta["tool_result"]
                # name 回填：ToolResult.to_dict() 与 MCP invoke 返回值都不带 name，
                # 但 TS 侧 ToolResult 声明 name 必填（协议漂移）。这里按 call 补齐，
                # 让 callId 配对失败时前端仍有 name 兜底。
                if isinstance(_result, dict) and not _result.get("name") and _call_name:
                    _result = {**_result, "name": _call_name}
                events.append(
                    {
                        "event": "tool_result",
                        "data": {
                            "kind": "tool_result",
                            "id": str(uuid.uuid4()),
                            "callId": _call_id,
                            "result": _result,
                            "runId": run_id,
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
                            "runId": run_id,
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
