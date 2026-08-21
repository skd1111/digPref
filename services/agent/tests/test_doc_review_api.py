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


async def test_retrieval_auto_activates_risk_types(tmp_path, client, monkeypatch):
    """类型自动判定：分类器只勾 legal，但文档命中财税规则库 →
    检索自动补入 compliance/financial 维度，无需人工指定文档类型。"""
    from agent.config import settings
    from agent.doc_review.fiscal_rules import _CACHE

    fiscal = tmp_path / "kb" / "fiscal-tax"
    (fiscal / "regulations").mkdir(parents=True)
    (fiscal / "regulations" / "增值税法-摘要.md").write_text(
        "# 增值税法-摘要\n\n## 进项抵扣\n餐饮服务购进的进项税额不得抵扣。\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "doc_review_fiscal_dir", str(fiscal))
    _CACHE.clear()

    p = tmp_path / "报销制度.txt"
    p.write_text("员工用餐饮服务发票抵扣进项税额，财务予以报销。", encoding="utf-8")
    doc_id = (await client.post("/doc-review/documents", json={"file_path": str(p)})).json()[
        "doc_id"
    ]

    async def fake_generate_review(self, **kwargs):
        kind = kwargs["kind"]
        if kind == "doc_classify":
            # 模拟小模型分类器漏勾财税相关维度，只给 legal
            return json.dumps({"doc_category": "internal_policy", "risk_types": ["legal"]})
        return json.dumps({"findings": []})

    monkeypatch.setattr("agent.llm.router.LMRouter.generate_review", fake_generate_review)
    resp = await client.post(f"/doc-review/documents/{doc_id}/analyze")
    assert resp.status_code == 200

    import asyncio

    body = {}
    for _ in range(200):
        body = (await client.get(f"/doc-review/documents/{doc_id}/status")).json()
        if body["status"] in ("done", "failed"):
            break
        await asyncio.sleep(0.05)
    assert body["status"] == "done"

    detail = (await client.get(f"/doc-review/documents/{doc_id}")).json()
    # legal（分类器）+ compliance/financial（检索自动补入）
    assert "legal" in detail["risk_types"]
    assert "compliance" in detail["risk_types"]
    assert "financial" in detail["risk_types"]
    _CACHE.clear()
