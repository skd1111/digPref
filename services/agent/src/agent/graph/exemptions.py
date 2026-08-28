"""HITL 会话级审批豁免集（2026-08-25，「此后都按此执行」）。

用户在审批卡选择「此后都按此执行」（decision=approve_always）后，本会话内
同一工具类（同 server·name）的后续写操作自动放行，不再弹卡；硬阻断
（DROP/TRUNCATE，由 is_hard_blocked 判定）永远优先于豁免。

作用域键 = 前端会话页签 id（page_context.page.tabId，ChatInput 透传）：
    - 豁免只在本会话生效，切到新页签/新会话不复用（用户明确要求）；
    - 未携带 tabId 的旧客户端回落 "default" 作用域（进程级，重启清空）。

存储为进程内存（不落 Redis）：豁免是运行态便利授权，与「会话自主性」同生命周期
——重启即回收是安全默认值；每次自动放行仍全量审计（HITL_SESSION_EXEMPT /
hitl_gate trace），可追溯。
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

# 作用域 → 豁免的工具类键集合（键格式 "server·name"）
_EXEMPT: dict[str, set[str]] = {}


def tool_kind_key(call: dict) -> str:
    """工具类标识：同 server + 同 name 视为同一类操作。"""
    return f"{call.get('server') or '?'}·{call.get('name') or '?'}"


def exemption_scope(state: Any) -> str:
    """从 graph state 推导豁免作用域（当前聊天页签）。"""
    if isinstance(state, dict):
        page_ctx = state.get("page_context")
        if isinstance(page_ctx, dict):
            page = page_ctx.get("page")
            if isinstance(page, dict):
                tab_id = str(page.get("tabId") or "").strip()
                if tab_id:
                    return f"tab:{tab_id}"
    return "default"


def add_exempt(scope: str, key: str) -> None:
    _EXEMPT.setdefault(scope, set()).add(key)
    log.info("HITL 会话豁免已登记: scope=%s kind=%s", scope, key)


def is_exempt(scope: str, key: str) -> bool:
    return key in _EXEMPT.get(scope, set())


def clear_scope(scope: str) -> None:
    """测试/会话结束时清理作用域。"""
    _EXEMPT.pop(scope, None)
