"""生成限制两级回退（gen limits）—— 最大输出长度 / 默认上下文长度的全局默认。

借鉴 DeepSeek Harness（dsh）的配置层级设计：每模型值优先，缺失时回退全局默认。
持久化在 router.db llm_kv 表（key='gen_limits'，JSON 值），同步 sqlite3 读写
（与 router._load_max_context_from_db 同风格：启动路径 / FastAPI 端点都可用）。

字段语义：
- max_output_tokens: 全局输出上限（cap）。限制模型一次生成的最大 token 数，
  防止过度输出。作为上限注入所有 LLM chat 调用（Ollama num_predict /
  OpenAI max_tokens）——调用点自带的任务预算仍生效，cap 只降不升。
- default_context_window: 后端 max_context 列未显式设置（NULL）时的回退值；
  未配置后端行时保持旧行为（不注入 num_ctx）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from agent.config import settings

logger = logging.getLogger(__name__)

KV_KEY = "gen_limits"

#: 全局默认值（用户未配置时生效）
DEFAULT_GEN_LIMITS: dict[str, int] = {
    "max_output_tokens": 32768,
    "default_context_window": 32768,
}

#: 校验区间（非法值抛 ValueError，由端点转 422）
_FIELD_BOUNDS: dict[str, tuple[int, int]] = {
    "max_output_tokens": (1, 1_000_000),
    "default_context_window": (1024, 10_000_000),
}


def _db_path() -> str:
    """每次读 settings —— 测试用 monkeypatch 改路径后立即生效。"""
    return settings.llm_router_db_path


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS llm_kv ("
        "key TEXT PRIMARY KEY, value TEXT NOT NULL, "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
    )


def load_gen_limits() -> dict[str, int]:
    """读全局生成限制；无记录 / 读库失败时返回默认值（不抛异常）。"""
    try:
        conn = sqlite3.connect(_db_path(), timeout=5)
        try:
            _ensure_table(conn)
            row = conn.execute("SELECT value FROM llm_kv WHERE key=?", (KV_KEY,)).fetchone()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("gen_limits read failed: %s", exc)
        return dict(DEFAULT_GEN_LIMITS)
    if not row:
        return dict(DEFAULT_GEN_LIMITS)
    try:
        data = json.loads(row[0])
    except (ValueError, TypeError):
        return dict(DEFAULT_GEN_LIMITS)
    merged = dict(DEFAULT_GEN_LIMITS)
    for key in DEFAULT_GEN_LIMITS:
        raw = data.get(key) if isinstance(data, dict) else None
        if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
            merged[key] = raw
    return merged


def save_gen_limits(patch: dict[str, Any]) -> dict[str, int]:
    """合并 patch 后落库（llm_kv upsert），返回合并后的完整配置。

    非法字段名或越界值抛 ValueError（端点层转 HTTP 422）。
    """
    merged = load_gen_limits()
    for key, value in patch.items():
        if key not in _FIELD_BOUNDS:
            raise ValueError(f"unknown field: {key}")
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(f"{key} must be an integer")
        lo, hi = _FIELD_BOUNDS[key]
        if not lo <= value <= hi:
            raise ValueError(f"{key} out of range [{lo}, {hi}]")
        merged[key] = value
    conn = sqlite3.connect(_db_path(), timeout=5)
    try:
        _ensure_table(conn)
        conn.execute(
            "INSERT INTO llm_kv (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (KV_KEY, json.dumps(merged, ensure_ascii=False)),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("gen_limits_saved %s", merged)
    return merged


def max_output_cap() -> int | None:
    """供 LLM 客户端构造时取输出上限；任何异常兜底 None（= 不注入 cap）。"""
    try:
        return load_gen_limits()["max_output_tokens"]
    except Exception:  # 防御：启动路径绝不因配置读取失败而阻塞
        return None


def default_context_window() -> int:
    """供行级回退：后端 max_context 列为 NULL 时用此值补位。"""
    try:
        return load_gen_limits()["default_context_window"]
    except Exception:
        return DEFAULT_GEN_LIMITS["default_context_window"]
