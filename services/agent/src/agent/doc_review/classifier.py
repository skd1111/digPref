"""第一阶段：模型分类。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent.doc_review.llm import LLMFunc, build_default_llm
from agent.doc_review.models import ClassificationResult

_CLASSIFY_TEMPLATE = (Path(__file__).parent / "prompts" / "classify.yaml").read_text(
    encoding="utf-8"
)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no json object in model output")
    data: dict[str, Any] = json.loads(text[start : end + 1])
    return data


async def classify_document(
    *,
    file_name: str,
    sample_text: str,
    max_chars: int,
    llm: LLMFunc | None = None,
) -> ClassificationResult:
    caller = llm or build_default_llm()
    prompt = (
        _CLASSIFY_TEMPLATE.replace("{{file_name}}", file_name)
        .replace("{{max_chars}}", str(max_chars))
        .replace("{{sample_text}}", sample_text[:max_chars])
    )
    last_err: Exception | None = None
    for _attempt in range(2):
        try:
            raw = await caller("doc_classify", prompt)
            return ClassificationResult.model_validate(_extract_json(raw))
        except Exception as exc:
            last_err = exc
            prompt += "\n\n（上次输出无法解析，请只输出符合 schema 的 JSON。）"
    raise ValueError(f"分类输出解析失败: {last_err}")
