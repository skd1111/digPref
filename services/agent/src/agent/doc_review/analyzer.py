"""第二阶段：按风险类型加载提示词分析。"""

from __future__ import annotations

from pathlib import Path

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

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_ANALYZE_TEMPLATES = {
    risk_type: (_PROMPTS_DIR / f"analyze_{risk_type.value}.yaml").read_text(encoding="utf-8")
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


async def analyze_document(
    *,
    parsed: ParsedDocument,
    classification: ClassificationResult,
    rules: list[PolicyRule],
    chunk_max_chars: int,
    chunk_overlap: int,
    llm: LLMFunc | None = None,
) -> list[Finding]:
    caller = llm or build_default_llm()
    findings: list[Finding] = []
    seen: set[tuple[str, str, str]] = set()
    for risk_type in classification.risk_types:
        template = _ANALYZE_TEMPLATES[risk_type]
        relevant = [r for r in rules if r.risk_type == risk_type]
        rules_text = (
            "\n".join(f"- [{r.rule_id}] {r.content}" for r in relevant) or "（无，模型自主判断）"
        )
        for chunk in _chunks(parsed.full_text, chunk_max_chars, chunk_overlap):
            prompt = (
                template.replace("{{doc_category}}", classification.doc_category.value)
                .replace("{{rules}}", rules_text)
                .replace("{{chunk_text}}", chunk)
            )
            raw = await caller("doc_analyze", prompt)
            data = _extract_json(raw)
            items = data.get("findings", [])
            if not isinstance(items, list):
                raise ValueError("findings 必须是数组")
            for item in items:
                finding = Finding(
                    finding_id=generate_id(),
                    risk_type=RiskType(item["risk_type"]),
                    risk_level=RiskLevel(item["risk_level"]),
                    title=str(item.get("title", ""))[:200],
                    description=str(item.get("description", "")),
                    suggestion=str(item.get("suggestion", "")),
                    rule_ref=item.get("rule_ref"),
                    evidence_text=str(item.get("evidence_text", "")),
                )
                key = (
                    finding.risk_type.value,
                    finding.risk_level.value,
                    _norm_key(finding.evidence_text),
                )
                if key in seen:
                    continue
                seen.add(key)
                findings.append(finding)
    for finding in findings:
        if finding.evidence_text:
            finding.positions = locate_positions(parsed, finding.evidence_text)
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
