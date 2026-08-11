"""第一阶段：模型分类。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import yaml

from agent.doc_review.llm import LLMFunc, build_default_llm
from agent.doc_review.models import ClassificationResult
from agent.llm.json_discipline import JSON_DISCIPLINE, extract_json

logger = logging.getLogger(__name__)

# 按 yaml 结构解析 system/user 两段，避免把 "system: |" 等键名当正文发给模型
_CLASSIFY_PROMPT = yaml.safe_load(
    (Path(__file__).parent / "prompts" / "classify.yaml").read_text(encoding="utf-8")
)


def _extract_json(text: str) -> dict[str, Any]:
    """薄封装：兼容既有测试 import；失败抛 ValueError（沿用原语义）。"""
    data = extract_json(text, want="object")
    if not isinstance(data, dict):
        raise ValueError("no json object in model output")
    return data


async def classify_document(
    *,
    file_name: str,
    sample_text: str,
    max_chars: int,
    llm: LLMFunc | None = None,
) -> ClassificationResult:
    caller = llm or build_default_llm()
    user_part = (
        _CLASSIFY_PROMPT["user"]
        .replace("{{file_name}}", file_name)
        .replace("{{max_chars}}", str(max_chars))
        .replace("{{sample_text}}", sample_text[:max_chars])
    )
    discipline = JSON_DISCIPLINE.replace(
        "{{schema}}",
        '{"doc_category": "contract|internal_policy|announcement|bidding|other", '
        '"risk_types": ["compliance|legal|data_security|financial"], '
        '"reason": "...", "confidence": {}',
    )
    prompt = f"{_CLASSIFY_PROMPT['system']}\n\n{discipline}\n\n{user_part}"
    last_err: Exception | None = None
    for attempt in range(2):
        t0 = time.perf_counter()
        try:
            raw = await caller("doc_classify", prompt)
            llm_s = time.perf_counter() - t0
            result = ClassificationResult.model_validate(_extract_json(raw))
            logger.info(
                "doc_review classify call ok attempt=%d llm=%.1fs sample_chars=%d",
                attempt + 1,
                llm_s,
                min(len(sample_text), max_chars),
            )
            return result
        except Exception as exc:
            last_err = exc
            logger.warning(
                "doc_review classify call failed attempt=%d elapsed=%.1fs: %s",
                attempt + 1,
                time.perf_counter() - t0,
                exc,
            )
            prompt += "\n\n（上次输出无法解析，请严格只输出符合 Schema 的合法 JSON。）"
    raise ValueError(f"分类输出解析失败: {last_err}")
