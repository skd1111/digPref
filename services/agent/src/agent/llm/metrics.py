"""路由指标记录（metrics.py）。

V0 实现：把 RoutingDecision 写入内存 + 异步写 router.db。
V1 升级：完整 SSE 事件 + Prometheus exporter。

CLAUDE.md 红线：metrics 走 router.db 独立表，**不**复用 audit.sqlite。

V2 增量：
    - `_router_db_path()` 改用 `settings.llm_router_db_path`（修原硬编 `%APPDATA%` 的
      bug —— 测试时 monkeypatch.chdir(tmp_path) 后 settings 默认 "router.db" 会落
      tmp_path；生产用 settings 配置的绝对路径）。
    - 新增 `emit_event(kind, payload)`：路由决策后异步 emit 到 in-process 订阅队列，
      `graph/stream.py` 的 `consume_router_events()` 拉出来转 SSE 推到前端。
      解决 CLAUDE.md §4 SSE 三处同步红线（Python 漏发 3 个 llm_* 事件）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from agent.llm.models import RoutingDecision

logger = logging.getLogger(__name__)


# router.db 表（独立于 audit / codenav / biznav）
# V2 起：metrics.py **不**再内联 schema —— 由 storage.py 读 schema.sql 单行建表。
# 保留本常量仅供 MetricsRecorder 内部 `_get_conn()` 第一次启动期 fallback（schema.sql
# 未跑过时仍能写入 routing_decisions；正常路径下 storage.py 先建表）。
_ROUTER_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS routing_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    task_category TEXT,
    sensitivity TEXT,
    primary_backend TEXT,
    actual_backend TEXT,
    fallback_used INTEGER,
    cache_hit INTEGER,
    estimated_cost REAL,
    actual_cost REAL,
    latency_ms INTEGER,
    quality_score REAL,
    trace_json TEXT,
    created_at INTEGER
);
CREATE INDEX IF NOT EXISTS idx_rd_request ON routing_decisions(request_id);
CREATE INDEX IF NOT EXISTS idx_rd_user ON routing_decisions(user_id);
CREATE INDEX IF NOT EXISTS idx_rd_created ON routing_decisions(created_at);
"""


def _router_db_path() -> Path:
    """router.db 路径（V2 修：不再硬编 %APPDATA%）。

    优先级：
        1. `EAIDE_LLM_ROUTER_DB_PATH` env 显式设置（绝对或相对）—— 测试用
        2. settings.llm_router_db_path（pydantic-settings 默认 "router.db"）

    - 测试：`_isolate` fixture `monkeypatch.setenv("EAIDE_LLM_ROUTER_DB_PATH", str(tmp_path / "router.db"))`
      → 走优先级 1，自动隔离
    - 生产：settings 绝对路径 → 直接用
    """
    env_path = os.environ.get("EAIDE_LLM_ROUTER_DB_PATH")
    if env_path:
        p = Path(env_path)
    else:
        from agent.config import settings

        p = Path(settings.llm_router_db_path)
    if not p.is_absolute():
        # 相对路径 + 当前 cwd；测试时 cwd 已被 chdir 到 tmp_path
        resolved = p.resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


# === V2 增量：SSE event bus（in-process 异步队列，stream.py 拉出来转 SSE） ===

# 进程级 deque + 异步锁：路由决策后 MetricsRecorder.emit_event() 写入，
# graph/stream.py 的 consume_router_events() 异步拉出来推到 SSE。
# deque 上限 1000 条防止 memory leak；旧的会被丢弃（前端不会无限累积）
_router_event_queue: deque[tuple[str, dict]] = deque(maxlen=1000)
_router_event_lock = threading.Lock()


def emit_router_event(kind: str, payload: dict) -> None:
    """公共 SSE emit 入口（模块级函数）。

    由 RouterEngine.route_request / fallback 等在决策完成后调用。
    graph/stream.py 会在 SSE 流里消费并推送。
    """
    with _router_event_lock:
        _router_event_queue.append((kind, payload))


async def consume_router_events(timeout_s: float = 0.1) -> list[tuple[str, dict]]:
    """异步拉队列（超时返回空列表）。

    graph/stream.py 在流循环里调一次；返回所有当前已入队的事件。
    测试用：调 `flush_router_events()` 清空队列。
    """
    out: list[tuple[str, dict]] = []
    # 短忙等：最多 3 次尝试拿锁
    for _ in range(3):
        with _router_event_lock:
            if not _router_event_queue:
                break
            out.append(_router_event_queue.popleft())
    if not out:
        await asyncio.sleep(timeout_s)
    return out


def flush_router_events() -> None:
    """测试夹具：清空队列。"""
    with _router_event_lock:
        _router_event_queue.clear()


class MetricsRecorder:
    """路由指标记录器（V0 单实例，thread-safe + asyncio 兼容）。"""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _router_db_path()
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.executescript(_ROUTER_DB_SCHEMA)
            self._conn.commit()
        return self._conn

    def record(self, decision: RoutingDecision) -> None:
        """异步记录一条路由决策。失败不抛（metrics 不影响主流程）。"""
        try:
            with self._lock:
                conn = self._get_conn()
                conn.execute(
                    """INSERT INTO routing_decisions (
                        request_id, user_id, task_category, sensitivity,
                        primary_backend, actual_backend, fallback_used, cache_hit,
                        estimated_cost, actual_cost, latency_ms, quality_score,
                        trace_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        decision.request_id,
                        decision.user_id,
                        decision.task_category.value if decision.task_category else None,
                        decision.sensitivity.value if decision.sensitivity else None,
                        decision.primary_backend,
                        decision.actual_backend,
                        1 if decision.fallback_used else 0,
                        1 if decision.cache_hit else 0,
                        decision.estimated_cost,
                        decision.actual_cost,
                        decision.latency_ms,
                        decision.quality_score,
                        json.dumps(decision.trace_dict(), ensure_ascii=False),
                        int(time.time() * 1000),
                    ),
                )
                conn.commit()
        except Exception as e:
            logger.warning("metrics_record_failed request_id=%s err=%s", decision.request_id, e)

    def emit_event(self, kind: str, payload: dict[str, Any]) -> None:
        """V2 新增：路由决策后 emit 到 SSE event bus。

        供 RouterEngine.route_request / spark_route 在决策完成后调用。
        best-effort：emit 失败不影响主路由（异常吞掉）。
        """
        try:
            emit_router_event(kind, payload)
        except Exception as e:
            logger.warning("metrics_emit_event_failed kind=%s err=%s", kind, e)

    def recent(self, limit: int = 100) -> list[dict]:
        """V0 简单：拉最近 limit 条决策。"""
        try:
            with self._lock:
                conn = self._get_conn()
                cur = conn.execute(
                    "SELECT request_id, primary_backend, actual_backend, latency_ms, "
                    "estimated_cost, actual_cost, created_at FROM routing_decisions "
                    "ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as e:
            logger.warning("metrics_recent_failed err=%s", e)
            return []
