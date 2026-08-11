"""knowledge.chunker —— Phase 4 V1 知识库分块器（占位恢复版）。

仅保留最小接口定义以满足其他模块 import 依赖。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def estimate_tokens(text: str) -> int:
    return len(text or "") // 4


def chunk_markdown(content: str, **kwargs):
    return []


def chunk_swagger(swagger_json, **kwargs):
    return []


def chunk_conversation(messages, **kwargs):
    return []


def chunk_business_rules(features, **kwargs):
    return []


def chunk_code_symbols(symbols, **kwargs):
    return []


def chunk_by_source(source_type: str, payload, **kwargs):
    return []
