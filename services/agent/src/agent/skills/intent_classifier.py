"""Skill 意图分类（intent_classifier.py）。

V1 实现：LLM 意图分类（Ollama 本地）+ 关键词回退。
- 优先调 Ollama /api/chat，prompt 含所有 enabled skill 的 name + trigger_keywords
- Ollama 返回结构化 JSON `{skill_id, confidence, reasoning}`（V1 期望；V0 接受纯文本回退）
- Ollama 不可用 / 超时 / 返回非 JSON → 静默回退关键词路由
- 严格遵守 CLAUDE.md §2：skill_router 任务在 _LOCAL_ONLY_TASKS → 仅 Ollama
"""

from __future__ import annotations

import logging

import httpx

from agent.llm.json_discipline import extract_json
from agent.llm.prompts import load_prompt, render_prompt
from agent.skills.models import Skill

logger = logging.getLogger(__name__)


class IntentResult:
    """LLM 分类结果（V0 简化，不强 schema）。"""

    def __init__(self, skill_id: str | None, confidence: float = 0.0, reasoning: str = ""):
        self.skill_id = skill_id
        self.confidence = confidence
        self.reasoning = reasoning

    def to_routing_dict(self) -> dict:
        return {
            "skill_id": self.skill_id,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
        }


async def classify_with_llm(
    user_prompt: str,
    skills: list[Skill],
    ollama_base_url: str = "http://127.0.0.1:11434",
    ollama_model: str = "qwen2.5:0.5b",
    timeout_s: float = 5.0,
) -> IntentResult | None:
    """调 Ollama 分类。失败返回 None（调用方回退关键词）。

    V0 简化：不做严格 JSON schema 解析（V1 期望 LLM 输出
    {"skill_id": "xxx", "confidence": 0.85, "reasoning": "..."}）。
    V0 用简单 regex 提取 skill_id；其他情况返回 None。
    """
    if not skills:
        return None

    # 构造 prompt：列出所有 enabled skills
    enabled = [s for s in skills if s.enabled]
    if not enabled:
        return None
    skill_lines = "\n".join(
        f"- {s.id} ({s.name}): {s.description} | keywords: {', '.join(s.trigger_keywords[:5])}"
        for s in enabled
    )
    system_prompt = render_prompt(
        load_prompt("skills/classify"),
        SKILL_LINES=skill_lines,
    )
    user_msg = f"用户输入: {user_prompt[:500]}"

    payload = {
        "model": ollama_model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 200},
    }

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(f"{ollama_base_url}/api/chat", json=payload)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.info("llm_intent_classify_failed err=%s", e)
        return None

    # 提取 assistant content
    content = (data.get("message") or {}).get("content", "")
    if not content:
        return None

    # 共享容错解析：围栏/think/前后缀（spec §4.5 第三层）
    parsed = extract_json(content, want="object")
    if isinstance(parsed, dict):
        sid = parsed.get("skill_id")
        if sid and any(s.id == sid for s in enabled):
            return IntentResult(
                skill_id=sid,
                confidence=float(parsed.get("confidence", 0.5)),
                reasoning=str(parsed.get("reasoning") or ""),
            )

    # 回退：content 直接出现 skill_id 字符串
    for s in enabled:
        if s.id in content:
            return IntentResult(skill_id=s.id, confidence=0.5, reasoning="text_match")

    return IntentResult(skill_id=None, confidence=0.0, reasoning="no_match")
