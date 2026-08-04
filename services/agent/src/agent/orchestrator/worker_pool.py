"""orchestrator.worker_pool —— Phase 12 V1 子 Agent Worker Pool + 重试 + DLQ。

设计（来自 phase-12-multi-agent-scaling.md §2.2）：
- 异步任务队列 + 有限并发 Worker（asyncio Semaphore 控制，V1 默认 4）
- 重试：worker 内最多 3 次（指数退避 0.5s × 2^attempt），与 Auto-Repair 的 retry_count 是两层独立机制
- DLQ：3 次重试仍失败 → 进 DLQ 队列 + 主 Agent 收到 `dlq` 状态上报
- 取消传播：1 秒内软停止所有 worker（V1 简化：asyncio CancelledError 传播）

CLAUDE.md §6 红线：
- 队列/DLQ 状态落 audit.sqlite（V1 占位：进程内 list；V1.5 持久化）
- idempotency_token 去重（同一任务不重复派发）
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class WorkerTask:
    """Worker Pool 中的一个任务。"""
    task_id: str
    idempotency_token: str
    payload: Any
    attempts: int = 0
    max_attempts: int = 3
    next_run_at: float = 0.0          # 退避时间戳（time.monotonic）
    last_error: str = ""
    status: str = "pending"          # pending / running / done / failed / dlq / cancelled


@dataclass
class WorkerResult:
    """Worker 执行结果。"""
    task_id: str
    success: bool
    result: Any = None
    error: str = ""
    attempts: int = 0
    latency_ms: int = 0


# ---- DLQ -------------------------------------------------------------------

@dataclass
class DLQEntry:
    """死信队列条目。"""
    task_id: str
    idempotency_token: str
    payload: Any
    last_error: str
    attempts: int
    enqueued_at: float = field(default_factory=time.monotonic)


# ---- Worker Pool -----------------------------------------------------------


class WorkerPool:
    """异步 Worker Pool + 重试 + DLQ（V1 进程内实现）。

    用法：
        pool = WorkerPool(concurrency=4, retry_base_delay_s=0.5)
        result = await pool.submit(
            idempotency_token="task-xxx",
            payload={"prompt": "..."},
            handler=lambda p: my_async_runner(p),
        )
        # 失败 3 次 → 自动进 DLQ
        # 拿到 dlq_entries 看死信

    线程安全：asyncio 一致（单 loop 内使用）。
    """

    def __init__(
        self,
        *,
        concurrency: int = 4,
        retry_base_delay_s: float = 0.5,
        max_attempts: int = 3,
    ):
        self.concurrency = concurrency
        self.retry_base_delay_s = retry_base_delay_s
        self.max_attempts = max_attempts
        self._sem = asyncio.Semaphore(concurrency)
        # 已派发任务（idempotency_token → task_id），去重用
        self._seen: dict[str, str] = {}
        # 任务表（task_id → WorkerTask）
        self._tasks: dict[str, WorkerTask] = {}
        # DLQ（task_id → DLQEntry）
        self._dlq: dict[str, DLQEntry] = {}
        # 取消事件（pool 关闭时翻 → 所有 worker 软停止）
        self._cancelled = asyncio.Event()

    @property
    def dlq_entries(self) -> list[DLQEntry]:
        return list(self._dlq.values())

    @property
    def task_count(self) -> int:
        return len(self._tasks)

    def cancel_all(self) -> None:
        """1 秒内软停止所有 worker。"""
        self._cancelled.set()

    async def submit(
        self,
        *,
        idempotency_token: str,
        payload: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> WorkerResult:
        """提交一个任务。

        Returns:
            WorkerResult（success=True 即 result 有值；success=False 即 attempts == max_attempts 且进 DLQ）
        """
        # 1. 幂等检查
        if idempotency_token in self._seen:
            existing_id = self._seen[idempotency_token]
            existing = self._tasks[existing_id]
            logger.debug("[worker_pool] dedup hit task=%s", existing_id)
            return WorkerResult(
                task_id=existing_id, success=existing.status == "done",
                result=existing.payload if existing.status == "done" else None,
                error=existing.last_error,
                attempts=existing.attempts,
            )

        # 2. 新建任务
        task_id = str(uuid.uuid4())
        task = WorkerTask(
            task_id=task_id,
            idempotency_token=idempotency_token,
            payload=payload,
            max_attempts=self.max_attempts,
        )
        self._tasks[task_id] = task
        self._seen[idempotency_token] = task_id

        # 3. 等待信号量（限并发）
        return await self._run_with_retry(task, handler)

    async def _run_with_retry(
        self,
        task: WorkerTask,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> WorkerResult:
        """带重试的执行循环。"""
        last_error = ""
        while task.attempts < task.max_attempts:
            if self._cancelled.is_set():
                task.status = "cancelled"
                return WorkerResult(
                    task_id=task.task_id, success=False,
                    error="cancelled", attempts=task.attempts,
                )

            async with self._sem:
                task.status = "running"
                task.attempts += 1
                t0 = time.monotonic()
                try:
                    result = await handler(task.payload)
                    task.status = "done"
                    return WorkerResult(
                        task_id=task.task_id, success=True,
                        result=result, attempts=task.attempts,
                        latency_ms=int((time.monotonic() - t0) * 1000),
                    )
                except asyncio.CancelledError:
                    task.status = "cancelled"
                    raise
                except Exception as e:
                    last_error = f"{type(e).__name__}: {e}"
                    task.last_error = last_error
                    logger.warning(
                        "[worker_pool] task=%s attempt %d/%d failed: %s",
                        task.task_id, task.attempts, task.max_attempts, last_error,
                    )
                    if task.attempts >= task.max_attempts:
                        break
                    # 指数退避
                    delay = self.retry_base_delay_s * (2 ** (task.attempts - 1))
                    await asyncio.sleep(delay)

        # 3 次均失败 → DLQ
        task.status = "dlq"
        self._dlq[task.task_id] = DLQEntry(
            task_id=task.task_id,
            idempotency_token=task.idempotency_token,
            payload=task.payload,
            last_error=last_error,
            attempts=task.attempts,
        )
        return WorkerResult(
            task_id=task.task_id, success=False,
            error=last_error, attempts=task.attempts,
        )