"""orchestrator.token_bucket —— Phase 12 V1 三层限流（租户 × 任务 × 后端）。

设计（来自 phase-12-multi-agent-scaling.md §2.4）：
- 按 租户 × 任务类型 × LLM 后端 三个维度分配令牌桶配额
- 多级降级：内网 LLM 配额打满 → 本地 0.3B → mock
- V1 进程内实现（接口兼容 V1.5 真 Redis）

CLAUDE.md §2 红线：
- 限流不绕过 `_LOCAL_ONLY_TASKS` —— 敏感任务即使配额满也要走本地（强制覆盖）
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)


@dataclass
class BucketConfig:
    """单个令牌桶配置。"""
    capacity: int          # 桶容量
    refill_rate: float     # 每秒补充的令牌数


@dataclass
class TokenBucket:
    """单租户 × 单任务 × 单后端的令牌桶。"""
    capacity: int
    refill_rate: float
    tokens: float = 0.0
    last_refill_at: float = 0.0

    def try_consume(self, n: int = 1) -> bool:
        """尝试消费 n 个令牌。Returns: True 成功 / False 失败（需降级）。"""
        now = time.monotonic()
        if self.last_refill_at > 0:
            elapsed = now - self.last_refill_at
            if elapsed > 0:
                self.tokens = min(
                    self.capacity,
                    self.tokens + elapsed * self.refill_rate,
                )
        self.last_refill_at = now
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False


class TokenBucketManager:
    """三层令牌桶管理器。

    Key 形如：(tenant, task_type, backend)
    例：("tenant_a", "plan", "private")
    """

    def __init__(
        self,
        *,
        default_capacity: int = 100,
        default_refill_rate: float = 10.0,
        backend_overrides: dict[str, tuple[int, float]] | None = None,
    ):
        self.default_capacity = default_capacity
        self.default_refill_rate = default_refill_rate
        self.backend_overrides = backend_overrides or {
            # 内网 LLM 配额偏紧；本地 Ollama 几乎无限制
            "private": (50, 5.0),
            "ollama": (10_000, 1_000.0),
            "local_small": (10_000, 1_000.0),
            "mock": (10_000, 1_000.0),
        }
        self._buckets: dict[tuple[str, str, str], TokenBucket] = {}

    def _get_bucket(
        self, tenant: str, task_type: str, backend: str,
    ) -> TokenBucket:
        key = (tenant, task_type, backend)
        if key not in self._buckets:
            cap, rate = self.backend_overrides.get(
                backend, (self.default_capacity, self.default_refill_rate),
            )
            self._buckets[key] = TokenBucket(
                capacity=cap, refill_rate=rate,
                tokens=cap,  # 初始满
            )
        return self._buckets[key]

    def try_consume(
        self,
        tenant: str,
        task_type: str,
        backend: str,
        n: int = 1,
    ) -> bool:
        """尝试消费。Returns: True 成功。"""
        return self._get_bucket(tenant, task_type, backend).try_consume(n)

    def fallback_backend(
        self,
        tenant: str,
        task_type: str,
        _current_backend: str = "",
        *,
        is_local_only_task: bool = False,
    ) -> str:
        """配额不足时降级到下个后端。

        多级降级链：private → ollama → local_small → mock
        红线：`_LOCAL_ONLY_TASKS`（is_local_only_task=True）即使私有桶满也强制走 ollama / local_small（跳过 mock）
        """
        if is_local_only_task:
            # 红线：本地 Ollama 几乎无限制 → 直接返
            return "ollama" if self.try_consume(tenant, task_type, "ollama", n=1) else "local_small"
        chain = ["private", "ollama", "local_small", "mock"]
        for backend in chain:
            if self.try_consume(tenant, task_type, backend, n=1):
                return backend
        return "mock"  # 终极兜底

    def reset(self) -> None:
        """测试 hook：清空所有桶。"""
        self._buckets.clear()


# ---- 全局单例 -------------------------------------------------------------

_default_manager: TokenBucketManager | None = None


def get_default_bucket_manager() -> TokenBucketManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = TokenBucketManager()
    return _default_manager


def reset_default_bucket_manager() -> None:
    """测试 hook。"""
    global _default_manager
    _default_manager = None