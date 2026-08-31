"""evolution.api —— /evolution 端点（用户反馈 + 经验库管理，设计文档 §2.2 / §3.3）。

端点：
    - POST /evolution/feedback              用户 👍/👎 显式反馈（最高优先级信号）
    - GET  /evolution/experiences           经验库列表（管理页）
    - POST /evolution/experiences/{id}/toggle  启停经验（人工干预）
    - DELETE /evolution/experiences/{id}    删除经验（人工干预）

👎 反馈直接触发该轨迹的反思（用户反馈是自评测的最高置信信号，
设计文档 §2.2）；反思在后台执行，API 不等待其完成。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.evolution import reflection, storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/evolution", tags=["evolution"])

# 后台反思任务持参集（防 create_task 返回值被 GC 提前回收；完成后自弃）
_BACKGROUND_TASKS: set[asyncio.Task[None]] = set()


class FeedbackRequest(BaseModel):
    # runId = 会话 run 标识（轨迹按它归属）；taskId 为任务页签（可选）
    session_id: str = Field(alias="sessionId")
    message_id: str = Field(default="", alias="messageId")
    trajectory_id: int | None = Field(default=None, alias="trajectoryId")
    rating: Literal["up", "down"]
    correction: str = ""

    model_config = {"populate_by_name": True}


@router.post("/feedback")
async def post_feedback(body: FeedbackRequest) -> dict[str, Any]:
    """记录用户显式反馈；👎 后台触发反思。"""
    trajectory = None
    try:
        if body.trajectory_id:
            trajectory = await storage.get_trajectory(body.trajectory_id)
        if trajectory is None:
            trajectory = await storage.latest_trajectory_by_session(body.session_id)
    except Exception as exc:
        logger.warning("[evolution] feedback trajectory lookup failed: %s", exc)
        trajectory = None

    signature = str((trajectory or {}).get("task_signature") or "")
    try:
        await storage.record_signal(
            session_id=body.session_id,
            message_id=body.message_id,
            task_signature=signature,
            source="user",
            score=1.0 if body.rating == "up" else 0.0,
            rating=1 if body.rating == "up" else 0,
            correction=body.correction[:500],
        )
    except Exception as exc:
        raise HTTPException(503, f"反馈记录失败：{exc}") from exc

    reflected = False
    if body.rating == "down" and trajectory is not None:
        # 用户纠错文本并入反思上下文（最高置信反馈信号）
        traj = dict(trajectory)
        traj["failure_reason"] = body.correction or "用户标记了不满意（👎）"
        try:
            intent_json = traj.get("intent_json") or "{}"
            import json

            parsed = json.loads(intent_json) if isinstance(intent_json, str) else {}
            traj["intent"] = parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            traj["intent"] = {}
        task = asyncio.create_task(_reflect_best_effort(traj))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        reflected = True
    return {
        "ok": True,
        "reflected": reflected,
        "task_signature": signature,
    }


async def _reflect_best_effort(trajectory: dict[str, Any]) -> None:
    try:
        await reflection.run_reflection(trajectory)
    except Exception as exc:
        logger.warning("[evolution] feedback reflection failed: %s", exc)


# ---- 经验库管理 -----------------------------------------------------------


@router.get("/experiences")
async def get_experiences() -> dict[str, Any]:
    try:
        items = await storage.list_experiences()
    except Exception as exc:
        raise HTTPException(503, f"经验库读取失败：{exc}") from exc
    return {"ok": True, "items": items}


@router.post("/experiences/{experience_id}/toggle")
async def toggle_experience(experience_id: int) -> dict[str, Any]:
    """启停切换（前端先读状态再调；后端按当前态翻转）。"""
    try:
        items = await storage.list_experiences()
    except Exception as exc:
        raise HTTPException(503, f"经验库读取失败：{exc}") from exc
    target = next((it for it in items if it["id"] == experience_id), None)
    if target is None:
        raise HTTPException(404, "经验不存在")
    new_status = "disabled" if target["status"] == "active" else "active"
    await storage.set_experience_status(experience_id, new_status)
    return {"ok": True, "id": experience_id, "status": new_status}


@router.delete("/experiences/{experience_id}")
async def delete_experience(experience_id: int) -> dict[str, Any]:
    deleted = await storage.delete_experience(experience_id)
    if not deleted:
        raise HTTPException(404, "经验不存在")
    return {"ok": True, "id": experience_id}
