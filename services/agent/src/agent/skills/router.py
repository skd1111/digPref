"""Skill 路由 + system_prompt 拼装。

V0 模式：纯关键词匹配。V1 升级 LLM 意图分类（Ollama）+ 关键词回退。
"""

from __future__ import annotations

import logging

from agent.skills.intent_classifier import classify_with_llm
from agent.skills.loader import SkillLoader
from agent.skills.models import Skill, SkillRoutingResult

logger = logging.getLogger(__name__)


class SkillRouter:
    def __init__(self, loader: SkillLoader, ollama_base_url: str = "http://127.0.0.1:11434"):
        self._loader = loader
        self._ollama_base_url = ollama_base_url

    def route(self, user_prompt: str) -> SkillRoutingResult:
        """V0 同步关键词路由。V1 用 `route_async` 跑 LLM → 关键词回退。"""
        return self._route_keyword(user_prompt)

    async def route_async(self, user_prompt: str) -> SkillRoutingResult:
        """V1 异步路由：2 层模型协调 + 关键词回退。

        链路（V1 用户决策）：
        1. 关键词快速预筛（V0 fast path，无 LLM 调用）
        2. 端侧 utility 模型（如果配置了 `utility` role 后端）做意图理解
        3. 推理模型（如果配置了 `reasoning` role 后端）做最终决断
        4. 任何一层失败 / 未配置 / 不可用 → 静默降级到下一层
        """
        skills = [s for s in self._loader.list() if s.enabled]
        if not skills:
            return SkillRoutingResult(skill_id=None, confidence=0.0)

        # 1. 关键词快速预筛（无 LLM 调用，V0 fast path）
        keyword_result = self._route_keyword(user_prompt)
        if keyword_result.skill_id and keyword_result.confidence >= 0.67:  # 2 关键词命中才直接走
            return keyword_result

        # 2. 端侧 utility 模型（轻量、强制 local）做意图理解
        if self._pick_backend_by_role("utility"):
            try:
                utility_result = await self._classify_with_one_backend(
                    user_prompt,
                    skills,
                    "utility",
                )
                if utility_result and utility_result.skill_id and utility_result.confidence >= 0.34:
                    return SkillRoutingResult(
                        skill_id=utility_result.skill_id,
                        skill_name=self._loader.get(utility_result.skill_id).name
                        if self._loader.get(utility_result.skill_id)
                        else "",
                        confidence=utility_result.confidence,
                        matched_keywords=[],
                    )
            except Exception as e:
                logger.info("route_async utility_failed err=%s", e)

        # 3. 推理模型（reasoning role）做最终决断
        if self._pick_backend_by_role("reasoning"):
            try:
                reason_result = await self._classify_with_one_backend(
                    user_prompt,
                    skills,
                    "reasoning",
                )
                if reason_result and reason_result.skill_id and reason_result.confidence >= 0.34:
                    return SkillRoutingResult(
                        skill_id=reason_result.skill_id,
                        skill_name=self._loader.get(reason_result.skill_id).name
                        if self._loader.get(reason_result.skill_id)
                        else "",
                        confidence=reason_result.confidence,
                        matched_keywords=[],
                    )
            except Exception as e:
                logger.info("route_async reasoning_failed err=%s", e)

        # 4. 最终回退：关键词（即使低置信度）
        return keyword_result

    def _pick_backend_by_role(self, role: str) -> bool:
        """V1 简化：仅检查是否存在该 role 的 enabled skill（bool 哨兵）。

        V2 会改为查 LLMBackend 注册表，返回真正的后端配置。
        Skill.role 与 LLMBackend.role 同名但语义不同：
        - Skill.role = 该任务需要什么层级的模型
        - LLMBackend.role = 该后端提供什么层级的能力
        """
        for s in self._loader.list():
            if s.enabled and s.role == role:
                return True
        return False

    async def _classify_with_one_backend(self, user_prompt, skills, _role: str):
        """V1 简化：hardcode Ollama 端点，V2 再接 LMRouter 选后端。

        _role 仅用于将来按 role 选不同 Ollama 模型（utility→0.5b / reasoning→7b）。
        """
        return await classify_with_llm(
            user_prompt=user_prompt,
            skills=skills,
            ollama_base_url=self._ollama_base_url,
            ollama_model="qwen2.5:0.5b",
        )

    def _route_keyword(self, user_prompt: str) -> SkillRoutingResult:
        """关键词匹配：找出 keyword 出现次数最多的 skill。

        0 个匹配 → 无 skill（confidence=0）。
        并列时取 id 字典序最小（MINOR #5 修复）。
        """
        prompt_lower = user_prompt.lower()
        scores: dict[str, int] = {}
        matched: dict[str, list[str]] = {}
        for skill in self._loader.list():
            if not skill.enabled:
                continue
            hits = [kw for kw in skill.trigger_keywords if kw.lower() in prompt_lower]
            if hits:
                scores[skill.id] = len(hits)
                matched[skill.id] = hits
        if not scores:
            return SkillRoutingResult(skill_id=None, confidence=0.0)
        # max score first, then min id (ties)
        best_id = min(scores, key=lambda k: (-scores[k], k))
        skill_obj = self._loader.get(best_id)
        return SkillRoutingResult(
            skill_id=best_id,
            skill_name=skill_obj.name if skill_obj else "",
            confidence=min(scores[best_id] / 3.0, 1.0),
            matched_keywords=matched[best_id],
        )

    def build_system_prompt(self, base: str, skill: Skill | None) -> str:
        """V0 简单拼接：base + skill.system_prompt + few-shot 序列化。

        V1 升级：llm.summarise(skill.few_shot_examples) → 摘要化注入。
        """
        if not skill:
            return base
        parts = [base, "", f"## 当前技能：{skill.name}", skill.system_prompt]
        if skill.few_shot_examples:
            parts.append("")
            parts.append("## Few-shot 示例：")
            for ex in skill.few_shot_examples:
                parts.append(f"[{ex.role}] {ex.content}")
        return "\n".join(parts)
