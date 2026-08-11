"""LangSmith exporter (optional)."""

from __future__ import annotations

import os


def enabled() -> bool:
    return bool(os.environ.get("LANGCHAIN_API_KEY"))


def configure() -> None:
    if not enabled():
        return
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT", "eaide-agent")
