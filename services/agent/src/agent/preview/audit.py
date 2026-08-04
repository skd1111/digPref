"""Phase 15 V0 · 预览引擎审计写入（复用 Phase 1 audit.sqlite）。

CLAUDE.md §6：preview 写操作走 audit（`actor_type='user'`）。
动作：preview_session_started / preview_session_stopped / preview_session_errored。
"""

from __future__ import annotations

import asyncio
from typing import Any


async def audit_preview(action: str, payload: dict[str, Any]) -> None:
    """写审计记录（best-effort，不阻塞业务响应）。"""
    try:
        from agent.audit.store import audit

        await audit(
            action=action,
            payload=payload,
            actor_type="user",
            event_type=action,
        )
    except Exception:  # noqa: BLE001 —— 审计失败不阻断预览
        pass


def audit_preview_sync(action: str, payload: dict[str, Any]) -> None:
    """同步版（线程内调用）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        try:
            loop.create_task(audit_preview(action, payload))
            return
        except RuntimeError:
            pass
    # 无运行 loop 时直接跑（同步路径）
    try:
        import asyncio as _asyncio

        from agent.audit.store import audit

        _asyncio.run(
            audit(
                action=action,
                payload=payload,
                actor_type="user",
                event_type=action,
            )
        )
    except Exception:  # noqa: BLE001
        pass
