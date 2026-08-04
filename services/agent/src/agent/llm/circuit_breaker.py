"""熔断器（circuit_breaker.py）。

V0 简化实现：
- 状态机：Closed → Open → Half-Open
- Closed：所有请求通过
- 连续 3 次失败 → Open：拒绝请求（短路）
- Open 30 秒后 → Half-Open：放 1 个请求探测
- 探测成功 → Closed；探测失败 → Open（再 30s）

CLAUDE.md 红线：熔断器**仅**影响请求是否放行，**不**改请求内容。
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class CircuitStats:
    failure_count: int = 0
    success_count: int = 0
    last_failure_at: float = 0.0
    last_state_change: float = field(default_factory=time.monotonic)


class CircuitBreaker:
    """单后端的熔断器（thread-safe 因为 V0 是同步调用，保留 lock 为 V1 异步）。"""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 3,
        reset_timeout_s: float = 30.0,
    ):
        self.name = name
        self._failure_threshold = failure_threshold
        self._reset_timeout_s = reset_timeout_s
        self._state = CircuitState.CLOSED
        self._stats = CircuitStats()
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_to_half_open()
            return self._state

    def _maybe_to_half_open(self) -> None:
        """Open 状态超过 reset_timeout → Half-Open（探测）。"""
        if self._state != CircuitState.OPEN:
            return
        if time.monotonic() - self._stats.last_state_change >= self._reset_timeout_s:
            self._state = CircuitState.HALF_OPEN
            self._stats.last_state_change = time.monotonic()
            logger.info("circuit_breaker %s OPEN -> HALF_OPEN", self.name)

    def allow(self) -> bool:
        """是否放行请求。Closed / Half-Open 放行；Open 拒绝。"""
        with self._lock:
            self._maybe_to_half_open()
            return self._state != CircuitState.OPEN

    def on_success(self) -> None:
        """请求成功：Half-Open → Closed；Closed 累计成功。"""
        with self._lock:
            self._stats.success_count += 1
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._stats.failure_count = 0
                self._stats.last_state_change = time.monotonic()
                logger.info("circuit_breaker %s HALF_OPEN -> CLOSED (probe ok)", self.name)

    def on_failure(self) -> None:
        """请求失败：Closed 累计失败，到阈值 → Open；Half-Open 探测失败 → Open。"""
        with self._lock:
            self._stats.failure_count += 1
            self._stats.last_failure_at = time.monotonic()
            if self._state == CircuitState.HALF_OPEN:
                # 探测失败，直接 Open
                self._state = CircuitState.OPEN
                self._stats.last_state_change = time.monotonic()
                logger.info("circuit_breaker %s HALF_OPEN -> OPEN (probe fail)", self.name)
            elif self._state == CircuitState.CLOSED:
                if self._stats.failure_count >= self._failure_threshold:
                    self._state = CircuitState.OPEN
                    self._stats.last_state_change = time.monotonic()
                    logger.info(
                        "circuit_breaker %s CLOSED -> OPEN (failures=%d)",
                        self.name, self._stats.failure_count,
                    )

    def reset(self) -> None:
        """手动重置（admin 工具）。"""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._stats = CircuitStats()
            self._stats.last_state_change = time.monotonic()
            logger.info("circuit_breaker %s manual reset", self.name)


class CircuitBreakerRegistry:
    """所有后端熔断器注册表（按 name 索引）。"""

    def __init__(self, failure_threshold: int = 3, reset_timeout_s: float = 30.0):
        self._registry: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()
        self._failure_threshold = failure_threshold
        self._reset_timeout_s = reset_timeout_s

    def get_or_create(self, name: str) -> CircuitBreaker:
        with self._lock:
            if name not in self._registry:
                self._registry[name] = CircuitBreaker(
                    name,
                    failure_threshold=self._failure_threshold,
                    reset_timeout_s=self._reset_timeout_s,
                )
            return self._registry[name]

    def all_states(self) -> dict[str, CircuitState]:
        with self._lock:
            return {name: cb.state for name, cb in self._registry.items()}

    def reset_all(self) -> None:
        with self._lock:
            for cb in self._registry.values():
                cb.reset()
