"""第二阶段：按风险类型加载提示词分析。"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

import yaml

from agent.config import settings
from agent.doc_review.classifier import _extract_json
from agent.doc_review.llm import LLMFunc, build_default_llm
from agent.doc_review.matcher import locate_positions
from agent.doc_review.models import (
    RISK_ORDER,
    AnalysisResult,
    ClassificationResult,
    Finding,
    ParsedDocument,
    RiskLevel,
    RiskType,
    generate_id,
)
from agent.doc_review.rules import PolicyRule
from agent.llm.json_discipline import JSON_DISCIPLINE

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
# 按 yaml 结构解析 system/user 两段，避免把 "system: |" 等键名当正文发给模型
_ANALYZE_TEMPLATES = {
    risk_type: yaml.safe_load(
        (_PROMPTS_DIR / f"analyze_{risk_type.value}.yaml").read_text(encoding="utf-8")
    )
    for risk_type in RiskType
}


def _chunks(text: str, max_chars: int, overlap: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = end - overlap
    return chunks


def _norm_key(text: str) -> str:
    return " ".join(text.split())[:120]


_ANALYZE_SCHEMA = (
    '{"findings": [{"risk_type": "...", "title": "...", '
    '"risk_level": "low|medium|high|critical", "description": "...", '
    '"suggestion": "...", "evidence_text": "逐字引用原文"}]}'
)


def _parse_finding(item: dict) -> Finding | None:
    """单条 finding 解析；字段缺失/枚举非法时返 None（跳过该条，不拖垮整篇）。"""
    try:
        return Finding(
            finding_id=generate_id(),
            risk_type=RiskType(item["risk_type"]),
            risk_level=RiskLevel(item["risk_level"]),
            title=str(item.get("title", ""))[:200],
            description=str(item.get("description", "")),
            suggestion=str(item.get("suggestion", "")),
            rule_ref=item.get("rule_ref"),
            evidence_text=str(item.get("evidence_text", "")),
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("doc_review finding item invalid, skipped: %s (%s)", item, exc)
        return None


async def analyze_document(
    *,
    parsed: ParsedDocument,
    classification: ClassificationResult,
    rules: list[PolicyRule],
    chunk_max_chars: int,
    chunk_overlap: int,
    llm: LLMFunc | None = None,
    on_progress: Callable[[float], Awaitable[None] | None] | None = None,
    concurrency: int | None = None,
) -> list[Finding]:
    """并发分析：风险维度 × 分块 的每个单元是一次 LLM 调用，用信号量限流并发执行。

    耗时从 N×单次调用 降到 ≈(N/并发度)×单次调用；单元完成顺序不确定，
    但 gather 保留提交顺序，去重仍按 risk→chunk 原序进行，结果确定性不变。
    """
    caller = llm or build_default_llm()
    findings: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()
    chunks = _chunks(parsed.full_text, chunk_max_chars, chunk_overlap)
    total_units = max(len(classification.risk_types) * len(chunks), 1)
    done_units = 0
    failed_units = 0
    last_unit_error: Exception | None = None
    # 耗时统计：模型调用总时长 vs 其余（解析/去重等），定位慢在哪
    llm_seconds = 0.0
    llm_calls = 0
    max_concurrency = max(1, concurrency or settings.doc_review_analyze_concurrency)
    sem = asyncio.Semaphore(max_concurrency)
    logger.info(
        "doc_review analyze start chunks=%d risk_types=%d total_units=%d concurrency=%d",
        len(chunks),
        len(classification.risk_types),
        total_units,
        max_concurrency,
    )

    async def _emit() -> None:
        if on_progress is None:
            return
        result = on_progress(done_units / total_units)
        if result is not None:
            await result

    # 预拼每个风险维度的规则文本（并发单元共享）
    rules_text_by_risk = {
        rt: (
            "\n".join(f"- [{r.rule_id}] {r.content}" for r in rules if r.risk_type == rt)
            or "（无，模型自主判断）"
        )
        for rt in classification.risk_types
    }
    discipline = JSON_DISCIPLINE.replace("{{schema}}", _ANALYZE_SCHEMA)

    async def _run_unit(risk_type: RiskType, chunk_idx: int, chunk: str) -> list[Finding]:
        """单个 (风险维度 × 分块) 单元：限流内调用模型（容错重试），返解析出的 finding 列表。"""
        nonlocal done_units, failed_units, llm_seconds, llm_calls, last_unit_error
        template = _ANALYZE_TEMPLATES[risk_type]
        system_part = (
            template["system"]
            .replace("{{doc_category}}", classification.doc_category.value)
            .replace("{{rules}}", rules_text_by_risk[risk_type])
        )
        user_part = template["user"].replace("{{chunk_text}}", chunk)
        base_prompt = f"{system_part}\n\n{discipline}\n\n{user_part}"
        data: dict | None = None
        unit_error: Exception | None = None
        prompt = base_prompt
        async with sem:
            # 单元级容错：解析失败重试一次，仍失败则跳过该单元继续其余分析，
            # 避免几十次调用中任一次输出破损导致整篇文档分析崩溃
            for _attempt in range(2):
                try:
                    t0 = time.perf_counter()
                    raw = await caller("doc_analyze", prompt)
                    unit_llm_s = time.perf_counter() - t0
                    llm_seconds += unit_llm_s
                    llm_calls += 1
                    data = _extract_json(raw)
                    logger.info(
                        "doc_review analyze unit ok risk=%s chunk=%d/%d llm=%.1fs",
                        risk_type.value,
                        chunk_idx + 1,
                        len(chunks),
                        unit_llm_s,
                    )
                    break
                except Exception as exc:
                    unit_error = exc
                    last_unit_error = exc
                    prompt = base_prompt + "\n\n（上次输出不是合法 JSON，请严格按约束重新输出。）"
        unit_findings: list[Finding] = []
        if data is None:
            failed_units += 1
            logger.warning(
                "doc_review analyze unit failed risk=%s chunk=%d/%d: %s",
                risk_type.value,
                chunk_idx + 1,
                len(chunks),
                unit_error,
            )
        else:
            items = data.get("findings", [])
            if not isinstance(items, list):
                items = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                finding = _parse_finding(item)
                if finding is not None:
                    unit_findings.append(finding)
        done_units += 1
        await _emit()
        return unit_findings

    # 提交顺序 = risk_types × chunks（与原串行顺序一致），gather 保留该顺序
    tasks = [
        _run_unit(risk_type, chunk_idx, chunk)
        for risk_type in classification.risk_types
        for chunk_idx, chunk in enumerate(chunks)
    ]
    unit_results = await asyncio.gather(*tasks)
    # 按原序去重，保证结果确定性
    for unit_findings in unit_results:
        for finding in unit_findings:
            key = (
                finding.risk_type.value,
                finding.risk_level.value,
                _norm_key(finding.evidence_text),
            )
            if key in seen:
                continue
            seen.add(key)
            findings.append(finding)
    # 全部单元都失败才视为整体失败（带最后一次错误原因）；否则部分成功也交付
    if failed_units == total_units:
        raise ValueError(f"分析失败：所有分块的模型输出均无法解析（{last_unit_error}）")
    t0 = time.perf_counter()
    for finding in findings:
        if finding.evidence_text:
            finding.positions = locate_positions(parsed, finding.evidence_text)
    logger.info(
        "doc_review analyze summary units=%d failed=%d findings=%d "
        "llm_calls=%d llm_total=%.1fs avg=%.1fs locate_positions=%.1fs",
        total_units,
        failed_units,
        len(findings),
        llm_calls,
        llm_seconds,
        (llm_seconds / llm_calls) if llm_calls else 0.0,
        time.perf_counter() - t0,
    )
    return findings


def build_analysis_result(
    *,
    doc_id: str,
    classification: ClassificationResult,
    findings: list[Finding],
    model_name: str,
    created_at: str,
) -> AnalysisResult:
    overall = max(
        (f.risk_level for f in findings),
        key=lambda level: RISK_ORDER[level.value],
        default=RiskLevel.LOW,
    )
    return AnalysisResult(
        doc_id=doc_id,
        doc_category=classification.doc_category,
        risk_types=classification.risk_types,
        overall_risk_level=overall,
        findings=findings,
        model={"provider": "local", "model_name": model_name},
        created_at=created_at,
    )
