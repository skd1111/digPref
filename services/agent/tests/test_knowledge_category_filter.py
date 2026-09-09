# services/agent/tests/test_knowledge_category_filter.py
"""知识库「按分类检索」P1：未分类=通用 + 命中不足放宽兜底 + 文档审核 risk_type→分类映射。

覆盖：
- storage._filter_and：category 传列表 → IN(...)，未分类('')视为通用始终纳入；标量向后兼容；
- retriever.retrieve：filter_relax_below —— 带分类命中不足阈值时去掉分类再检索补齐（预过滤+放宽）；
- doc_review._attach_kb_refs：finding.risk_type 映射到 KB 分类（financial→finance），
  传 filter={category:[映射类,'']} + 放宽阈值；开关关闭 / 无 risk_type 时不传 filter（行为不变）。
"""

from __future__ import annotations

import math
from typing import ClassVar

import pytest
from agent.config import settings
from agent.knowledge.chunker import chunk_text
from agent.knowledge.hybrid_rag import HybridRetriever
from agent.knowledge.retriever import RAGRetriever
from agent.knowledge.storage import get_default_storage, reset_default_storage


class FakeEmb:
    """4 维玩具向量：按关键词映射到不同轴（确定性，无真实 ONNX）。"""

    model = "fake-emb"

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        if "差旅" in text or "报销" in text or "违约" in text:
            v[0] = 1.0
        if "招待" in text or "合同" in text:
            v[1] = 1.0
        if "数据" in text or "安全" in text:
            v[2] = 1.0
        if not any(v):
            v[3] = 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    async def embed(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        return [self._vec(t) for t in texts]


@pytest.fixture(autouse=True)
def _reset_storage_singleton():
    reset_default_storage()
    yield
    reset_default_storage()


@pytest.fixture
def kb(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "rag_kb_dir", str(tmp_path / "knowledge"))
    monkeypatch.setattr(settings, "rag_rerank_enabled", False)
    reset_default_storage()
    return get_default_storage()


def _seed(kb, doc_id: str, *, category: str, text: str) -> None:
    """落一个生效文档（含向量），category 可为 ''（未分类=通用）。"""
    kb.insert_doc(
        doc_id=doc_id,
        title=doc_id,
        file_name=f"{doc_id}.md",
        source_type="md",
        category=category,
        status="ready",
    )
    res = chunk_text(text, chunk_size=64, parent_size=200)
    pmap = kb.upsert_parents(doc_id, res.parents)
    recs = [
        type(
            "C",
            (),
            {
                "id": f"{doc_id}#{c.ord}",
                "doc_id": doc_id,
                "content": c.text,
                "index_text": c.index_text(),
                "seq": c.ord,
                "parent_seq": pmap.get(c.parent_ord),
                "heading_path": c.heading_path,
                "page_no": c.page_no,
                "category": category,
                "source_type": "md",
                "token_count": c.token_count,
                "metadata": {},
                "embedding": FakeEmb()._vec(c.text),
            },
        )()
        for c in res.children
    ]
    kb.upsert_chunks(doc_id, recs)
    kb.set_doc_status(doc_id, "ready", chunk_count=len(recs))


def _ids(hits: list[dict]) -> set[str]:
    return {h["chunk"].doc_id for h in hits}


# ---- storage：category 列表过滤（未分类=通用）------------------------------


class TestCategoryFilterStorage:
    def test_category_list_includes_uncategorized_excludes_other(self, kb):
        _seed(kb, "dlegal", category="legal", text="违约金上限约定过高。")
        _seed(kb, "duncat", category="", text="违约金责任条款规定。")  # 未分类=通用
        _seed(kb, "dfin", category="finance", text="违约金财务处理方式。")
        # category IN ('legal','') → 命中 legal + 未分类，排除 finance
        hits = kb.search_by_fts("违约金", limit=10, filter={"category": ["legal", ""]})
        ids = _ids(hits)
        assert "dlegal" in ids and "duncat" in ids
        assert "dfin" not in ids

    def test_category_scalar_backward_compatible(self, kb):
        _seed(kb, "dlegal", category="legal", text="违约金上限约定过高。")
        _seed(kb, "duncat", category="", text="违约金责任条款规定。")
        # 标量 category 仍是精确等值（不含未分类），与既有行为一致
        hits = kb.search_by_fts("违约金", limit=10, filter={"category": "legal"})
        assert _ids(hits) == {"dlegal"}

    def test_category_empty_string_means_no_filter(self, kb):
        _seed(kb, "dlegal", category="legal", text="违约金上限约定过高。")
        # category='' 视为「不过滤」（与既有语义一致），全库命中
        hits = kb.search_by_fts("违约金", limit=10, filter={"category": ""})
        assert _ids(hits) == {"dlegal"}


# ---- retriever：命中不足放宽兜底 -------------------------------------------


class TestCategoryRelaxFallback:
    async def test_relax_backfills_when_category_insufficient(self, kb):
        # 库内只有 finance；按 legal+未分类 过滤命中 0 → 放宽到全库补齐
        _seed(kb, "dfin", category="finance", text="差旅费报销标准为每日三百元。")
        retr = RAGRetriever(kb, FakeEmb(), hybrid=HybridRetriever(kb, FakeEmb(), None))
        ctx = await retr.retrieve(
            "差旅费报销", top_k=3, filter={"category": ["legal", ""]}, filter_relax_below=3
        )
        assert ctx.results  # 放宽后命中 finance 文档
        # 不设放宽 → 严格过滤命中空
        ctx2 = await retr.retrieve("差旅费报销", top_k=3, filter={"category": ["legal", ""]})
        assert ctx2.results == []

    async def test_no_relax_when_category_has_enough(self, kb):
        # 同类命中已达阈值 → 不放宽，严格只返回同类（+未分类）
        _seed(kb, "dlegal", category="legal", text="违约金上限约定过高需审慎。")
        _seed(kb, "dfin", category="finance", text="违约金财务处理方式说明。")
        retr = RAGRetriever(kb, FakeEmb(), hybrid=HybridRetriever(kb, FakeEmb(), None))
        ctx = await retr.retrieve(
            "违约金", top_k=1, filter={"category": ["legal", ""]}, filter_relax_below=1
        )
        assert {r.chunk.doc_id for r in ctx.results} == {"dlegal"}


# ---- doc_review：risk_type → KB 分类映射 -----------------------------------


class TestDocReviewCategoryMapping:
    def test_mapping_table(self):
        from agent.doc_review.api import _RISK_TYPE_TO_KB_CATEGORY

        assert _RISK_TYPE_TO_KB_CATEGORY == {
            "financial": "finance",
            "legal": "legal",
            "compliance": "compliance",
            "data_security": "data_security",
        }

    async def _run_attach(self, monkeypatch, *, enabled: bool, finding: dict) -> dict:
        from agent.doc_review import api as dr_api

        captured: dict = {}

        class _FakeChunk:
            doc_id = "kb1"
            content = "x"
            metadata: ClassVar[dict] = {"child_content": "c", "heading_path": "h", "matched": []}

        class _FakeResult:
            chunk = _FakeChunk()
            doc_title = "t.pdf"
            citation = "t.pdf"

        class _FakeCtx:
            results: ClassVar[list] = [_FakeResult()]

        class _FakeRetriever:
            async def retrieve(
                self, query, top_k=3, use_rerank=None, filter=None, filter_relax_below=None
            ):
                captured["filter"] = filter
                captured["relax"] = filter_relax_below
                return _FakeCtx()

        class _FakeKbStorage:
            def resolve_file_path(self, doc_id):
                return ""

        monkeypatch.setattr(
            "agent.knowledge.retriever.get_default_retriever", lambda: _FakeRetriever()
        )
        monkeypatch.setattr("agent.knowledge.storage.get_default_storage", lambda: _FakeKbStorage())
        monkeypatch.setattr(dr_api.settings, "rag_enabled", True)
        monkeypatch.setattr(dr_api.settings, "doc_review_kb_category_filter_enabled", enabled)
        await dr_api._attach_kb_refs([finding])
        return captured

    async def test_risk_type_maps_to_category_filter(self, monkeypatch):
        captured = await self._run_attach(
            monkeypatch,
            enabled=True,
            finding={
                "title": "违约金过高",
                "description": "d",
                "evidence_text": "e",
                "risk_type": "financial",
            },
        )
        # financial → finance，并把未分类('')一并纳入；带放宽阈值
        assert captured["filter"] == {"category": ["finance", ""]}
        assert captured["relax"] and captured["relax"] > 0

    async def test_no_filter_when_disabled(self, monkeypatch):
        captured = await self._run_attach(
            monkeypatch,
            enabled=False,
            finding={"title": "t", "description": "d", "evidence_text": "e", "risk_type": "legal"},
        )
        assert captured["filter"] is None and captured["relax"] is None

    async def test_no_filter_when_risk_type_missing(self, monkeypatch):
        captured = await self._run_attach(
            monkeypatch,
            enabled=True,
            finding={"title": "t", "description": "d", "evidence_text": "e"},
        )
        assert captured["filter"] is None and captured["relax"] is None
