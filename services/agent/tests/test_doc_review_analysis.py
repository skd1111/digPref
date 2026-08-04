# services/agent/tests/test_doc_review_analysis.py
import json

import pytest
from agent.doc_review.analyzer import analyze_document
from agent.doc_review.classifier import classify_document
from agent.doc_review.models import (
    ClassificationResult,
    DocCategory,
    Finding,
    ParsedDocument,
    RiskType,
)
from agent.doc_review.rules import PolicyRule


def _parsed() -> ParsedDocument:
    return ParsedDocument(
        doc_id="d1",
        file_name="合同.txt",
        file_path="C:/合同.txt",
        format="txt",
        page_count=1,
        pages=[
            {
                "page_no": 1,
                "blocks": [
                    {
                        "block_id": "p1b1",
                        "text": "违约金上限为合同金额的 100%。",
                        "start": 0,
                        "end": 16,
                    },
                ],
            }
        ],
        full_text="违约金上限为合同金额的 100%。",
    )


async def test_classify_parses_mock_output():
    async def fake_llm(kind, prompt):
        return json.dumps({"doc_category": "contract", "risk_types": ["legal"], "reason": "r"})

    result = await classify_document(
        file_name="合同.txt", sample_text="x", max_chars=100, llm=fake_llm
    )
    assert result.doc_category == DocCategory.CONTRACT
    assert result.risk_types == [RiskType.LEGAL]


async def test_classify_retries_on_bad_json():
    calls = 0

    async def fake_llm(kind, prompt):
        nonlocal calls
        calls += 1
        if calls == 1:
            return "not json"
        return json.dumps({"doc_category": "other", "risk_types": []})

    result = await classify_document(file_name="x", sample_text="y", max_chars=100, llm=fake_llm)
    assert calls == 2
    assert result.doc_category == DocCategory.OTHER


async def test_classify_fails_after_retries():
    async def fake_llm(kind, prompt):
        return "still not json"

    with pytest.raises(ValueError, match="分类输出解析失败"):
        await classify_document(file_name="x", sample_text="y", max_chars=100, llm=fake_llm)


async def test_analyze_merges_and_locates():
    async def fake_llm(kind, prompt):
        return json.dumps(
            {
                "findings": [
                    {
                        "risk_type": "legal",
                        "title": "违约金过高",
                        "risk_level": "high",
                        "description": "d",
                        "suggestion": "s",
                        "evidence_text": "违约金上限为合同金额的 100%。",
                    },
                ]
            }
        )

    classification = ClassificationResult(doc_category="contract", risk_types=["legal"])
    findings = await analyze_document(
        parsed=_parsed(),
        classification=classification,
        rules=[],
        chunk_max_chars=8000,
        chunk_overlap=200,
        llm=fake_llm,
    )
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, Finding)
    assert f.positions and f.positions[0].block_id == "p1b1"


async def test_analyze_dedupes_same_finding():
    payload = {
        "findings": [
            {
                "risk_type": "legal",
                "title": "a",
                "risk_level": "high",
                "evidence_text": "同一句原文。",
            },
            {
                "risk_type": "legal",
                "title": "a",
                "risk_level": "high",
                "evidence_text": "同一句原文。",
            },
        ]
    }

    async def fake_llm(kind, prompt):
        return json.dumps(payload)

    parsed = _parsed()
    classification = ClassificationResult(doc_category="contract", risk_types=["legal"])
    findings = await analyze_document(
        parsed=parsed,
        classification=classification,
        rules=[],
        chunk_max_chars=8000,
        chunk_overlap=200,
        llm=fake_llm,
    )
    assert len(findings) == 1


async def test_rules_injected_into_prompt():
    seen = {}

    async def fake_llm(kind, prompt):
        seen["prompt"] = prompt
        return json.dumps({"findings": []})

    parsed = _parsed()
    classification = ClassificationResult(doc_category="contract", risk_types=["legal"])
    rule = PolicyRule(
        rule_id="R-001", source="制度A", risk_type="legal", content="违约金不得高于 30%"
    )
    await analyze_document(
        parsed=parsed,
        classification=classification,
        rules=[rule],
        chunk_max_chars=8000,
        chunk_overlap=200,
        llm=fake_llm,
    )
    assert "R-001" in seen["prompt"]
    assert "违约金不得高于 30%" in seen["prompt"]
