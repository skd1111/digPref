"""Context-trimming utilities — keep LLM prompts under the model's
context budget by summarising long tool outputs (logs, query results)
before injection.
"""
from __future__ import annotations

import re
from typing import Iterable

_TOKEN_RE = re.compile(r"\S+")


def approx_tokens(s: str) -> int:
    """Rough token estimate (~ 4 chars / token)."""
    return max(1, len(s) // 4)


def trim_lines(lines: Iterable[str], max_tokens: int) -> tuple[list[str], int]:
    """Greedy trim from the top, keeping the tail (usually most diagnostic)."""
    kept: list[str] = []
    used = 0
    for line in lines:
        t = approx_tokens(line)
        if used + t > max_tokens:
            return kept, used
        kept.append(line)
        used += t
    return kept, used


def extract_window(text: str, *, around: str, before: int = 500, after: int = 2000) -> str:
    """For long logs — find `around` (e.g. an error signature) and return
    a window of `before` + match + `after` chars.
    """
    idx = text.find(around)
    if idx < 0:
        return text[-before - after:]
    return text[max(0, idx - before): idx + len(around) + after]