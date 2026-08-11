"""orchestrator.locks —— Phase 12 V1 状态锁（乐观 CAS + 字典序分布式锁）。

设计（来自 phase-12-multi-agent-scaling.md §2.2）：
- **乐观锁（默认）** —— 任务状态更新携带 `state_version`；CAS 失败 → 重读最新版本再写
- **字典序分布式锁** —— 仅用于跨子 Agent 争抢同一外部资源；按资源 ID 字典序获取防死锁
- V1 实现：进程内 dict 模拟 Redis（接口兼容 V1.5 切真 Redis）

CLAUDE.md §6 红线：
- 锁 TTL + 自动续租（V1 占位：TTL 30s + 无续租；V1.5 接真 Redis）
- 按字典序获取（防死锁）
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


# ---- 乐观 CAS -------------------------------------------------------------


@dataclass
class VersionedState:
    """带 state_version 的状态对象（CAS 用）。"""

    data: dict
    state_version: int = 0


def cas_update(state: VersionedState, mutator) -> tuple[bool, int]:
    """CAS 更新：mutator 接受 current data → 新 data。

    Returns:
        (success, new_version)
        - success=True → mutator 返回了新 dict，state_version 自增
        - success=False → 调用方重读最新版本再 retry（V1 单进程下永远 success）
    """
    old_version = state.state_version
    new_data = mutator(dict(state.data))  # copy on write
    if new_data is None:
        return False, old_version
    state.data = new_data
    state.state_version = old_version + 1
    return True, state.state_version


# ---- 字典序分布式锁（进程内 mock）-----------------------------------------


class _LockRecord:
    """单把锁的元数据（持有者 + TTL）。"""

    def __init__(self, owner: str, expires_at: float):
        self.owner = owner
        self.expires_at = expires_at


class DistributedLockManager:
    """进程内分布式锁（V1 占位；接口兼容 V1.5 真 Redis）。

    用法：
        async with mgr.acquire_many(["resource_a", "resource_b"]) as held:
            # 同时持有两把锁（按字典序获取）
            ...
    """

    def __init__(self, default_ttl_s: float = 30.0):
        self.default_ttl_s = default_ttl_s
        self._locks: dict[str, _LockRecord] = {}
        self._waiters: dict[str, asyncio.Event] = {}
        self._owner_id = str(uuid.uuid4())  # 当前进程 owner

    @contextlib.asynccontextmanager
    async def acquire_one(
        self, resource_id: str, *, ttl_s: float | None = None
    ) -> AsyncIterator[bool]:
        """单把锁；超时自动让出。"""
        ttl = ttl_s if ttl_s is not None else self.default_ttl_s
        # 自旋等待
        while True:
            self._cleanup_expired()
            if resource_id not in self._locks:
                self._locks[resource_id] = _LockRecord(
                    owner=self._owner_id,
                    expires_at=time.monotonic() + ttl,
                )
                break
            # 等被释放
            ev = self._waiters.setdefault(resource_id, asyncio.Event())
            try:
                await asyncio.wait_for(ev.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
        try:
            yield True
        finally:
            # 释放
            if self._locks.get(resource_id) and self._locks[resource_id].owner == self._owner_id:
                del self._locks[resource_id]
                ev = self._waiters.get(resource_id)
                if ev:
                    ev.set()
                    ev.clear()

    @contextlib.asynccontextmanager
    async def acquire_many(
        self, resource_ids: list[str], *, ttl_s: float | None = None
    ) -> AsyncIterator[list[str]]:
        """按字典序获取多把锁（防死锁）。"""
        ordered = sorted(set(resource_ids))
        held_cms: list[tuple[str, Any]] = []  # (resource_id, cm)
        try:
            for rid in ordered:
                cm = self.acquire_one(rid, ttl_s=ttl_s)
                await cm.__aenter__()
                held_cms.append((rid, cm))
            yield [rid for rid, _ in held_cms]
        finally:
            # 逆序释放
            for _, cm in reversed(held_cms):
                await cm.__aexit__(None, None, None)

    def _cleanup_expired(self) -> None:
        now = time.monotonic()
        expired = [r for r, rec in self._locks.items() if rec.expires_at <= now]
        for r in expired:
            del self._locks[r]
            ev = self._waiters.get(r)
            if ev:
                ev.set()
                ev.clear()


# ---- 全局单例 -------------------------------------------------------------

_default_manager: DistributedLockManager | None = None


def get_default_lock_manager() -> DistributedLockManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = DistributedLockManager()
    return _default_manager


def reset_default_lock_manager() -> None:
    """测试 hook。"""
    global _default_manager
    _default_manager = None
