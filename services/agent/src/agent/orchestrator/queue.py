"""orchestrator.queue —— Phase 12 V1.5 优先级任务队列（high / normal / low）。

设计文档 §2.2 里这一层原本是 Redis List（`queue:sub_agent:{priority}` + BLPOP）。
架构决策（2026-07-31）：本地 EAIDE 是单进程桌面应用，跨进程队列无必要 ——
V1.5 用 **进程内 deque + asyncio.Condition** 实现同语义，权威状态落
`orchestrator.db::sub_agent_tasks`（进程重启后可从 `status='pending'` 重放）。

语义保持与 Redis 版一致：
    - 三条优先级队列，`dequeue()` 严格按 high → normal → low 取
    - `idempotency_token` 入队去重（铁律 6 幂等）
    - `close()` 唤醒所有等待者（取消传播 ≤ 1s，铁律 8）
    - `stats()` 暴露堆积长度（监控阈值：单队列 > 100 告警）
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

PRIORITIES: tuple[str, ...] = ("high", "normal", "low")
# 监控告警阈值（实现文档 §13）
BACKLOG_ALERT_THRESHOLD = 100


class QueueClosed(RuntimeError):
    """队列已关闭（取消传播 / 优雅停机）。"""


@dataclass
class QueueItem:
    """队列中的一个待派发任务。"""
    task_id: str
    idempotency_token: str
    payload: Any
    priority: str = "normal"
    enqueued_at: float = field(default_factory=time.monotonic)

    @property
    def wait_ms(self) -> int:
        return int((time.monotonic() - self.enqueued_at) * 1000)


class PriorityTaskQueue:
    """三级优先级异步队列。"""

    def __init__(self) -> None:
        self._queues: dict[str, deque[QueueItem]] = {p: deque() for p in PRIORITIES}
        self._cond = asyncio.Condition()
        self._seen: set[str] = set()
        self._closed = False
        self._enqueued_total = 0
        self._dequeued_total = 0
        self._dedup_hits = 0

    # ---- 状态 -----------------------------------------------------------

    @property
    def closed(self) -> bool:
        return self._closed

    def qsize(self, priority: str | None = None) -> int:
        if priority:
            return len(self._queues[priority])
        return sum(len(q) for q in self._queues.values())

    def stats(self) -> dict[str, Any]:
        by_priority = {p: len(self._queues[p]) for p in PRIORITIES}
        return {
            "closed": self._closed,
            "pending": sum(by_priority.values()),
            "by_priority": by_priority,
            "enqueued_total": self._enqueued_total,
            "dequeued_total": self._dequeued_total,
            "dedup_hits": self._dedup_hits,
            "backlog_alert": any(
                n > BACKLOG_ALERT_THRESHOLD for n in by_priority.values()
            ),
        }

    # ---- 入队 / 出队 -----------------------------------------------------

    async def enqueue(
        self,
        *,
        task_id: str,
        idempotency_token: str,
        payload: Any,
        priority: str = "normal",
    ) -> bool:
        """入队。返回 False = 幂等命中（同 token 已入队过，不重复派发）。"""
        if self._closed:
            raise QueueClosed("队列已关闭，拒绝入队")
        if priority not in self._queues:
            raise ValueError(f"非法 priority: {priority!r}（允许 {PRIORITIES}）")
        async with self._cond:
            if idempotency_token in self._seen:
                self._dedup_hits += 1
                logger.debug("[queue] dedup hit token=%s", idempotency_token)
                return False
            self._seen.add(idempotency_token)
            self._queues[priority].append(
                QueueItem(
                    task_id=task_id,
                    idempotency_token=idempotency_token,
                    payload=payload,
                    priority=priority,
                )
            )
            self._enqueued_total += 1
            self._cond.notify()
        return True

    def _pop_ready(self) -> Optional[QueueItem]:
        for p in PRIORITIES:
            if self._queues[p]:
                return self._queues[p].popleft()
        return None

    async def dequeue(self, timeout: float | None = None) -> Optional[QueueItem]:
        """按 high → normal → low 顺序取一个任务。

        Returns:
            QueueItem，或 None（超时 / 队列已关闭且空）。
        """
        deadline = None if timeout is None else (time.monotonic() + timeout)
        async with self._cond:
            while True:
                item = self._pop_ready()
                if item is not None:
                    self._dequeued_total += 1
                    return item
                if self._closed:
                    return None
                if deadline is None:
                    await self._cond.wait()
                else:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return None
                    try:
                        await asyncio.wait_for(self._cond.wait(), timeout=remaining)
                    except asyncio.TimeoutError:
                        return None

    def drain(self) -> list[QueueItem]:
        """同步抽干（测试 / 停机时把剩余任务交回调用方持久化）。"""
        out: list[QueueItem] = []
        for p in PRIORITIES:
            while self._queues[p]:
                out.append(self._queues[p].popleft())
        return out

    # ---- 生命周期 --------------------------------------------------------

    async def close(self) -> None:
        """关闭队列并唤醒所有等待者（取消传播）。"""
        self._closed = True
        async with self._cond:
            self._cond.notify_all()

    async def reopen(self) -> None:
        """重新开放（DLQ requeue 场景）。"""
        self._closed = False

    def forget(self, idempotency_token: str) -> None:
        """遗忘幂等记录（DLQ requeue 需要允许重派同一 token）。"""
        self._seen.discard(idempotency_token)


# ---- 全局单例 -------------------------------------------------------------

_default_queue: PriorityTaskQueue | None = None


def get_default_queue() -> PriorityTaskQueue:
    global _default_queue
    if _default_queue is None:
        _default_queue = PriorityTaskQueue()
    return _default_queue


def reset_default_queue() -> PriorityTaskQueue:
    """测试 hook。"""
    global _default_queue
    _default_queue = PriorityTaskQueue()
    return _default_queue


__all__ = [
    "PriorityTaskQueue",
    "QueueItem",
    "QueueClosed",
    "PRIORITIES",
    "BACKLOG_ALERT_THRESHOLD",
    "get_default_queue",
    "reset_default_queue",
]
