"""L3 幂等只读工具结果缓存（Phase 17 V1）。

红线（docs/design/phase-17-cache-hit-rate.md §3.4）：
    - 只缓存白名单内的幂等只读 builtin 工具（保守起步，宁窄勿宽）；
    - 写工具 / HITL 待审批链路绝不缓存（write_detector 双重把关）；
    - MCP 工具一律不缓存（副作用未知，V2 再评估）；
    - 失败结果（ok=False / awaiting_approval）不缓存。

Key = sha256(tool_name + canonical_json(args))；TTL 短（默认 60s）。
范围裁剪（2026-08-10）：本地不自建 RAG（未来走外部 RAG 接口，本地检索用
grep），故本层只做工具结果缓存，不含 embedding / 检索缓存。
"""
from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from agent.llm.normalize import canonical_json
from agent.safety.write_detector import is_write_call

# 幂等只读白名单（保守起步：纯本地读取 / 纯计算）
_CACHEABLE_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "list_dir",
        "grep",
        "stat_file",
        "find",
        "glob",
        "calculator",
        "json_parse",
        "json_format",
        "regex_match",
        "url_parse",
        "csv_parse",
        "text_split",
        "hash",
        "base64",
        "uuid4",
    }
)


class ToolResultCache:
    """内存 LRU + TTL 工具结果缓存（值可为任意结果 dict）。"""

    def __init__(
        self,
        *,
        max_size: int = 512,
        ttl_sec: float = 60.0,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max(1, max_size)
        self._ttl = ttl_sec
        self._now = now_fn
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Any | None:
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        value, expires_at = entry
        if self._now() >= expires_at:
            del self._store[key]
            self.misses += 1
            return None
        self._store.move_to_end(key)
        self.hits += 1
        return value

    def put(self, key: str, value: Any) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, self._now() + self._ttl)
        while len(self._store) > self._max:
            self._store.popitem(last=False)

    def clear(self) -> None:
        self._store.clear()
        self.hits = 0
        self.misses = 0

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


_TOOL_RESULT_CACHE = ToolResultCache()
_ENABLED = True


def set_tool_cache_enabled(enabled: bool) -> None:
    """一键开关（回滚用）。"""
    global _ENABLED
    _ENABLED = bool(enabled)


def is_tool_cache_enabled() -> bool:
    return _ENABLED


def get_tool_cache() -> ToolResultCache:
    """暴露实例（统计 / 测试清理）。"""
    return _TOOL_RESULT_CACHE


def make_tool_cache_key(name: str, args: dict[str, Any]) -> str:
    raw = f"tool\x00{name}\x00{canonical_json(args)}".encode()
    return "l3:" + hashlib.sha256(raw).hexdigest()


def cacheable_tool(name: str, args: dict[str, Any]) -> bool:
    """是否允许缓存该工具调用：白名单 + 非写（双重防御）。"""
    if not _ENABLED or name not in _CACHEABLE_TOOLS:
        return False
    return not is_write_call({"name": name, "args": args})


def lookup(name: str, args: dict[str, Any]) -> Any | None:
    if not cacheable_tool(name, args):
        return None
    return _TOOL_RESULT_CACHE.get(make_tool_cache_key(name, args))


def store(name: str, args: dict[str, Any], result: Any) -> None:
    if not cacheable_tool(name, args):
        return
    _TOOL_RESULT_CACHE.put(make_tool_cache_key(name, args), result)
