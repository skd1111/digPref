"""LMRouter 适配：文档审核任务走可配置链 generate_review（默认云端 → 内网，均需已启用）。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from agent.llm.router import LMRouter
from agent.llm.types import TaskKind

LLMFunc = Callable[[TaskKind, str], Awaitable[str]]


def build_default_llm() -> LLMFunc:
    async def _call(kind: TaskKind, prompt: str) -> str:
        return await LMRouter().generate_review(kind=kind, prompt=prompt)

    return _call
