"""orchestrator.observability —— Phase 12 V1.5 结构化事件日志。

设计意图：
    - 子 Agent 全链路事件（spawn / progress / done / retry / dlq / cancel /
      hitl / judge）落到本地 `logs/orchestrator-YYYYMMDD.jsonl` 文件
    - 不引入 ELK 依赖（架构决策 2026-07-31）；logs/ 是合规权威的副本
    - 写文件失败时降级 stderr，绝不阻塞主图

CLAUDE.md §2 红线遵守：
    - 敏感负载（DB 行 / SQL 错误 / PII）经 scrub 后再写文件
    - 文件仅供检索分析；合规追溯以 audit.sqlite 为准

使用：
    from agent.orchestrator.observability import get_default_logger
    logger = get_default_logger()
    await logger.log_event(
        event_type="sub_agent_spawn",
        correlation_id="run-1:sub-1",
        task_id="sub-1",
        actor_type="sub_agent",
        payload={"depth": 1, "task_type": "data_summary"},
    )
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---- 敏感字段脱敏（CLAUDE.md §2）--------------------------------------

# 与 Phase 2F+ loganalysis/scrubber.py 同款 8 类 PII 正则；本文件轻量自维护，
# 不依赖 loganalysis 避免循环依赖。
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PHONE", re.compile(r"\b1[3-9]\d{9}\b")),
    ("ID_CARD", re.compile(r"\b\d{17}[\dXx]\b")),
    ("BANK_CARD", re.compile(r"\b\d{16,19}\b")),
    ("AWS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    ("IPV4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("EMAIL", re.compile(r"\b[\w._%+-]+@[\w.-]+\.[A-Za-z]{2,}\b")),
)

# 永远红化的 JSON 字段名（不区分大小写）
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "dsn",
        "connection_string",
        "private_key",
        "authorization",
        "session",
        "cookie",
        "credit_card",
    }
)


def _scrub_value(v: Any, *, _depth: int = 0) -> Any:
    """递归脱敏：dict / list / str / 其他原样返回。"""
    if _depth > 5:
        return "<truncated-depth>"
    if isinstance(v, dict):
        return {
            k: (
                "<redacted>"
                if k.lower() in _SENSITIVE_KEYS
                else _scrub_value(val, _depth=_depth + 1)
            )
            for k, val in v.items()
        }
    if isinstance(v, list):
        return [_scrub_value(x, _depth=_depth + 1) for x in v]
    if isinstance(v, str):
        out = v
        for label, pat in _PII_PATTERNS:
            out = pat.sub(f"[REDACTED-{label}]", out)
        return out
    return v


# ---- 结构化事件 envelope ---------------------------------------------

# 标准 7 字段 envelope（与 Phase 12 设计文档 §3.1 ELK 字段对齐）：
#   ts            — RFC 3339 UTC
#   event_type    — spawn / progress / done / retry / dlq / cancel / hitl / judge
#   correlation_id — 一棵决策树共享
#   actor_type    — main_agent / sub_agent / system
#   task_id       — sub_agent_id（主 Agent 时 None）
#   parent_task_id — 父 sub_agent_id
#   payload       — 业务负载（已 scrub）
_ALLOWED_ACTOR_TYPES = frozenset({"main_agent", "sub_agent", "system", "user"})


class StructuredLogger:
    """结构化 JSON 日志写入器：logs/orchestrator-YYYYMMDD.jsonl。

    - 异步写入（asyncio.Lock + 队列）；写文件失败 → stderr 兜底
    - 文件按本地日期滚动（YYYYMMDD.jsonl）
    - 单实例（懒初始化；测试可 reset_default_logger 重建）
    """

    def __init__(
        self,
        log_dir: str | os.PathLike[str] | None = None,
        max_bytes: int = 50 * 1024 * 1024,  # 50MB / 文件
    ) -> None:
        # 默认 logs/ 路径：相对 cwd（pytest 也走 tmp_path 隔离）
        self._log_dir = Path(log_dir) if log_dir else Path("logs")
        self._max_bytes = max_bytes
        self._lock = asyncio.Lock()
        # 当前打开的 file handle（按日期切）
        self._current_date: str = ""
        self._current_handle: Any | None = None

    # ---- 公开 API --------------------------------------------------

    async def log_event(
        self,
        *,
        event_type: str,
        correlation_id: str,
        payload: dict[str, Any],
        actor_type: str = "sub_agent",
        task_id: str | None = None,
        parent_task_id: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """异步写一条结构化事件。失败不抛异常（降级 stderr）。"""
        if actor_type not in _ALLOWED_ACTOR_TYPES:
            actor_type = "sub_agent"  # 兜底归类

        envelope = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": event_type,
            "correlation_id": correlation_id,
            "actor_type": actor_type,
            "task_id": task_id,
            "parent_task_id": parent_task_id,
            "run_id": run_id,
            "payload": _scrub_value(payload),
        }
        try:
            await self._write_line(envelope)
        except Exception as exc:
            # 兜底：stderr 单行；不阻塞主流程
            print(f"[observability] log_event failed: {exc}", flush=True)

    async def log_sub_agent_spawn(
        self,
        *,
        correlation_id: str,
        sub_agent_id: str,
        parent_sub_agent_id: str | None,
        run_id: str,
        task_type: str,
        task_description: str,
        depth: int,
        requires_write: bool,
        backend: str,
    ) -> None:
        await self.log_event(
            event_type="sub_agent_spawn",
            correlation_id=correlation_id,
            task_id=sub_agent_id,
            parent_task_id=parent_sub_agent_id,
            run_id=run_id,
            payload={
                "task_type": task_type,
                "task_description_preview": task_description[:200],
                "depth": depth,
                "requires_write": requires_write,
                "backend": backend,
            },
        )

    async def log_sub_agent_progress(
        self,
        *,
        correlation_id: str,
        sub_agent_id: str,
        parent_sub_agent_id: str | None,
        run_id: str,
        attempt: int,
        status: str,
        elapsed_ms: int,
    ) -> None:
        await self.log_event(
            event_type="sub_agent_progress",
            correlation_id=correlation_id,
            task_id=sub_agent_id,
            parent_task_id=parent_sub_agent_id,
            run_id=run_id,
            payload={
                "attempt": attempt,
                "status": status,
                "elapsed_ms": elapsed_ms,
            },
        )

    async def log_sub_agent_done(
        self,
        *,
        correlation_id: str,
        sub_agent_id: str,
        parent_sub_agent_id: str | None,
        run_id: str,
        status: str,
        attempts: int,
        latency_ms: int,
        backend: str,
        model: str,
    ) -> None:
        await self.log_event(
            event_type="sub_agent_done",
            correlation_id=correlation_id,
            task_id=sub_agent_id,
            parent_task_id=parent_sub_agent_id,
            run_id=run_id,
            payload={
                "status": status,
                "attempts": attempts,
                "latency_ms": latency_ms,
                "backend": backend,
                "model": model,
            },
        )

    async def log_hitl_decision(
        self,
        *,
        correlation_id: str,
        approval_id: str,
        decision: str,
        decided_by: str,
        risk_level: str,
    ) -> None:
        await self.log_event(
            event_type="sub_agent_hitl",
            correlation_id=correlation_id,
            actor_type="sub_agent",
            payload={
                "approval_id": approval_id,
                "decision": decision,
                "decided_by": decided_by,
                "risk_level": risk_level,
            },
        )

    # ---- 内部 ------------------------------------------------------

    async def _write_line(self, envelope: dict[str, Any]) -> None:
        async with self._lock:
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            if self._current_date != today or self._current_handle is None:
                await self._rotate(today)
            line = json.dumps(envelope, ensure_ascii=False, default=str)
            self._current_handle.write(line + "\n")
            self._current_handle.flush()
            # 大小检查
            try:
                size = self._current_handle.tell()
                if size > self._max_bytes:
                    self._current_handle.close()
                    self._current_handle = None  # 下次 _rotate 触发
            except OSError:
                pass

    async def _rotate(self, today: str) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        if self._current_handle:
            try:
                self._current_handle.close()
            except Exception:
                pass
        path = self._log_dir / f"orchestrator-{today}.jsonl"
        # append 模式；编码 utf-8；newline='' 让 jsonl 不重复换行
        # 长生命周期句柄，由 _rotate / aclose 显式关闭，不适用 with 块
        self._current_handle = open(path, "a", encoding="utf-8", newline="")  # noqa: SIM115
        self._current_date = today

    async def aclose(self) -> None:
        """关闭当前 file handle（测试 + 优雅停机）。"""
        async with self._lock:
            if self._current_handle:
                try:
                    self._current_handle.close()
                except Exception:
                    pass
                self._current_handle = None
                self._current_date = ""


# ---- 全局单例 -------------------------------------------------------

_default_logger: StructuredLogger | None = None
_default_lock = asyncio.Lock()


def get_default_logger() -> StructuredLogger:
    """获取全局 StructuredLogger（懒初始化）。"""
    global _default_logger
    if _default_logger is None:
        _default_logger = StructuredLogger()
    return _default_logger


def reset_default_logger(log_dir: str | os.PathLike[str] | None = None) -> StructuredLogger:
    """测试 hook：重建 StructuredLogger。"""
    global _default_logger
    _default_logger = StructuredLogger(log_dir=log_dir)
    return _default_logger
