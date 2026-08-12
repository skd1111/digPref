"""请求规范化与稳定缓存 Key（Phase 17 V0）。

缓存命中前必须先做请求规范化 —— 相同请求必须产生相同规范化表示：
    - JSON 字段顺序固定（canonical_json sort_keys）；
    - 文本空白归一（strip + 折叠连续空白）；
    - request_id / trace_id / 时间戳等随机字段**绝不**进入缓存 Key
      （CLAUDE.md 红线：凭证 / DSN 同样禁止进入）。

设计文档：docs/design/phase-17-cache-hit-rate.md §3.1
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: Any) -> str:
    """canonical JSON 序列化：key 排序 + 紧凑分隔符 + 保留中文。

    不可序列化对象用 default=str 兜底（保证 key 稳定可计算，
    绝不因序列化失败而绕过缓存逻辑）。
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def normalize_text(text: str) -> str:
    """空白归一：首尾 strip + 连续空白折叠为单空格。"""
    return " ".join(str(text).split())


def build_response_cache_key(
    *,
    task_kind: str,
    intent: str,
    user_prompt: str,
    plan: list[Any],
    results: list[Any],
) -> str:
    """L1 精确响应缓存 Key：sha256(canonical(规范化请求载荷))。

    进入 key 的字段：task_kind / intent / 归一化 user_prompt / plan / results。
    禁止进入：request_id、trace_id、时间戳、凭证（见模块 docstring）。
    """
    payload = {
        "task_kind": task_kind,
        "intent": intent,
        "user_prompt": normalize_text(user_prompt),
        "plan": plan,
        "results": results,
    }
    digest = hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()
    return f"l1:{digest}"
