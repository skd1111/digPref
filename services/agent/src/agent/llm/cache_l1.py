"""L1 精确缓存（Phase 2C v2）—— 内存 LRU + TTL。

命中条件：key = sha256(model + normalized_prompt) 完全相同。
用途：同一 prompt 短时间重复调用（如用户连点、重试）直接返回，省一次 LLM 调用。
缓存命中不计入 API 成本（storage.record_decision 里 cache_hit → actual_cost=0）。

零外部依赖：OrderedDict 实现 LRU，手写 TTL。进程内单实例即可（LLM 调用不是热路径）。
时间注入：now_fn 便于测试控制 TTL 过期，不依赖真实时钟 sleep。
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from typing import Callable, Optional


def make_key(model: str, prompt: str) -> str:
    """规范化 prompt（strip + 折叠空白）后与 model 一起哈希。"""
    normalized = " ".join(prompt.split())
    raw = f"{model}\x00{normalized}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class L1Cache:
    """内存 LRU 精确缓存，容量上限 + TTL 双重淘汰。

    Args:
        max_size: 最多缓存条数，超出淘汰最久未用（LRU）
        ttl_sec: 每条存活秒数，读到过期条目视为 miss 并删除
        now_fn: 时间源（默认 time.monotonic），测试可注入
    """

    def __init__(
        self,
        *,
        max_size: int = 512,
        ttl_sec: float = 300.0,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max(1, max_size)
        self._ttl = ttl_sec
        self._now = now_fn
        # key -> (value, expires_at)
        self._store: "OrderedDict[str, tuple[str, float]]" = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[str]:
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        value, expires_at = entry
        if self._now() >= expires_at:
            # 过期 → 淘汰并算 miss
            del self._store[key]
            self.misses += 1
            return None
        # 命中 → 移到 MRU 端
        self._store.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key: str, value: str) -> None:
        expires_at = self._now() + self._ttl
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, expires_at)
        # 容量淘汰：从 LRU 端弹出
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0
