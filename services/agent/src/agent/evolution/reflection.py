"""evolution.reflection —— 失败反思提炼经验（L1，ExpeL/Reflexion 式）。

设计文档 §3.2：
    - 新 LLM 任务 `reflection`，属 `_LOCAL_ONLY_TASKS`（输入接触用户原始
      内容，敏感红线：本地优先，不可用才逐级降级）
    - 输入：轨迹摘要 + 失败原因；输出：一条结构化教训（JSON）
    - 解析失败 / LLM 不可用 → 跳过（保守回退，绝不阻塞主链路）

触发时机（V0）：
    - 任务结束时轨迹判定为失败（trajectory.py 后台任务）
    - 用户 👎 反馈（api.py 后台任务）
"""

from __future__ import annotations

import logging
from typing import Any

from agent.config import settings
from agent.evolution import storage
from agent.evolution.events import EVT_EVOLUTION_INSIGHT_CREATED, emit_evolution_event
from agent.llm.json_discipline import extract_json
from agent.llm.prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)

# 归因四分类（设计文档 §3.2）：供后续 L2（缺工具→技能蒸馏）/
# L3（prompt→优化）路由；非法值归 "unknown"
_ATTRIBUTIONS = frozenset({"prompt", "tool", "reasoning", "env"})

_MAX_INSIGHT_CHARS = 500


def parse_reflection_output(text: str) -> dict[str, Any] | None:
    """解析反思输出（JSON 优先；缺 insight 视为无效）。"""
    data = extract_json(text or "", want="object")
    if not isinstance(data, dict):
        return None
    insight = str(data.get("insight") or "").strip()
    if not insight:
        return None
    raw_tags = data.get("tags")
    tags = (
        [str(t).strip() for t in raw_tags if str(t).strip()] if isinstance(raw_tags, list) else []
    )
    attribution = str(data.get("attribution") or "").strip().lower()
    if attribution not in _ATTRIBUTIONS:
        attribution = "unknown"
    applies_to = str(data.get("applies_to") or "").strip()
    return {
        "insight": insight[:_MAX_INSIGHT_CHARS],
        "tags": tags[:8],
        "attribution": attribution,
        "applies_to": applies_to[:80],
    }


def _build_failure_context(trajectory: dict[str, Any]) -> str:
    """把轨迹摘要 + 失败原因拼成反思输入片段（截断防 token 爆炸）。"""
    parts: list[str] = []
    intent = trajectory.get("intent") or {}
    if isinstance(intent, dict) and intent:
        category = str(intent.get("intent_category") or "")
        query = str(intent.get("rewritten_query") or "")[:120]
        parts.append(f"- 任务意图：{category}（{query}）")
    if trajectory.get("active_skill_id"):
        parts.append(f"- 命中技能：{trajectory['active_skill_id']}")
    if trajectory.get("tool_fp"):
        parts.append(f"- 调用工具序列：{str(trajectory['tool_fp'])[:200]}")
    parts.append(f"- 结果：{trajectory.get('outcome') or 'fail'}")
    digest = str(trajectory.get("answer_digest") or "").strip()
    if digest:
        parts.append(f"- 最终输出摘要：{digest[:300]}")
    reason = str(trajectory.get("failure_reason") or "").strip()
    if reason:
        parts.append(f"- 失败原因 / 用户纠错：{reason[:300]}")
    return "\n".join(parts)


async def run_reflection(
    trajectory: dict[str, Any], *, db_path: str | None = None
) -> dict[str, Any] | None:
    """对一条失败 / 差评轨迹执行反思，产出经验并落库 + emit 事件。

    返回新经验 dict（测试与 API 用）；任何失败返 None（best-effort）。
    """
    if not settings.evolution_enabled:
        return None
    context = _build_failure_context(trajectory)
    if not context:
        return None
    try:
        prompt = render_prompt(load_prompt("evolution/reflection"), CONTEXT=context)
    except Exception as exc:  # 模板缺失等部署问题：静默跳过
        logger.warning("[evolution] reflection prompt load failed: %s", exc)
        return None

    try:
        from agent.llm.router import LMRouter

        text = await LMRouter().route(task="reflection", prompt=prompt)
    except Exception as exc:
        logger.info("[evolution] reflection LLM unavailable, skipped: %s", exc)
        return None

    parsed = parse_reflection_output(text)
    if parsed is None:
        logger.info("[evolution] reflection output unparseable, skipped")
        return None

    # 复用触发源的任务签名特征作为经验适用域（签名 = intent|skill|工具指纹，
    # applies_to 存 intent 细分类型，检索时按它优先匹配）
    applies_to = parsed["applies_to"] or str(
        (trajectory.get("intent") or {}).get("intent_category") or ""
    )
    try:
        exp_id = await storage.insert_experience(
            insight=parsed["insight"],
            tags=parsed["tags"],
            applies_to=applies_to,
            source_session=str(trajectory.get("session_id") or ""),
            attribution=parsed["attribution"],
            db_path=db_path,
        )
    except Exception as exc:
        logger.warning("[evolution] experience insert failed: %s", exc)
        return None

    experience = {
        "id": exp_id,
        "insight": parsed["insight"],
        "tags": parsed["tags"],
        "attribution": parsed["attribution"],
        "applies_to": applies_to,
        "task_signature": str(trajectory.get("task_signature") or ""),
    }
    emit_evolution_event(
        EVT_EVOLUTION_INSIGHT_CREATED,
        {
            "kind": "evolution_insight_created",
            "experience_id": exp_id,
            "insight": parsed["insight"],
            "attribution": parsed["attribution"],
            "task_signature": str(trajectory.get("task_signature") or ""),
        },
    )
    logger.info("[evolution] insight created id=%s attribution=%s", exp_id, parsed["attribution"])
    return experience
