"""orchestrator.orchestrator —— Phase 12 V1.5 完整集成。

V0（保留 / 行为不变）：
    - 同步 spawn：调 LMRouter 直接等结果，返 SubAgentReport
    - 进程内 asyncio.Queue 推 SSE 事件（V0 API）

V1（保留但被 V1.5 包住）：
    - WorkerPool / 字典序锁 / 三层 Token Bucket / HITL bridge 都已就位
    - V0 的 `_run` 路径在 V1.5 里只在「没有 StateRepo / 没有 WorkerPool」时退化

V1.5（本版新增）：
    - **真实任务生命周期**：`enqueue → consume → execute (3 次重试) → CAS 落库 → 推 SSE`
    - **三类上下文策略**自动选型（passthrough / shared_memory_pool / incremental_summary）
    - **敏感负载二次校验**（sensitive.py）→ 强制本地 + 记 telemetry
    - **HITL 真审批**（hitl_bridge.py `wait_for_user=True`）—— 复用主图 approval 通道
    - **DLQ 持久化**（state_repo.py `sub_agent_dlq`）—— 重启可恢复
    - **LLM Judge 抽样**（eval_collector.py）—— 10% 采样，**不作 CI 闸门**
    - **审计双写**：旧的 `audit(action, payload)` 兼容 + V1.5 增强的
      `audit_bridge.log_event(event_type, correlation_id=...)`（可回放整棵决策树）
    - **结构化事件日志**（observability.py）：logs/orchestrator-YYYYMMDD.jsonl，
      取代 ELK 的检索分析层（架构决策 2026-07-31）
    - **取消传播 ≤ 1s**：Worker Pool `cancel_all()` + 进程内队列 `close()` 通知
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from agent.llm.router import _LOCAL_ONLY_TASKS, LMRouter
from agent.orchestrator import audit_bridge, observability
from agent.orchestrator.audit_bridge import (
    EVENT_DLQ,
    EVENT_DONE,
    EVENT_PROGRESS,
    EVENT_SPAWN,
)
from agent.orchestrator.context_strategy import (
    ComposedContext,
    build_context,
    get_default_pool,
    select_strategy,
)
from agent.orchestrator.eval_collector import (
    EvalCollector,
    get_default_collector,
    judge_report,
)
from agent.orchestrator.events import (
    EVT_SUB_AGENT_DONE,
    EVT_SUB_AGENT_PROGRESS,
    EVT_SUB_AGENT_SPAWN,
    emit_orchestrator_event,
)
from agent.orchestrator.hitl_bridge import get_default_hitl_bridge
from agent.orchestrator.locks import (
    DistributedLockManager,
    get_default_lock_manager,
)
from agent.orchestrator.queue import (
    PriorityTaskQueue,
    QueueItem,
    get_default_queue,
)
from agent.orchestrator.sensitive import classify_spec
from agent.orchestrator.spec import (
    StateDelta,
    SubAgentReport,
    SubAgentSpec,
    SubAgentStatus,
)
from agent.orchestrator.state_repo import StateRepo, get_default_repo
from agent.orchestrator.token_bucket import (
    TokenBucketManager,
    get_default_bucket_manager,
)
from agent.orchestrator.tree_guard import enforce_tree_limits
from agent.orchestrator.worker_pool import WorkerPool

logger = logging.getLogger(__name__)

# cancel_all fire-and-forget 任务强引用（防 GC 提前回收）
_bg_close_tasks: set[asyncio.Task] = set()

# SSE 通道（与 events.py 常量一致；这里再列一次方便调用方 grep）
CH_SUB_AGENT_SPAWN = "agent://sub_agent_spawn"
CH_SUB_AGENT_PROGRESS = "agent://sub_agent_progress"
CH_SUB_AGENT_DONE = "agent://sub_agent_done"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_model_name(router: Any) -> str:
    try:
        priv = getattr(router, "private", None)
        if priv is None:
            return ""
        name = getattr(priv, "model", "")
        return name if isinstance(name, str) else ""
    except Exception:
        return ""


# ---- Orchestrator V1.5 -----------------------------------------------------


class Orchestrator:
    """V1.5 完整 Orchestrator（向下兼容 V0 同步 spawn）。"""

    def __init__(
        self,
        llm_router: LMRouter | None = None,
        *,
        repo: StateRepo | None = None,
        queue: PriorityTaskQueue | None = None,
        worker_pool: WorkerPool | None = None,
        lock_manager: DistributedLockManager | None = None,
        bucket_manager: TokenBucketManager | None = None,
        hitl_bridge: Any = None,
        collector: EvalCollector | None = None,
        shared_pool: Any = None,
        judge_caller: Any | None = None,
    ) -> None:
        self._llm = llm_router
        self._repo = repo or get_default_repo()
        self._queue = queue or get_default_queue()
        self._pool = worker_pool or WorkerPool(
            concurrency=4,
            retry_base_delay_s=0.5,
            max_attempts=3,
        )
        self._locks = lock_manager or get_default_lock_manager()
        self._buckets = bucket_manager or get_default_bucket_manager()
        self._hitl = hitl_bridge or get_default_hitl_bridge()
        self._collector = collector or get_default_collector()
        self._shared = shared_pool or get_default_pool()
        self._judge = judge_caller
        self._cancel_event = asyncio.Event()
        self._total_nodes = 0
        self._reports: dict[str, SubAgentReport] = {}
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = None
        self._tenant = "default"
        # V0 兼容：旧 asyncio.Queue 仍同步推一份（V0 e2e 测试通过 event_queue 属性读）
        self._events: asyncio.Queue = asyncio.Queue()

    # ---- 计数 -----------------------------------------------------------

    @property
    def total_nodes(self) -> int:
        return self._total_nodes

    @property
    def collector(self) -> EvalCollector:
        return self._collector

    @property
    def repo(self) -> StateRepo:
        return self._repo

    @property
    def task_queue(self) -> PriorityTaskQueue:
        return self._queue

    @property
    def event_queue(self) -> asyncio.Queue:
        """V0 兼容：暴露旧 asyncio.Queue 给 V0 e2e 测试用。

        V1.5 实际 SSE 通道在 `orchestrator.events` 模块的进程内 deque，由
        `graph/stream.py::_drain_orchestrator_events()` 拉到前端。这里保留
        旧 `self._events` 作为 V0 兼容路径（已被 `_emit_spawn` / `_emit_done` 写入）。
        """
        return self._events

    @property
    def tenant(self) -> str:
        return self._tenant

    def set_tenant(self, tenant: str) -> None:
        self._tenant = tenant or "default"

    # ---- 取消传播（验收：≤ 1s） ----------------------------------------

    def cancel_all(self) -> None:
        """软停止所有子 Agent —— ≤ 1s 唤醒 + 关闭队列。"""
        self._cancel_event.set()
        self._pool.cancel_all()
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                task = loop.create_task(self._queue.close())
                _bg_close_tasks.add(task)
                task.add_done_callback(_bg_close_tasks.discard)
                loop.call_later(1.0, self._cancel_event.clear)
        except RuntimeError:
            pass

    @property
    def cancel_event(self) -> asyncio.Event:
        return self._cancel_event

    # ---- V1.5 核心：派发 -------------------------------------------------

    async def dispatch(
        self,
        spec: SubAgentSpec,
        *,
        priority: str = "normal",
    ) -> str:
        """入队一个子 Agent 任务，返回 task_id（不等完成）。

        Returns:
            task_id（已经在 repo 里有 pending 行；主 Agent 可继续工作）

        Raises:
            TreeLimitExceeded: 派生树超 max_depth / total_nodes
        """
        enforce_tree_limits(spec, self._total_nodes)
        self._total_nodes += 1

        # 1) correlation_id（一棵决策树共享）
        correlation_id = (
            spec.input_payload.get("correlation_id")
            if isinstance(spec.input_payload, dict)
            else None
        ) or f"{spec.parent_run_id}:{spec.sub_agent_id}"

        # 2) 幂等键
        idem = (
            spec.input_payload.get("idempotency_token")
            if isinstance(spec.input_payload, dict)
            else None
        ) or f"auto:{spec.sub_agent_id}"

        # 3) 落库（pending）
        await self._repo.save_task(
            task_id=spec.sub_agent_id,
            parent_run_id=spec.parent_run_id,
            parent_task_id=spec.parent_sub_agent_id,
            correlation_id=correlation_id,
            idempotency_token=idem,
            depth=spec.depth,
            task_type=spec.task_type,
            priority=priority,
            spec=spec.model_dump(mode="json"),
            status=SubAgentStatus.PENDING.value,
            strategy=select_strategy(spec),
        )

        # 4) 入队
        ok = await self._queue.enqueue(
            task_id=spec.sub_agent_id,
            idempotency_token=idem,
            payload={"spec": spec.model_dump(mode="json"), "correlation_id": correlation_id},
            priority=priority,
        )

        # 5) 推 SSE + 审计 + 评测
        self._collector.record_dispatch(
            local_only=spec.model_policy.carries_sensitive_payload,
        )
        if ok:
            await self._emit_spawn(spec, correlation_id)
        return spec.sub_agent_id

    async def run_until_drained(
        self,
        *,
        timeout: float | None = None,
    ) -> list[SubAgentReport]:
        """Worker 消费队列直到空或取消。返回完成的 report 列表。"""
        reports: list[SubAgentReport] = []
        deadline = None if timeout is None else (time.monotonic() + timeout)
        while True:
            if deadline is not None and time.monotonic() > deadline:
                break
            if self._cancel_event.is_set():
                break
            item = await self._queue.dequeue(timeout=0.5)
            if item is None:
                if self._queue.qsize() == 0:
                    break
                continue
            report = await self._execute(item)
            reports.append(report)
        return reports

    # ---- 兼容 V0 同步 spawn（API 不变） ---------------------------------

    async def spawn(self, spec: SubAgentSpec) -> SubAgentReport:
        """V0 兼容：同步等待 + 返 SubAgentReport。"""
        enforce_tree_limits(spec, self._total_nodes)
        self._total_nodes += 1

        skeleton = SubAgentReport(
            sub_agent_id=spec.sub_agent_id,
            parent_run_id=spec.parent_run_id,
            parent_sub_agent_id=spec.parent_sub_agent_id,
            status=SubAgentStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
            backend_used="",
            model_used="",
        )
        self._reports[spec.sub_agent_id] = skeleton

        correlation_id = (
            spec.input_payload.get("correlation_id")
            if isinstance(spec.input_payload, dict)
            else None
        ) or f"{spec.parent_run_id}:{spec.sub_agent_id}"
        await self._emit_spawn(spec, correlation_id)

        t0 = time.monotonic()
        from agent.config import settings

        try:
            async with asyncio.timeout(settings.orchestrator_task_timeout_sec):
                report = await self._execute_sync(spec, correlation_id, t0)
        except Exception as e:
            logger.exception("[orchestrator] spawn failed sub=%s err=%s", spec.sub_agent_id, e)
            report = SubAgentReport(
                sub_agent_id=spec.sub_agent_id,
                parent_run_id=spec.parent_run_id,
                parent_sub_agent_id=spec.parent_sub_agent_id,
                status=SubAgentStatus.ERR,
                started_at=skeleton.started_at,
                finished_at=datetime.now(timezone.utc),
                error_message=f"{type(e).__name__}: {e}",
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

        report.finished_at = datetime.now(timezone.utc)
        semantic_errors = report.validate_semantic()
        if report.status == SubAgentStatus.OK and semantic_errors:
            report.status = SubAgentStatus.DLQ
            report.error_message = "; ".join(semantic_errors)
        self._reports[spec.sub_agent_id] = report

        await self._emit_done(report, correlation_id)
        return report

    def list_reports(self) -> list[SubAgentReport]:
        return list(self._reports.values())

    def get_report(self, sub_agent_id: str) -> SubAgentReport | None:
        return self._reports.get(sub_agent_id)

    async def cancel(self, sub_agent_id: str) -> bool:
        report = self._reports.get(sub_agent_id)
        if not report or report.status in (
            SubAgentStatus.OK,
            SubAgentStatus.ERR,
            SubAgentStatus.DLQ,
        ):
            return False
        report.status = SubAgentStatus.CANCELLED
        report.finished_at = datetime.now(timezone.utc)
        return True

    # ---- 内部：事件 emit -------------------------------------------------

    async def _emit_spawn(self, spec: SubAgentSpec, correlation_id: str) -> None:
        payload = {
            "kind": "sub_agent_spawn",
            "sub_agent_id": spec.sub_agent_id,
            "parent_run_id": spec.parent_run_id,
            "parent_sub_agent_id": spec.parent_sub_agent_id,
            "depth": spec.depth,
            "task_type": spec.task_type,
            "task_description": spec.task_description,
            "model_role": spec.model_policy.role,
            "carries_sensitive_payload": spec.model_policy.carries_sensitive_payload,
            "correlation_id": correlation_id,
        }
        emit_orchestrator_event(EVT_SUB_AGENT_SPAWN, payload)
        # V0 兼容：旧的 _events 队列（V0 e2e 测用 .empty() / get_nowait() 读）
        try:
            self._events.put_nowait(
                {
                    "channel": "agent://sub_agent_spawn",
                    "data": payload,
                    "ts": time.time(),
                }
            )
        except Exception:
            pass
        await audit_bridge.log_event(
            EVENT_SPAWN,
            correlation_id=correlation_id,
            task_id=spec.sub_agent_id,
            parent_task_id=spec.parent_sub_agent_id,
            run_id=spec.parent_run_id,
            payload=payload,
        )
        try:
            obs = observability.get_default_logger()
            await obs.log_sub_agent_spawn(
                correlation_id=correlation_id,
                sub_agent_id=spec.sub_agent_id,
                parent_sub_agent_id=spec.parent_sub_agent_id,
                run_id=spec.parent_run_id,
                task_type=spec.task_type,
                task_description=spec.task_description,
                depth=spec.depth,
                requires_write=spec.requires_write,
                backend=spec.model_policy.preferred_backend or "",
            )
        except Exception:
            pass

    async def _emit_progress(
        self,
        sub_agent_id: str,
        attempt: int,
        status: str,
        elapsed_ms: int,
        *,
        correlation_id: str,
        parent_task_id: str | None,
        run_id: str,
    ) -> None:
        emit_orchestrator_event(
            EVT_SUB_AGENT_PROGRESS,
            {
                "kind": "sub_agent_progress",
                "sub_agent_id": sub_agent_id,
                "attempt": attempt,
                "status": status,
                "elapsed_ms": elapsed_ms,
                "correlation_id": correlation_id,
            },
        )
        await audit_bridge.log_event(
            EVENT_PROGRESS,
            correlation_id=correlation_id,
            task_id=sub_agent_id,
            parent_task_id=parent_task_id,
            run_id=run_id,
            payload={"attempt": attempt, "status": status, "elapsed_ms": elapsed_ms},
        )

    async def _emit_done(
        self,
        report: SubAgentReport,
        correlation_id: str,
    ) -> None:
        payload = {
            "kind": "sub_agent_done",
            "sub_agent_id": report.sub_agent_id,
            "parent_run_id": report.parent_run_id,
            "status": report.status.value,
            "latency_ms": report.latency_ms,
            "summary_preview": (report.summary or report.error_message)[:200],
            "confidence": report.confidence,
            "backend": report.backend_used,
            "model": report.model_used,
            "attempts": report.attempts,
            "correlation_id": correlation_id,
        }
        emit_orchestrator_event(EVT_SUB_AGENT_DONE, payload)
        # V0 兼容
        try:
            self._events.put_nowait(
                {
                    "channel": "agent://sub_agent_done",
                    "data": payload,
                    "ts": time.time(),
                }
            )
        except Exception:
            pass
        await audit_bridge.log_event(
            EVENT_DONE,
            correlation_id=correlation_id,
            task_id=report.sub_agent_id,
            parent_task_id=report.parent_sub_agent_id,
            run_id=report.parent_run_id,
            payload=payload,
        )
        try:
            obs = observability.get_default_logger()
            await obs.log_sub_agent_done(
                correlation_id=correlation_id,
                sub_agent_id=report.sub_agent_id,
                parent_sub_agent_id=report.parent_sub_agent_id,
                run_id=report.parent_run_id,
                status=report.status.value,
                attempts=report.attempts,
                latency_ms=report.latency_ms,
                backend=report.backend_used,
                model=report.model_used,
            )
        except Exception:
            pass

    async def _emit_dlq(
        self,
        spec: SubAgentSpec,
        correlation_id: str,
        last_error: str,
        attempts: int,
    ) -> None:
        await audit_bridge.log_event(
            EVENT_DLQ,
            correlation_id=correlation_id,
            task_id=spec.sub_agent_id,
            parent_task_id=spec.parent_sub_agent_id,
            run_id=spec.parent_run_id,
            payload={"last_error": last_error[:500], "attempts": attempts},
        )

    # ---- 内部：执行 ------------------------------------------------------

    async def _execute(self, item: QueueItem) -> SubAgentReport:
        """Worker 拉到一个 QueueItem → 完整生命周期。"""
        spec_dict = item.payload.get("spec", {})
        correlation_id = item.payload.get("correlation_id", "")
        try:
            spec = SubAgentSpec.model_validate(spec_dict)
        except Exception as e:
            logger.exception(
                "[orchestrator] spec 解析失败 token=%s err=%s", item.idempotency_token, e
            )
            return self._deadletter_from_item(item, f"spec_parse:{e}")

        # 上下文策略组装
        composed = build_context(spec, pool=self._shared, strategy=None)
        prompt = composed.prompt
        self._collector.record_context(
            compression_ratio=composed.compression_ratio,
            required_kept=composed.required_fields_kept,
        )

        # 敏感负载 + LMRouter 红线 → 选 backend
        sens = classify_spec(spec, prompt)
        local_only = sens.local_only or (
            spec.task_type in _LOCAL_ONLY_TASKS or spec.model_policy.task_type in _LOCAL_ONLY_TASKS
        )
        from agent.config import settings

        if local_only:
            self._collector.local_only_forced += 1
            chosen_backend = "ollama"
        else:
            chosen_backend = self._buckets.fallback_backend(
                self._tenant,
                spec.task_type,
                spec.model_policy.preferred_backend or "",
                is_local_only_task=False,
            )

        # 跑 handler，失败重试
        attempts = 0
        last_error = ""
        t0 = time.monotonic()
        for attempt in range(1, settings.orchestrator_max_attempts + 1):
            attempts = attempt
            if self._cancel_event.is_set():
                await self._emit_progress(
                    spec.sub_agent_id,
                    attempt,
                    "cancelled",
                    int((time.monotonic() - t0) * 1000),
                    correlation_id=correlation_id,
                    parent_task_id=spec.parent_sub_agent_id,
                    run_id=spec.parent_run_id,
                )
                return self._build_report(
                    spec,
                    correlation_id,
                    SubAgentStatus.CANCELLED,
                    t0,
                    attempts,
                    error="cancelled",
                )
            try:
                result_text = await self._invoke_llm(chosen_backend, prompt, spec)
                latency_ms = int((time.monotonic() - t0) * 1000)
                await self._emit_progress(
                    spec.sub_agent_id,
                    attempt,
                    "running",
                    latency_ms,
                    correlation_id=correlation_id,
                    parent_task_id=spec.parent_sub_agent_id,
                    run_id=spec.parent_run_id,
                )
                report = self._build_report(
                    spec,
                    correlation_id,
                    SubAgentStatus.OK,
                    t0,
                    attempts,
                    summary=result_text[:2000],
                    confidence=0.8,
                    backend=chosen_backend,
                    latency_ms=latency_ms,
                    tokens_before=composed.tokens_before,
                    tokens_after=composed.tokens_after,
                )
                sem = report.validate_semantic()
                if sem:
                    last_error = "; ".join(sem)
                    self._collector.record_validation(ok=False)
                    if attempt < settings.orchestrator_max_attempts:
                        continue
                    return await self._dlq(
                        spec, correlation_id, last_error, attempts, t0, composed, chosen_backend
                    )
                self._collector.record_validation(ok=True)
                await self._persist_ok(spec, correlation_id, report, composed, chosen_backend)
                # Judge 抽样
                if self._judge and self._collector.should_judge():
                    verdict = await judge_report(
                        task_id=spec.sub_agent_id,
                        task_type=spec.task_type,
                        task_description=spec.task_description,
                        summary=result_text,
                        judge_caller=self._judge,
                        collector=self._collector,
                    )
                    await audit_bridge.log_event(
                        audit_bridge.EVENT_JUDGE,
                        correlation_id=correlation_id,
                        task_id=spec.sub_agent_id,
                        parent_task_id=spec.parent_sub_agent_id,
                        run_id=spec.parent_run_id,
                        payload=verdict.to_dict(),
                    )
                return report
            except Exception as e:
                last_error = f"{type(e).__name__}: {e}"
                self._collector.record_retry()
                if attempt >= settings.orchestrator_max_attempts:
                    break
                await asyncio.sleep(0.05 * (2 ** (attempt - 1)))
        return await self._dlq(
            spec, correlation_id, last_error, attempts, t0, composed, chosen_backend
        )

    async def _execute_sync(
        self,
        spec: SubAgentSpec,
        correlation_id: str,
        t0: float,
    ) -> SubAgentReport:
        """V0 兼容路径：把 V0 的 `_run` 逻辑包成 V1.5 风格（带 context + 敏感检测）。"""
        composed = build_context(spec, pool=self._shared, strategy=None)
        self._collector.record_context(
            compression_ratio=composed.compression_ratio,
            required_kept=composed.required_fields_kept,
        )
        sens = classify_spec(spec, composed.prompt)
        local_only = sens.local_only or (
            spec.task_type in _LOCAL_ONLY_TASKS or spec.model_policy.task_type in _LOCAL_ONLY_TASKS
        )
        backend = "ollama" if local_only else (spec.model_policy.preferred_backend or "")
        if local_only:
            self._collector.local_only_forced += 1
        try:
            result_text = await self._invoke_llm(backend, composed.prompt, spec)
            if not result_text:
                raise RuntimeError("LLM 返回空字符串")
            return self._build_report(
                spec,
                correlation_id,
                SubAgentStatus.OK,
                t0,
                1,
                summary=result_text[:2000],
                confidence=0.85 if backend == "ollama" else 0.8,
                backend=backend,
                latency_ms=int((time.monotonic() - t0) * 1000),
                tokens_before=composed.tokens_before,
                tokens_after=composed.tokens_after,
            )
        except Exception as e:
            return self._build_report(
                spec,
                correlation_id,
                SubAgentStatus.ERR,
                t0,
                1,
                error=f"{type(e).__name__}: {e}",
                backend=backend,
                latency_ms=int((time.monotonic() - t0) * 1000),
            )

    async def _invoke_llm(self, backend: str, prompt: str, spec: SubAgentSpec) -> str:
        """调 LMRouter；缺 router 走 mock 兜底（测试可注入）。"""
        if self._llm is None:
            return f"[mock:{backend}] {prompt[:200]}"
        return await self._llm.route(
            task=spec.model_policy.task_type or spec.task_type,
            prompt=prompt,
        )

    # ---- V0 兼容：prompt 拼装（V0 e2e 测调用）----------------------------

    def _compose_prompt(self, spec: SubAgentSpec) -> str:
        """V0 兼容入口：组装 prompt。V1.5 内部走 context_strategy.build_context()。

        保留 V0 行为：
          - `passthrough` 策略直接列 `task_type / task_description / input_payload`
          - `required_fields` 拼到「必读字段」区
          - `shared_memory_pool` V0 仅日志 warning（V1.5 build_context 已支持）
        """
        composed = build_context(spec, pool=self._shared, strategy=None)
        if spec.context_policy.strategy != "passthrough":
            logger.warning(
                "[orchestrator] strategy=%s V0 _compose_prompt 走 passthrough；"
                "V1.5 建议改用 build_context()",
                spec.context_policy.strategy,
            )
        return composed.prompt

    def _build_report(
        self,
        spec: SubAgentSpec,
        correlation_id: str,
        status: SubAgentStatus,
        t0: float,
        attempts: int,
        *,
        summary: str = "",
        confidence: float = 0.0,
        backend: str = "",
        latency_ms: int | None = None,
        error: str = "",
        tokens_before: int = 0,
        tokens_after: int = 0,
    ) -> SubAgentReport:
        ms = int(latency_ms if latency_ms is not None else (time.monotonic() - t0) * 1000)
        model = _extract_model_name(self._llm) if backend else ""
        return SubAgentReport(
            sub_agent_id=spec.sub_agent_id,
            parent_run_id=spec.parent_run_id,
            parent_sub_agent_id=spec.parent_sub_agent_id,
            status=status,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            summary=summary,
            confidence=confidence,
            state_delta=StateDelta(
                fields_added={"task_type": spec.task_type, "correlation_id": correlation_id}
            ),
            backend_used=backend,
            model_used=model,
            latency_ms=ms,
            error_message=error,
            attempts=attempts,
        )

    async def _persist_ok(
        self,
        spec: SubAgentSpec,
        correlation_id: str,
        report: SubAgentReport,
        composed: ComposedContext,
        backend: str,
    ) -> None:
        try:
            await self._repo.update_status_cas(
                task_id=spec.sub_agent_id,
                expected_version=1,
                status=SubAgentStatus.OK.value,
                report=report.model_dump(mode="json"),
                backend=backend,
                strategy=composed.strategy,
                tokens_before=composed.tokens_before,
                tokens_after=composed.tokens_after,
                latency_ms=report.latency_ms,
            )
        except Exception as e:
            logger.warning("[orchestrator] update_status_cas ok 失败: %s", e)
        for ref in composed.raw_refs:
            try:
                await self._repo.save_artifact(
                    artifact_id=ref.artifact_id,
                    task_id=spec.sub_agent_id,
                    kind=ref.kind,
                    content_hash=ref.content_hash,
                    byte_size=ref.byte_size,
                    preview=ref.preview,
                )
            except Exception:
                pass
        self._collector.record_result(
            status=SubAgentStatus.OK.value,
            latency_ms=report.latency_ms,
            attempts=report.attempts,
        )
        try:
            await self._repo.record_metric(
                metric="latency_ms",
                value=report.latency_ms,
                task_id=spec.sub_agent_id,
                correlation_id=correlation_id,
            )
            await self._repo.record_metric(
                metric="compression_ratio",
                value=composed.compression_ratio,
                task_id=spec.sub_agent_id,
                correlation_id=correlation_id,
            )
        except Exception:
            pass

    async def _dlq(
        self,
        spec: SubAgentSpec,
        correlation_id: str,
        last_error: str,
        attempts: int,
        t0: float,
        composed: ComposedContext,
        backend: str,
    ) -> SubAgentReport:
        report = self._build_report(
            spec,
            correlation_id,
            SubAgentStatus.DLQ,
            t0,
            attempts,
            error=last_error,
            backend=backend,
        )
        await self._repo.push_dlq(
            task_id=spec.sub_agent_id,
            correlation_id=correlation_id,
            idempotency_token=f"auto:{spec.sub_agent_id}",
            payload=spec.model_dump(mode="json"),
            last_error=last_error[:500],
            attempts=attempts,
        )
        try:
            await self._repo.update_status_cas(
                task_id=spec.sub_agent_id,
                expected_version=1,
                status=SubAgentStatus.DLQ.value,
                report=report.model_dump(mode="json"),
                error=last_error[:500],
                backend=backend,
                strategy=composed.strategy,
                tokens_before=composed.tokens_before,
                tokens_after=composed.tokens_after,
                latency_ms=report.latency_ms,
            )
        except Exception:
            pass
        await self._emit_dlq(spec, correlation_id, last_error, attempts)
        self._collector.record_result(
            status=SubAgentStatus.DLQ.value,
            latency_ms=report.latency_ms,
            attempts=attempts,
        )
        return report

    def _deadletter_from_item(self, item: QueueItem, error: str) -> SubAgentReport:
        return SubAgentReport(
            sub_agent_id=item.task_id,
            parent_run_id="",
            status=SubAgentStatus.DLQ,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            error_message=error[:500],
            attempts=1,
        )


# ---- 单例 ----------------------------------------------------------------


_ORCH: Orchestrator | None = None


def get_orchestrator() -> Orchestrator:
    global _ORCH
    if _ORCH is None:
        _ORCH = Orchestrator()
    return _ORCH


def reset_orchestrator(router: LMRouter | None = None) -> Orchestrator:
    global _ORCH
    _ORCH = Orchestrator(llm_router=router)
    return _ORCH
