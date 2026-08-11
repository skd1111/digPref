"""Phase 18 自动模式授权端点 —— 前端风险确认弹窗确认后调用。

只做一件事：把"用户显式授权开启自动模式"写入审计库（AUTO_MODE_ENABLED）。
autonomy 本身是会话级运行时状态（chatStore → chat 请求透传），后端不存储。
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agent.audit.store import audit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/autonomy", tags=["autonomy"])


class AutonomyConfirmRequest(BaseModel):
    session_id: str = Field(alias="sessionId")
    consent_version: str = Field(default="v1", alias="consentVersion")
    work_mode: Literal["full", "operator", "auditor", "analyst"] = Field(
        default="full", alias="workMode"
    )


@router.post("/confirm")
async def confirm_auto_mode(body: AutonomyConfirmRequest) -> dict:
    """记录会话级自动模式授权（合规证据：显式授权时间戳 + 文案版本）。"""
    try:
        await audit(
            "AUTO_MODE_ENABLED",
            {
                "session_id": body.session_id,
                "consent_version": body.consent_version,
                "work_mode": body.work_mode,
            },
            actor_type="user",
            event_type="AUTO_MODE_ENABLED",
        )
    except Exception as exc:
        logger.warning("audit AUTO_MODE_ENABLED failed: %s", exc)
    return {"ok": True}
