# services/agent/tests/test_doc_review_storage.py
import json

import pytest
from agent.doc_review.models import ParsedDocument, generate_id
from agent.doc_review.storage import get_default_storage


@pytest.fixture(autouse=True)
def _reset_storage():
    from agent.doc_review.storage import reset_default_storage

    reset_default_storage()
    yield
    reset_default_storage()


async def _parsed() -> ParsedDocument:
    return ParsedDocument(
        doc_id=generate_id(),
        file_name="a.txt",
        file_path="C:/a.txt",
        format="txt",
        page_count=1,
        pages=[
            {"page_no": 1, "blocks": [{"block_id": "p1b1", "text": "hi", "start": 0, "end": 2}]}
        ],
        full_text="hi",
    )


async def test_document_crud():
    storage = get_default_storage()
    doc = await _parsed()
    await storage.insert_document(doc)
    got = await storage.get_document(doc.doc_id)
    assert got is not None
    assert got["file_name"] == "a.txt"
    rows = await storage.list_documents()
    assert len(rows) == 1
    assert await storage.delete_document(doc.doc_id) is True
    assert await storage.get_document(doc.doc_id) is None


async def test_run_and_findings_flow():
    storage = get_default_storage()
    doc = await _parsed()
    await storage.insert_document(doc)
    run_id = generate_id()
    await storage.insert_run(run_id=run_id, doc_id=doc.doc_id, status="queued")
    await storage.update_run(
        run_id,
        status="done",
        doc_category="contract",
        risk_types=["legal"],
        overall_risk_level="high",
        summary="s",
        model_provider="local",
        model_name="qwen2.5:14b",
    )
    latest = await storage.latest_run(doc.doc_id)
    assert latest["status"] == "done"
    finding = {
        "finding_id": generate_id(),
        "risk_type": "legal",
        "risk_level": "high",
        "title": "t",
        "description": "",
        "suggestion": "",
        "rule_ref": None,
        "evidence_text": "hi",
        "positions_json": json.dumps([{"page_no": 1, "block_id": "p1b1", "start": 0, "end": 2}]),
    }
    await storage.insert_findings(run_id, doc.doc_id, [finding])
    entries = await storage.list_findings(doc.doc_id)
    assert len(entries) == 1
    assert entries[0]["title"] == "t"
    await storage.delete_document(doc.doc_id)
    assert await storage.list_findings(doc.doc_id) == []
    assert await storage.latest_run(doc.doc_id) is None


async def test_run_failure_error_persisted():
    storage = get_default_storage()
    doc = await _parsed()
    await storage.insert_document(doc)
    run_id = generate_id()
    await storage.insert_run(run_id=run_id, doc_id=doc.doc_id, status="queued")
    await storage.update_run(run_id, status="failed", error="boom")
    assert (await storage.latest_run(doc.doc_id))["error"] == "boom"
