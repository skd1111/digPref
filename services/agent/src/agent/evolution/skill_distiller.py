"""evolution.skill_distiller —— 成功轨迹蒸馏技能草稿（L2，Voyager 裁剪，设计文档 §4）。

裁剪原则（红线对齐）：
    - 本项目 Skill 是**规则类提示词**（YAML），不生成可执行代码技能
    - 草稿默认 `enabled: false`，**永不自动启用**；人工审核通过后才写入
      `skills/` 目录并经现有 `SkillLoader.load_one` 生效
    - 蒸馏前过 `validate_skill_yaml` + `validate_no_dsn`，校验不过直接丢弃

触发条件（`maybe_distill_for_signature`）：
    - 同签名成功轨迹 ≥ `settings.skill_draft_min_successes`
    - 该签名尚无成功轨迹命中过 Skill（蒸馏器只补无覆盖的任务类型）
    - 同签名无既有待审草稿（防重复蒸馏）
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import yaml

from agent.config import settings
from agent.evolution import storage
from agent.evolution.events import emit_evolution_event
from agent.llm.prompts import load_prompt, render_prompt
from agent.skills.schema import validate_no_dsn, validate_skill_yaml

logger = logging.getLogger(__name__)

# SSE 通道名（与 sse_bridge.rs::channel + events.ts::EVT 三处同步）
EVT_SKILL_DRAFT_READY: str = "skill_draft_ready"

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.M)
_MAX_CONTEXT_CHARS = 3000


def parse_skill_yaml_output(text: str) -> dict[str, Any] | None:
    """解析蒸馏输出为 Skill 数据（剥围栏 → YAML → 双重校验 → 强制不启用）。

    任何解析 / 校验失败返 None（草稿宁缺毋滥，设计文档 §4.2）。
    """
    if not text or not text.strip():
        return None
    cleaned = _FENCE_RE.sub("", text.strip())
    try:
        data = yaml.safe_load(cleaned)
    except yaml.YAMLError as exc:
        logger.info("[evolution] skill draft YAML parse failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None
    # 红线：无论模型写什么，草稿一律不启用（人工审核闸门）
    data["enabled"] = False
    errors = validate_skill_yaml(data)
    if errors:
        logger.info("[evolution] skill draft schema invalid: %s", errors[:3])
        return None
    dsn_errors = validate_no_dsn(data)
    if dsn_errors:
        logger.warning("[evolution] skill draft contains DSN patterns: %s", dsn_errors[:3])
        return None
    return data


def _build_distill_context(trajectories: list[dict[str, Any]]) -> str:
    """把多条成功轨迹拼成蒸馏输入（截断防 token 爆炸）。"""
    parts: list[str] = []
    for i, t in enumerate(trajectories, 1):
        lines = [f"轨迹 {i}:"]
        try:
            intent = json.loads(t.get("intent_json") or "{}")
        except (TypeError, ValueError):
            intent = {}
        if isinstance(intent, dict):
            query = str(intent.get("rewritten_query") or "")[:150]
            if query:
                lines.append(f"  - 请求：{query}")
        if t.get("tool_fp"):
            lines.append(f"  - 工具序列：{str(t['tool_fp'])[:150]}")
        digest = str(t.get("answer_digest") or "").strip()
        if digest:
            lines.append(f"  - 结果摘要：{digest[:200]}")
        parts.append("\n".join(lines))
    return "\n".join(parts)[:_MAX_CONTEXT_CHARS]


async def run_skill_distill(
    task_signature: str,
    trajectories: list[dict[str, Any]] | None = None,
    *,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    """对同签名成功轨迹执行蒸馏，产出草稿落库 + emit 事件。

    返回草稿 dict（测试与 API 用）；任何失败返 None（best-effort）。
    """
    if not settings.evolution_enabled:
        return None
    trajs = trajectories or await storage.successful_trajectories(task_signature)
    context = _build_distill_context(trajs)
    if not context:
        return None
    try:
        prompt = render_prompt(load_prompt("evolution/skill_distill"), CONTEXT=context)
    except Exception as exc:
        logger.warning("[evolution] skill_distill prompt load failed: %s", exc)
        return None

    try:
        from agent.llm.router import LMRouter

        text = await LMRouter().route(task="skill_distill", prompt=prompt)
    except Exception as exc:
        logger.info("[evolution] skill_distill LLM unavailable, skipped: %s", exc)
        return None

    data = parse_skill_yaml_output(text)
    if data is None:
        return None

    slug = str(data["id"])
    yaml_text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    source_sessions = ",".join(
        str(t.get("session_id") or "") for t in trajs[:3] if t.get("session_id")
    )
    try:
        draft_id = await storage.insert_skill_draft(
            slug=slug,
            name=str(data.get("name") or slug),
            yaml_text=yaml_text,
            task_signature=task_signature,
            source_session=source_sessions[:200],
            db_path=db_path,
        )
    except Exception as exc:
        logger.warning("[evolution] skill draft insert failed: %s", exc)
        return None

    emit_evolution_event(
        EVT_SKILL_DRAFT_READY,
        {
            "kind": "skill_draft_ready",
            "draft_id": draft_id,
            "slug": slug,
            "name": str(data.get("name") or slug),
            "task_signature": task_signature,
        },
    )
    logger.info("[evolution] skill draft created id=%s slug=%s", draft_id, slug)
    return {"id": draft_id, "slug": slug, "name": str(data.get("name") or slug)}


async def maybe_distill_for_signature(
    task_signature: str, *, db_path: str | None = None
) -> dict[str, Any] | None:
    """触发条件检查 + 蒸馏（成功轨迹收尾钩子调用，后台 best-effort）。"""
    if not settings.evolution_enabled or not task_signature:
        return None
    try:
        if await storage.has_draft_for_signature(task_signature):
            return None
        count = await storage.success_count_by_signature(task_signature)
        if count < settings.skill_draft_min_successes:
            return None
        # 无 Skill 覆盖才蒸馏：若成功轨迹里出现过 active_skill，说明已有技能承接
        trajs = await storage.successful_trajectories(task_signature)
        if any(t.get("active_skill_id") for t in trajs):
            return None
        return await run_skill_distill(task_signature, trajs)
    except Exception as exc:
        logger.warning("[evolution] skill distill check failed sig=%s: %s", task_signature, exc)
        return None
