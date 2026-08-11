"""专家团推荐器 —— 业务 → 专家团的自动选择。

决策链（任一级失败都静默降级，绝不抛出）：
  1. preset：预设的 required_expert_team_ids 直接命中（来源：功能点 expert_team_ids，
     历史数据兼容 Skill.required_expert_team_ids）
  2. llm：把功能点名 + 全部启用专家团的描述发给模型判断（三级降级：
     本地 Ollama → router.db 启用内网后端 → 云端后端），仿 biznav/api.py::_make_llm_client
     走 extract_chat 原始对话
  3. keyword：trigger_keywords 子串命中数最高者
  4. none：返回空，提示手动选择
"""

from __future__ import annotations

import logging

from agent.expert_teams.models import ExpertTeam
from agent.llm.json_discipline import extract_json

logger = logging.getLogger(__name__)


def _result(team_ids: list[str], confidence: float, reasoning: str, source: str) -> dict:
    return {
        "team_ids": team_ids,
        "confidence": confidence,
        "reasoning": reasoning,
        "source": source,
    }


async def _llm_recommend(teams: list[ExpertTeam], query: str) -> tuple[str, float, str] | None:
    """LLM 三级降级推荐。返回 (team_id, confidence, reasoning) 或 None。"""
    from agent.llm.router import LMRouter

    team_lines = "\n".join(
        f"- {t.id} ({t.name}): {t.description[:120] or '无描述'}"
        f" | 适用场景 {', '.join(t.applicable_scenarios) or '未标注'}"
        f" | 关键词 {', '.join(t.trigger_keywords[:8])}"
        for t in teams
    )
    system_prompt = (
        "你是专家团路由器。根据业务信息从候选专家团中选择最匹配的一个。\n"
        f"候选专家团：\n{team_lines}\n\n"
        '只返回 JSON：{"team_id": "...", "confidence": 0.0-1.0, "reasoning": "一句话理由"}\n'
        "如果没有匹配的专家团，team_id 返回空字符串。不要输出 JSON 以外的内容。"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query[:1000]},
    ]

    router = LMRouter()
    if router._mock_mode:
        text = str(await router.mock.extract_chat(messages) or "")
        return _parse_llm_text(text, teams)

    valid_ids = {t.id for t in teams}

    # 1/3 本地 Ollama（本地优先：可用时绝不外发）
    try:
        text = str(await router.ollama.extract_chat(messages) or "")
        if text.strip():
            hit = _parse_llm_text(text, teams)
            if hit and hit[0] in valid_ids:
                return hit
    except Exception as e:
        logger.warning("[expert_teams] local ollama unavailable (%s); fallback to private", e)

    # 2/3 内网模型（router.db 已启用 private 后端）
    try:
        private = await router._build_private_client()
        if private is not None:
            try:
                text = str(await private.extract_chat(messages) or "")
                if text.strip():
                    hit = _parse_llm_text(text, teams)
                    if hit and hit[0] in valid_ids:
                        return hit
            except Exception as e:
                logger.warning("[expert_teams] private unavailable (%s); fallback to cloud", e)
    except Exception as e:
        logger.warning("[expert_teams] private lookup failed (%s); fallback to cloud", e)

    # 3/3 云端模型（router.db 已启用 cloud 后端）
    try:
        cloud = await router._build_cloud_client()
        if cloud is not None:
            text = str(await cloud.extract_chat(messages) or "")
            if text.strip():
                hit = _parse_llm_text(text, teams)
                if hit and hit[0] in valid_ids:
                    return hit
    except Exception as e:
        logger.warning("[expert_teams] cloud unavailable (%s); all backends exhausted", e)

    return None


def _parse_llm_text(text: str, teams: list[ExpertTeam]) -> tuple[str, float, str] | None:
    """解析 LLM 返回的 JSON；team_id 不在候选列表 → None。"""
    data = extract_json(text)
    if not isinstance(data, dict):
        return None
    team_id = str(data.get("team_id", "") or "").strip()
    if not team_id or team_id not in {t.id for t in teams}:
        return None
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    return team_id, max(0.0, min(1.0, confidence)), str(data.get("reasoning", ""))


def _keyword_match(teams: list[ExpertTeam], query: str) -> tuple[str, list[str]] | None:
    """trigger_keywords 子串命中：返回命中数最高的 (team_id, matched)。"""
    best: tuple[str, list[str]] | None = None
    for t in teams:
        matched = [kw for kw in t.trigger_keywords if kw and kw in query]
        if matched and (best is None or len(matched) > len(best[1])):
            best = (t.id, matched)
    return best


async def recommend_team(
    teams: list[ExpertTeam],
    *,
    preset_ids: list[str],
    feature_name: str,
    feature_description: str = "",
    materials: list[str] | None = None,
    deliverables: list[str] | None = None,
) -> dict:
    """推荐业务适用的专家团。任何异常都不得抛出（前端绝不因推荐卡住）。

    返回 {team_ids, confidence, reasoning, source}，
    source ∈ {'preset', 'llm', 'keyword', 'none'}。
    """
    try:
        enabled = {t.id: t for t in teams if t.enabled}

        # 1. 预设优先（功能点 expert_team_ids，历史数据兼容 Skill 预设；零 LLM 开销）
        preset_hit = [tid for tid in preset_ids if tid in enabled]
        if preset_hit:
            return _result(preset_hit, 1.0, "功能点预设的默认专家团", "preset")

        if not enabled:
            return _result([], 0.0, "没有启用的专家团，请到设置页维护", "none")

        query_parts = [feature_name, feature_description]
        if materials:
            query_parts.append("办理材料：" + "、".join(materials))
        if deliverables:
            query_parts.append("交付物：" + "、".join(deliverables))
        query = "\n".join(p for p in query_parts if p)

        # 2. LLM 三级降级（失败静默落到关键词回退，不断链）
        try:
            hit = await _llm_recommend(list(enabled.values()), query)
        except Exception:
            logger.warning("[expert_teams] llm recommend failed; fallback to keyword")
            hit = None
        if hit:
            team_id, confidence, reasoning = hit
            return _result([team_id], confidence, reasoning or "LLM 分析推荐", "llm")

        # 3. 关键词回退
        kw = _keyword_match(list(enabled.values()), query)
        if kw:
            team_id, matched = kw
            return _result([team_id], 0.4, f"关键词命中：{'、'.join(matched)}", "keyword")

        # 4. 无匹配
        return _result([], 0.0, "无匹配专家团，请在输入栏手动选择", "none")
    except Exception:
        logger.exception("[expert_teams] recommend_team crashed")
        return _result([], 0.0, "推荐过程异常，请手动选择", "none")
