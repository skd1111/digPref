# services/agent/tests/test_doc_review_models.py
import pytest
from pydantic import ValidationError

from agent.doc_review.models import (
    AnalysisResult,
    Block,
    ClassificationResult,
    DocCategory,
    Finding,
    ParsedDocument,
    RiskLevel,
    RiskType,
)


def test_parsed_document_offsets():
    doc = ParsedDocument(
        doc_id="d1",
        file_name="a.pdf",
        file_path="C:/a.pdf",
        format="pdf",
        page_count=1,
        pages=[{"page_no": 1, "blocks": [{"block_id": "p1b1", "text": "hi", "start": 0, "end": 2}]}],
        full_text="hi",
    )
    assert doc.pages[0].blocks[0].text == "hi"
    assert doc.pages[0].page_no == 1


def test_risk_types_enum():
    assert RiskType.DATA_SECURITY.value == "data_security"
    assert RiskLevel.CRITICAL.value == "critical"


def test_classification_requires_valid_category():
    with pytest.raises(ValidationError):
        ClassificationResult(doc_category="bad", risk_types=[])


def test_finding_position_validation():
    f = Finding(finding_id="f1", risk_type="legal", risk_level="high", title="t",
                positions=[{"page_no": 1, "block_id": "p1b1", "start": 0, "end": 3}])
    assert f.positions[0].end == 3


def test_analysis_result_roundtrip():
    r = AnalysisResult(doc_id="d1", doc_category=DocCategory.CONTRACT, risk_types=["legal"],
                       overall_risk_level="high", findings=[])
    assert r.model_dump()["doc_category"] == "contract"
