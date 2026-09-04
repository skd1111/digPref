# services/agent/tests/test_doc_review_api.py
import json

import pytest
from agent.doc_review import doc_review_api_router
from agent.doc_review.storage import reset_default_storage
from httpx import ASGITransport, AsyncClient


@pytest.fixture(autouse=True)
def _reset():
    reset_default_storage()
    yield
    reset_default_storage()


@pytest.fixture
async def client():
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(doc_review_api_router)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_register_and_get(tmp_path, client):
    p = tmp_path / "合同.txt"
    p.write_text("违约金过高。", encoding="utf-8")
    resp = await client.post("/doc-review/documents", json={"file_path": str(p)})
    assert resp.status_code == 200
    doc_id = resp.json()["doc_id"]

    resp = await client.get(f"/doc-review/documents/{doc_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["file_name"] == "合同.txt"
    assert body["pages"][0]["blocks"][0]["text"] == "违约金过高。"
    assert body["status"] == "none"


async def test_register_unsupported_format(tmp_path, client):
    p = tmp_path / "a.xls"
    p.write_bytes(b"x")
    resp = await client.post("/doc-review/documents", json={"file_path": str(p)})
    assert resp.status_code == 400


async def test_analyze_flow_with_mock_llm(tmp_path, client, monkeypatch):
    p = tmp_path / "合同.txt"
    p.write_text("若乙方违约，违约金为合同金额 100%。", encoding="utf-8")
    doc_id = (await client.post("/doc-review/documents", json={"file_path": str(p)})).json()[
        "doc_id"
    ]

    async def fake_generate_review(self, **kwargs):
        kind = kwargs["kind"]
        if kind == "doc_classify":
            return json.dumps({"doc_category": "contract", "risk_types": ["legal"], "reason": "r"})
        return json.dumps(
            {
                "findings": [
                    {
                        "risk_type": "legal",
                        "risk_level": "high",
                        "title": "违约金过高",
                        "description": "d",
                        "suggestion": "s",
                        "evidence_text": "违约金为合同金额 100%。",
                    },
                ]
            }
        )

    monkeypatch.setattr("agent.llm.router.LMRouter.generate_review", fake_generate_review)
    resp = await client.post(f"/doc-review/documents/{doc_id}/analyze")
    assert resp.status_code == 200
    run_id = resp.json()["run_id"]

    import asyncio

    body = {}
    for _ in range(200):
        status = await client.get(f"/doc-review/documents/{doc_id}/status")
        body = status.json()
        if body["status"] in ("done", "failed"):
            break
        await asyncio.sleep(0.05)
    assert body["status"] == "done"

    findings = await client.get(f"/doc-review/documents/{doc_id}/findings?run_id={run_id}")
    assert findings.status_code == 200
    assert len(findings.json()["findings"]) == 1
    assert findings.json()["findings"][0]["positions"]


async def test_delete_cascades(tmp_path, client):
    p = tmp_path / "a.txt"
    p.write_text("x", encoding="utf-8")
    doc_id = (await client.post("/doc-review/documents", json={"file_path": str(p)})).json()[
        "doc_id"
    ]
    assert (await client.delete(f"/doc-review/documents/{doc_id}")).status_code == 200
    assert (await client.get(f"/doc-review/documents/{doc_id}")).status_code == 404


async def test_kb_refs_come_from_uploaded_rag(monkeypatch):
    """依据改走上传 RAG：_attach_kb_refs 用混合检索命中构建 kb_refs（含可预览 file_path）。"""
    from typing import ClassVar

    from agent.doc_review import api as dr_api

    class _FakeChunk:
        doc_id = "kb1"
        content = "父块原文"
        metadata: ClassVar[dict] = {
            "child_content": "子块：本公司享有最终解释权属违规",
            "heading_path": "第一章 > 格式条款",
            "matched": ["解释权"],
        }

    class _FakeResult:
        chunk = _FakeChunk()
        doc_title = "合规制度.pdf"
        citation = "合规制度.pdf"

    class _FakeCtx:
        results: ClassVar[list] = [_FakeResult()]

    class _FakeRetriever:
        async def retrieve(self, query, top_k=3):
            return _FakeCtx()

    class _FakeKbStorage:
        def resolve_file_path(self, doc_id):
            return "/data/knowledge/files/kb1_合规制度.pdf"

    monkeypatch.setattr("agent.knowledge.retriever.get_default_retriever", lambda: _FakeRetriever())
    monkeypatch.setattr("agent.knowledge.storage.get_default_storage", lambda: _FakeKbStorage())
    monkeypatch.setattr(dr_api.settings, "rag_enabled", True)

    findings = [
        {"title": "单方最终解释权", "description": "d", "evidence_text": "本公司享有最终解释权"}
    ]
    out = await dr_api._attach_kb_refs(findings)
    assert out[0]["kb_refs"]
    ref = out[0]["kb_refs"][0]
    assert ref["source"] == "合规制度.pdf"
    assert ref["file_path"] == "/data/knowledge/files/kb1_合规制度.pdf"
    assert ref["heading"] == "第一章 > 格式条款"
    assert "解释权" in ref["matched_terms"]


async def test_kb_refs_empty_when_rag_disabled(monkeypatch):
    """未启用 RAG → kb_refs 为空（不调检索）。"""
    from agent.doc_review import api as dr_api

    monkeypatch.setattr(dr_api.settings, "rag_enabled", False)
    out = await dr_api._attach_kb_refs([{"title": "t", "description": "", "evidence_text": ""}])
    assert out[0]["kb_refs"] == []
