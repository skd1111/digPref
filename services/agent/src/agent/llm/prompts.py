"""Loads prompt templates from agent/llm/prompts/*.md at runtime.

Why files instead of constants? Prompts need iteration; keeping them as
.md files means non-engineers (PMs, domain experts) can edit them via PR.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=16)
def load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt not found: {path}")
    return path.read_text(encoding="utf-8")