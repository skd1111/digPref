# services/agent/tests/test_doc_review_analysis.py
import asyncio
import json

import pytest
from agent.doc_review.analyzer import analyze_document
from agent.doc_review.classifier import _extract_json, classify_document
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


async def test_classify_flattens_nested_confidence():
    # 本地模型常输出 {"confidence": {"risk_types": {...}}}，需归一化而不报错
    async def fake_llm(kind, prompt):
        return json.dumps(
            {
                "doc_category": "contract",
                "risk_types": ["compliance", "legal"],
                "confidence": {
                    "risk_types": {
                        "compliance": 0.97,
                        "legal": 0.95,
                        "data_security": 0.98,
                        "financial": 0.92,
                    }
                },
            }
        )

    result = await classify_document(
        file_name="合同.txt", sample_text="x", max_chars=100, llm=fake_llm
    )
    assert result.confidence == {
        "compliance": 0.97,
        "legal": 0.95,
        "data_security": 0.98,
        "financial": 0.92,
    }


async def test_classify_keeps_flat_confidence():
    async def fake_llm(kind, prompt):
        return json.dumps(
            {
                "doc_category": "contract",
                "risk_types": ["legal"],
                "confidence": {"legal": 0.9},
            }
        )

    result = await classify_document(
        file_name="合同.txt", sample_text="x", max_chars=100, llm=fake_llm
    )
    assert result.confidence == {"legal": 0.9}


async def test_classify_normalizes_mixed_confidence():
    # 混合形态：顶层数值项保留，嵌套项展开合并
    async def fake_llm(kind, prompt):
        return json.dumps(
            {
                "doc_category": "contract",
                "risk_types": ["legal"],
                "confidence": {
                    "overall": 0.9,
                    "risk_types": {"compliance": 0.97, "financial": 0.92},
                },
            }
        )

    result = await classify_document(
        file_name="合同.txt", sample_text="x", max_chars=100, llm=fake_llm
    )
    assert result.confidence == {"overall": 0.9, "compliance": 0.97, "financial": 0.92}


async def test_classify_normalizes_deep_and_invalid_confidence():
    # 深层嵌套 + 非 dict 兜底：均不报错
    async def fake_llm(kind, prompt):
        return json.dumps(
            {
                "doc_category": "contract",
                "risk_types": ["legal"],
                "confidence": {"detail": {"risk_types": {"legal": 0.8}}},
            }
        )

    result = await classify_document(
        file_name="合同.txt", sample_text="x", max_chars=100, llm=fake_llm
    )
    assert result.confidence == {"legal": 0.8}

    async def fake_llm2(kind, prompt):
        return json.dumps({"doc_category": "other", "risk_types": [], "confidence": "high"})

    result2 = await classify_document(file_name="x", sample_text="y", max_chars=100, llm=fake_llm2)
    assert result2.confidence == {}


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


# ---- 容错解析（_extract_json）----


def test_extract_json_unescaped_quotes_repaired():
    # 字符串值内未转义的双引号（"Expecting ',' delimiter" 最常见根因）
    raw = '{"findings": [{"title": "约定"最终解释权"归甲方", "risk_level": "high"}]}'
    data = _extract_json(raw)
    assert "最终解释权" in data["findings"][0]["title"]


def test_extract_json_trailing_comma_repaired():
    data = _extract_json('{"doc_category": "contract", "risk_types": ["legal"],}')
    assert data["doc_category"] == "contract"


def test_extract_json_truncated_output_balanced():
    # 输出被截断：字符串未闭合 + 括号未闭合
    data = _extract_json('{"findings": [{"title": "单方解除权')
    assert isinstance(data["findings"], list)


def test_extract_json_bare_newline_in_string():
    data = _extract_json('{"description": "第一行\n第二行", "ok": true}')
    assert "\n" in data["description"]


async def test_analyze_skips_broken_unit_and_continues():
    """单块输出破损：重试一次仍失败则跳过，其余块继续，不整篇崩溃。"""
    calls = {"n": 0}

    async def fake_llm(kind, prompt):
        calls["n"] += 1
        # 第 1、2 次（同一块的首次 + 重试）都破损，第 3 次正常
        if calls["n"] <= 2:
            return "完全不是 json"
        return json.dumps(
            {
                "findings": [
                    {
                        "risk_type": "legal",
                        "title": "ok",
                        "risk_level": "medium",
                        "evidence_text": "违约金上限为合同金额的 100%。",
                    }
                ]
            }
        )

    # 2 块 × 1 风险类型：第 1 块失败被跳过，第 2 块产出 finding
    parsed = _parsed()
    parsed.full_text = parsed.full_text * 1200  # 超过单块阈值
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


async def test_analyze_all_units_broken_raises():
    async def fake_llm(kind, prompt):
        return "not json at all"

    classification = ClassificationResult(doc_category="contract", risk_types=["legal"])
    with pytest.raises(ValueError, match="均无法解析"):
        await analyze_document(
            parsed=_parsed(),
            classification=classification,
            rules=[],
            chunk_max_chars=8000,
            chunk_overlap=200,
            llm=fake_llm,
        )


async def test_analyze_respects_concurrency_limit():
    """并发限流：同时在飞的单元数不得超过 concurrency。"""
    active = 0
    max_active = 0

    async def fake_llm(kind, prompt):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return json.dumps({"findings": []})

    classification = ClassificationResult(
        doc_category="contract",
        risk_types=["compliance", "legal", "data_security", "financial"],
    )
    await analyze_document(
        parsed=_parsed(),
        classification=classification,
        rules=[],
        chunk_max_chars=8000,
        chunk_overlap=200,
        llm=fake_llm,
        concurrency=2,
    )
    # 4 个单元、并发度 2：峰值并发应为 2（若串行则为 1）
    assert max_active == 2


async def test_analyze_concurrent_speedup():
    """并发后总耗时应明显小于串行（4 单元× 0.05s：串行≈0.2s，并发 4 ≈0.05s）。"""

    async def fake_llm(kind, prompt):
        await asyncio.sleep(0.05)
        return json.dumps({"findings": []})

    classification = ClassificationResult(
        doc_category="contract",
        risk_types=["compliance", "legal", "data_security", "financial"],
    )
    loop = asyncio.get_event_loop()
    t0 = loop.time()
    await analyze_document(
        parsed=_parsed(),
        classification=classification,
        rules=[],
        chunk_max_chars=8000,
        chunk_overlap=200,
        llm=fake_llm,
        concurrency=4,
    )
    elapsed = loop.time() - t0
    assert elapsed < 0.15, f"expected concurrent speedup, got {elapsed:.3f}s"


async def test_analyze_invalid_finding_item_skipped():
    async def fake_llm(kind, prompt):
        return json.dumps(
            {
                "findings": [
                    {"risk_type": "bogus", "title": "枚举非法应被跳过"},
                    {
                        "risk_type": "legal",
                        "risk_level": "high",
                        "title": "合法条目",
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
    assert findings[0].title == "合法条目"
