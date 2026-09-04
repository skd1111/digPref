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


async def test_kb_refs_parallel_preserves_mapping_and_bounds_concurrency(monkeypatch):
    """并发化后：每条 finding 拿到「自己 query」对应的依据（不错位），且并发受信号量限流。

    回归 #189：读取详情 GET 曾串行做 N 次 RAG（16 条 finding≈32s）超过前端 15s 超时，
    被误报成「分析失败」。改并发后总耗时压到接近单次检索；本用例锁死「并发但映射不错位 + 限流」。
    """
    import asyncio

    from agent.doc_review import api as dr_api

    state = {"in_flight": 0, "peak": 0}

    class _FakeChunk:
        def __init__(self, tag):
            self.doc_id = "kb1"
            self.content = f"content-{tag}"
            self.metadata = {"heading_path": f"h-{tag}", "matched": [tag], "child_content": tag}

    class _FakeResult:
        def __init__(self, tag):
            self.chunk = _FakeChunk(tag)
            self.doc_title = f"{tag}.pdf"
            self.citation = f"{tag}.pdf"

    class _FakeCtx:
        def __init__(self, tag):
            self.results = [_FakeResult(tag)]

    class _FakeRetriever:
        async def retrieve(self, query, top_k=3):
            # 制造重叠窗口：串行实现下 peak 恒为 1，并发下会升到信号量上限
            state["in_flight"] += 1
            state["peak"] = max(state["peak"], state["in_flight"])
            try:
                await asyncio.sleep(0.02)
                return _FakeCtx(query)  # 用 query 原样回显，验证 finding↔依据不错位
            finally:
                state["in_flight"] -= 1

    class _FakeKbStorage:
        def resolve_file_path(self, doc_id):
            return f"/files/{doc_id}.pdf"

    monkeypatch.setattr("agent.knowledge.retriever.get_default_retriever", lambda: _FakeRetriever())
    monkeypatch.setattr("agent.knowledge.storage.get_default_storage", lambda: _FakeKbStorage())
    monkeypatch.setattr(dr_api.settings, "rag_enabled", True)
    monkeypatch.setattr(dr_api.settings, "doc_review_kb_refs_concurrency", 4)

    findings = [{"title": f"t{i}", "description": "", "evidence_text": ""} for i in range(12)]
    out = await dr_api._attach_kb_refs(findings)

    # 映射不错位：第 i 条 finding 的依据来自它自己的 query（title=t{i}）
    for i, item in enumerate(out):
        assert item["kb_refs"][0]["source"] == f"t{i}.pdf"
        assert item["kb_refs"][0]["heading"] == f"h-t{i}"

    # 确实并发（peak>1）且受信号量限流（peak<=4）
    assert state["peak"] > 1
    assert state["peak"] <= 4
