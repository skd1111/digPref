"""Retry decorator with exponential backoff — used by MCP calls."""
from __future__ import annotations

from functools import wraps
from typing import Awaitable, Callable, TypeVar

from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

T = TypeVar("T")


def async_retry(
    *, attempts: int = 3, base: float = 0.5, max_wait: float = 4.0
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    def deco(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(fn)
        async def wrapper(*args, **kwargs) -> T:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(attempts),
                wait=wait_exponential(multiplier=base, max=max_wait),
                reraise=True,
            ):
                with attempt:
                    return await fn(*args, **kwargs)
            # reraise=True 使 tenacity 在全部重试耗尽后直接抛出 RetryError，
            # 此处的 return/raise 不可达；保留空 return 让 mypy 满意。
        return wrapper
    return deco