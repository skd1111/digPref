"""Skill 路由 + system_prompt 拼装。

V0 模式：纯关键词匹配。V1 升级 LLM 意图分类（Ollama）+ 关键词回退。
关键词匹配带归一化与中英混排兜底（2026-08-26）：「做一个…的ppt」
不含连续子串「做ppt」也能命中 office_pptx_designer。
"""

from __future__ import annotations

import logging
import re

from agent.skills.intent_classifier import classify_with_llm
from agent.skills.loader import SkillLoader
from agent.skills.models import Skill, SkillRoutingResult

logger = logging.getLogger(__name__)

# 归一化：去空白/常见标点，让宽松表述命中连排关键词（“做 个 PPT” → “做个ppt”）
_NORMALIZE_RE = re.compile(r"[\s\.\,!\?~，。！？、；：·…\-_/\\\"'()\[\]{}]+")
# 核心 token：中英混排关键词中的字母数字串（做ppt → ppt；生成excel → excel）
_CORE_TOKEN_RE = re.compile(r"[a-z0-9]{2,}")


def _normalize(text: str) -> str:
    """去空白/标点后小写化（关键词与输入双侧同规则）。"""
    return _NORMALIZE_RE.sub("", text.lower())


def _core_tokens(keyword: str) -> list[str]:
    """提取关键词中的英文/数字核心 token（长度 ≥2，去重保序）。"""
    seen: set[str] = set()
    out: list[str] = []
    for t in _CORE_TOKEN_RE.findall(keyword.lower()):
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


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

        # LLM 层闸门（2026-08-26 修复）：此前用「是否存在 role=utility/reasoning
        # 的 skill」当哨兵（语义错误，几乎永远 False）→ LLM 意图分类从未执行。
        # 改为真实探测端侧 Ollama 存活；不可用则静默跳过两层直走关键词回退。
        backend_alive = await self._local_backend_alive()

        # 2. 端侧 utility 模型（轻量、强制 local）做意图理解
        if backend_alive:
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

        # 3. 推理模型（reasoning role）做最终决断（同一存活探测结果）
        if backend_alive:
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

    async def _local_backend_alive(self) -> bool:
        """LLM 意图分类兜底门：真实探测端侧 Ollama 端点存活（1s 内快速失败）。

        修复（2026-08-26）：此前 _pick_backend_by_role 以「是否存在某 role 的
        skill」当哨兵，与后端可用性无关 → LLM 分类层从不执行；端点不通时若直接
        调 classify 会每轮多耗 5s 超时，故先轻量探活再进层。
        """
        base = (self._ollama_base_url or "").strip().rstrip("/")
        if not base:
            return False
        try:
            import httpx

            async with httpx.AsyncClient(timeout=1.0) as client:
                r = await client.get(f"{base}/api/tags")
                return r.status_code == 200
        except Exception:
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

        命中规则（2026-08-26 增强）：
          1. 归一化（去空白/标点小写）后子串命中；
          2. 中英混排关键词的英文核心 token 出现在原文即兜底命中（如「做一个…的ppt」
             不含连续「做ppt」，但含 token ppt → 命中）。
        0 个匹配 → 无 skill（confidence=0）。
        并列时取 id 字典序最小（MINOR #5 修复）。
        """
        prompt_lower = user_prompt.lower()
        prompt_norm = _normalize(user_prompt)
        scores: dict[str, int] = {}
        matched: dict[str, list[str]] = {}
        for skill in self._loader.list():
            if not skill.enabled:
                continue
            hits: list[str] = []
            for kw in skill.trigger_keywords:
                if not kw:
                    continue
                if _normalize(kw) in prompt_norm:
                    hits.append(kw)
                    continue
                tokens = _core_tokens(kw)
                if tokens and all(t in prompt_lower for t in tokens):
                    hits.append(kw)
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
