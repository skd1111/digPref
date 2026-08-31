"""evolution.api —— /evolution 端点（用户反馈 + 经验库 + 技能草稿审核，设计文档 §2.2 / §3.3 / §4）。

端点：
    - POST /evolution/feedback              用户 👍/👎 显式反馈（最高优先级信号）
    - GET  /evolution/experiences           经验库列表（管理页）
    - POST /evolution/experiences/{id}/toggle  启停经验（人工干预）
    - DELETE /evolution/experiences/{id}    删除经验（人工干预）
    - GET  /evolution/skill-drafts          技能蒸馏草稿列表（V1）
    - POST /evolution/skill-drafts/{id}/approve  审核通过 → 写入 skills/ 并启用（V1）
    - POST /evolution/skill-drafts/{id}/reject   拒绝草稿（V1）
    - GET  /evolution/stats                 进化看板统计（V1）
    - POST /evolution/prompt-optimization/run    运行 Few-shot 影子优化实验（V1.5）
    - GET  /evolution/prompt-versions            Prompt 版本列表（V1.5）
    - POST /evolution/prompt-versions/{id}/apply     采纳版本（写回技能）（V1.5）
    - POST /evolution/prompt-versions/{id}/rollback  回滚到上一版本（V1.5）

👎 反馈直接触发该轨迹的反思（用户反馈是自评测的最高置信信号，
设计文档 §2.2）；反思在后台执行，API 不等待其完成。

红线（设计文档 §4）：技能草稿默认不启用，approve 是唯一启用入口，
且需人工显式调用；批准前重跑 schema + DSN 校验。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.evolution import reflection, storage
from agent.skills.schema import validate_no_dsn, validate_skill_yaml

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

    # 意图闭环反馈（2026-08-31）：👍 将该 run 的路由案例置 positive（动态
    # Few-Shot 优先）；👎 置 negative 且原始查询回流困难样本库（语义路由负样本）。
    try:
        from agent.graph import intent_memory

        if body.rating == "up":
            await intent_memory.mark_positive(body.session_id)
        else:
            await intent_memory.harden_by_run(body.session_id)
    except Exception as exc:
        logger.debug("[evolution] intent feedback hook failed: %s", exc)

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


# ---- 技能草稿审核（V1） ---------------------------------------------------


@router.get("/skill-drafts")
async def get_skill_drafts() -> dict[str, Any]:
    try:
        items = await storage.list_skill_drafts(status="draft")
    except Exception as exc:
        raise HTTPException(503, f"草稿读取失败：{exc}") from exc
    return {"ok": True, "items": items}


@router.post("/skill-drafts/{draft_id}/approve")
async def approve_skill_draft(draft_id: int) -> dict[str, Any]:
    """审核通过：重跑校验 → 写入 skills/ 目录（enabled: true）→ load_one 生效。

    这是草稿启用的**唯一**入口（人工闸门；红线：草稿永不自动启用）。
    """
    draft = await storage.get_skill_draft(draft_id)
    if draft is None:
        raise HTTPException(404, "草稿不存在")
    if draft["status"] != "draft":
        raise HTTPException(409, f"草稿已{draft['status']}，不可重复审核")
    try:
        data = yaml.safe_load(draft["yaml_text"])
    except yaml.YAMLError as exc:
        raise HTTPException(400, f"草稿 YAML 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise HTTPException(400, "草稿 YAML 不是对象")
    errors = validate_skill_yaml(data)
    if errors:
        raise HTTPException(400, {"validation_errors": errors})
    dsn_errors = validate_no_dsn(data)
    if dsn_errors:
        raise HTTPException(400, {"dsn_errors": dsn_errors})

    # 人工批准后才启用；写入既有 Skill 目录走现有 loader 生效
    data["enabled"] = True
    from agent.skills import api as skills_api

    loader = skills_api.get_loader()
    slug = str(data["id"])
    if loader.get(slug) is not None:
        raise HTTPException(409, f"skill {slug} 已存在，请先删除或改名")
    path = loader._dir / f"{slug}.yaml"
    if path.exists():
        raise HTTPException(409, f"skill 文件 {slug}.yaml 已存在")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)
    skill = loader.load_one(path)
    if skill is None:
        raise HTTPException(500, "草稿写入后加载失败")
    await storage.set_skill_draft_status(draft_id, "approved")
    return {"ok": True, "id": draft_id, "skill_id": slug, "path": str(path)}


@router.post("/skill-drafts/{draft_id}/reject")
async def reject_skill_draft(draft_id: int) -> dict[str, Any]:
    draft = await storage.get_skill_draft(draft_id)
    if draft is None:
        raise HTTPException(404, "草稿不存在")
    await storage.set_skill_draft_status(draft_id, "rejected")
    return {"ok": True, "id": draft_id, "status": "rejected"}


# ---- 进化看板（V1） -------------------------------------------------------


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    try:
        summary = await storage.stats_summary()
    except Exception as exc:
        raise HTTPException(503, f"统计读取失败：{exc}") from exc
    return {"ok": True, **summary}


# ---- Prompt 影子优化（V1.5） ------------------------------------------------


class PromptOptRunRequest(BaseModel):
    skill_id: str = Field(alias="skillId")
    task_signature: str = Field(default="", alias="taskSignature")

    model_config = {"populate_by_name": True}


@router.post("/prompt-optimization/run")
async def run_prompt_optimization(body: PromptOptRunRequest) -> dict[str, Any]:
    """手动触发一次 Few-shot 影子优化实验（离线回放，不影响在线链路）。

    同步返回实验结果（桌面端单次实验耗时可接受；LLM 多次调用）。
    """
    from agent.evolution import prompt_opt

    try:
        result = await prompt_opt.run_prompt_experiment(
            skill_id=body.skill_id, task_signature=body.task_signature
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, **result}


@router.get("/prompt-versions")
async def get_prompt_versions(skill_id: str = "") -> dict[str, Any]:
    try:
        items = await storage.list_prompt_versions(skill_id or None)
    except Exception as exc:
        raise HTTPException(503, f"版本读取失败：{exc}") from exc
    return {"ok": True, "items": items}


@router.post("/prompt-versions/{version_id}/apply")
async def apply_prompt_version(version_id: int) -> dict[str, Any]:
    """采纳版本：把该版 few-shot 写回技能 YAML 并重载；原 active 降级为 rolled_back。"""
    from agent.evolution import prompt_opt

    ver = await storage.get_prompt_version(version_id)
    if ver is None:
        raise HTTPException(404, "版本不存在")
    if ver["status"] == "active":
        raise HTTPException(409, "该版本已是生效版本")
    if not prompt_opt._apply_few_shot_to_skill(ver["skill_id"], ver["few_shot"]):
        raise HTTPException(500, "写回技能失败（技能文件不存在或不可读）")
    # 同 skill 的其他 active 版本降级（与自动采纳共用同一降级函数，保证单 active）
    await prompt_opt._demote_other_actives(ver["skill_id"], version_id)
    await storage.set_prompt_version_status(version_id, "active")
    return {"ok": True, "id": version_id, "skill_id": ver["skill_id"], "status": "active"}


@router.post("/prompt-versions/{version_id}/rollback")
async def rollback_prompt_version(version_id: int) -> dict[str, Any]:
    """一键回滚：恢复到上一版本的 few-shot（无上一版则清空示例）。"""
    from agent.evolution import prompt_opt

    ver = await storage.get_prompt_version(version_id)
    if ver is None:
        raise HTTPException(404, "版本不存在")
    if ver["status"] != "active":
        raise HTTPException(409, "只有生效版本可回滚")
    versions = await storage.list_prompt_versions(ver["skill_id"])
    prev = next((v for v in versions if v["version"] < ver["version"]), None)
    restore_to = prev["few_shot"] if prev is not None else []
    if not prompt_opt._apply_few_shot_to_skill(ver["skill_id"], restore_to):
        raise HTTPException(500, "写回技能失败（技能文件不存在或不可读）")
    await storage.set_prompt_version_status(version_id, "rolled_back")
    if prev is not None:
        await storage.set_prompt_version_status(prev["id"], "active")
    return {
        "ok": True,
        "id": version_id,
        "skill_id": ver["skill_id"],
        "rolled_back_to": prev["version"] if prev is not None else 0,
    }
