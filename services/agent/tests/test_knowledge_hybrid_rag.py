# services/agent/tests/test_knowledge_hybrid_rag.py
"""本地知识库混合检索（FTS5 BM25 + sqlite-vec 向量 + RRF + rerank）单元/集成测试。

覆盖：tokenizer / chunker / storage（FTS5+向量+父子+级联删除+漂移）/ hybrid_rag
（RRF 融合、降级、rerank、元数据过滤）/ retriever（编号引用）/ ingestion（复制入库+reindex）
/ rag_config（重启生效语义）/ api 端点。
"""

from __future__ import annotations

import math

import pytest
from agent.config import settings
from agent.knowledge import rag_config
from agent.knowledge import tokenizer as tk
from agent.knowledge.chunker import chunk_text
from agent.knowledge.hybrid_rag import HybridRetriever
from agent.knowledge.ingestion import KnowledgeIngestion
from agent.knowledge.storage import KnowledgeStorage

# ---- 测试替身 ---------------------------------------------------------------


class FakeEmb:
    """4 维玩具向量：按关键词映射到不同轴（确定性，无真实 ONNX）。"""

    model = "fake-emb"

    def __init__(self, dim: int = 4, zero: bool = False) -> None:
        self.dim = dim
        self.zero = zero

    def _vec(self, text: str) -> list[float]:
        if self.zero:
            return [0.0] * self.dim
        v = [0.0] * self.dim
        if "差旅" in text or "报销" in text:
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


class FakeRerank:
    """把含 prefer 关键词的文档排到最前（验证 rerank 改变最终顺序）。"""

    def __init__(self, prefer: str) -> None:
        self.prefer = prefer

    async def rerank(self, query: str, docs: list[str]) -> list[float]:
        return [10.0 if self.prefer in d else 0.0 for d in docs]

    def model_present(self) -> bool:
        return True


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """把知识库数据根钉到 tmp（迁移语义 + 测试隔离）。"""
    monkeypatch.setattr(settings, "rag_kb_dir", str(tmp_path / "knowledge"))
    monkeypatch.setattr(settings, "rag_rerank_enabled", False)
    return KnowledgeStorage(rag_config.kb_db_path())


# ---- tokenizer -------------------------------------------------------------


class TestTokenizer:
    def test_tokenize_nonempty(self):
        assert tk.tokenize("违约金上限") != []

    def test_match_query_or_and_quoted(self):
        mq = tk.build_match_query("差旅报销标准")
        assert " OR " in mq
        assert '"' in mq

    def test_match_query_empty_for_punct(self):
        assert tk.build_match_query("，。！") == ""


# ---- chunker ---------------------------------------------------------------


class TestChunker:
    def test_heading_sections_and_parent_child(self):
        text = (
            "# 制度\n## 第三章 差旅报销\n差旅费报销标准为每日三百元。\n"
            "## 第四章 招待费\n招待费报销上限为合同金额的百分之十。"
        )
        res = chunk_text(text, chunk_size=64, overlap=0.1, parent_size=200)
        assert len(res.parents) >= 2
        assert len(res.children) >= 2
        # 层级标题路径被保留
        assert any("差旅报销" in p.heading_path for p in res.parents)
        # 子块 index_text 拼接标题上下文前缀
        child = next(c for c in res.children if "差旅" in c.text)
        assert child.index_text(contextual_prefix=True).startswith(child.heading_path)
        assert child.index_text(contextual_prefix=False) == child.text

    def test_page_index_mapping(self):
        # 两段各占一页；page_index 按字符偏移映射页码
        text = "第一页内容甲乙丙丁。\n第二页内容戊己庚辛。"
        page_index = [(0, 1), (len("第一页内容甲乙丙丁。\n"), 2)]
        res = chunk_text(text, chunk_size=8, overlap=0.0, parent_size=8, page_index=page_index)
        pages = {c.page_no for c in res.children}
        assert 2 in pages or 1 in pages

    def test_empty_text(self):
        res = chunk_text("   ")
        assert res.parents == [] and res.children == []


# ---- storage ---------------------------------------------------------------


class TestStorage:
    def _seed(self, kb: KnowledgeStorage) -> None:
        kb.insert_doc(doc_id="d1", title="报销制度", file_name="b.md", category="finance")
        res = chunk_text(
            "# 制度\n## 差旅\n差旅费报销标准每日三百元。\n## 招待\n招待费上限为合同金额百分之十。",
            chunk_size=64,
            parent_size=200,
        )
        pmap = kb.upsert_parents("d1", res.parents)
        recs = []
        for c in res.children:
            recs.append(
                type(
                    "C",
                    (),
                    {
                        "id": f"c{c.ord}",
                        "doc_id": "d1",
                        "content": c.text,
                        "index_text": c.index_text(),
                        "seq": c.ord,
                        "parent_seq": pmap.get(c.parent_ord),
                        "heading_path": c.heading_path,
                        "page_no": c.page_no,
                        "category": "finance",
                        "source_type": "md",
                        "token_count": c.token_count,
                        "metadata": {},
                        "embedding": FakeEmb()._vec(c.text),
                    },
                )()
            )
        kb.upsert_chunks("d1", recs)
        kb.set_doc_status("d1", "ready", chunk_count=len(recs))
        kb.set_meta("fake-emb", 4)

    def test_fts_search_ranks_relevant_first(self, kb):
        self._seed(kb)
        hits = kb.search_by_fts("差旅费报销标准", limit=5)
        assert hits
        assert "差旅" in hits[0]["chunk"].content

    def test_vector_search(self, kb):
        self._seed(kb)
        hits = kb.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=2)
        assert hits and hits[0]["similarity"] > 0

    def test_filter_category(self, kb):
        self._seed(kb)
        assert kb.search_by_fts("差旅", filter={"category": "legal"}) == []
        assert kb.search_by_fts("差旅", filter={"category": "finance"}) != []

    def test_soft_delete_hides_hard_delete_cascades(self, kb):
        self._seed(kb)
        assert kb.has_chunks()
        kb.soft_delete_doc("d1")
        assert kb.search_by_fts("差旅报销", limit=5) == []
        assert not kb.has_chunks()
        assert kb.hard_delete_doc("d1")
        assert kb.get_stats().total_chunks == 0

    def test_needs_reindex_on_model_drift(self, kb):
        self._seed(kb)
        assert not kb.needs_reindex("fake-emb", 4)
        assert kb.needs_reindex("other-model", 4)
        assert kb.needs_reindex("fake-emb", 8)


# ---- hybrid_rag ------------------------------------------------------------


class TestHybrid:
    def _seed(self, kb):
        kb.insert_doc(doc_id="d1", title="报销制度", file_name="b.md", category="finance")
        res = chunk_text(
            "# 制度\n## 差旅\n差旅费报销标准每日三百元。\n## 招待\n招待费上限为合同金额百分之十。",
            chunk_size=64,
            parent_size=200,
        )
        pmap = kb.upsert_parents("d1", res.parents)
        recs = [
            type(
                "C",
                (),
                {
                    "id": f"c{c.ord}",
                    "doc_id": "d1",
                    "content": c.text,
                    "index_text": c.index_text(),
                    "seq": c.ord,
                    "parent_seq": pmap.get(c.parent_ord),
                    "heading_path": c.heading_path,
                    "page_no": c.page_no,
                    "category": "finance",
                    "source_type": "md",
                    "token_count": c.token_count,
                    "metadata": {},
                    "embedding": FakeEmb()._vec(c.text),
                },
            )()
            for c in res.children
        ]
        kb.upsert_chunks("d1", recs)
        kb.set_doc_status("d1", "ready", chunk_count=len(recs))

    async def test_hybrid_returns_hits_with_source(self, kb):
        self._seed(kb)
        r = HybridRetriever(storage=kb, embedding=FakeEmb(), reranker=None)
        hits = await r.search("差旅费报销标准是多少", top_k=3)
        assert hits
        assert hits[0].source  # 溯源串非空
        assert hits[0].parent_content  # small-to-big 回喂父块

    async def test_degrade_to_bm25_when_embedding_zero(self, kb):
        self._seed(kb)
        r = HybridRetriever(storage=kb, embedding=FakeEmb(zero=True), reranker=None)
        hits = await r.search("差旅报销", top_k=3)
        assert hits  # 向量全零 → 纯 BM25 仍召回

    async def test_rerank_changes_order(self, kb):
        self._seed(kb)
        r = HybridRetriever(storage=kb, embedding=FakeEmb(), reranker=FakeRerank(prefer="招待"))
        settings.rag_rerank_enabled = True
        try:
            hits = await r.search("报销", top_k=3)
            assert hits and "招待" in hits[0].parent_content + hits[0].content
        finally:
            settings.rag_rerank_enabled = False

    async def test_empty_kb_short_circuits(self, kb):
        r = HybridRetriever(storage=kb, embedding=FakeEmb(), reranker=None)
        assert await r.search("任意查询") == []

    async def test_metadata_filter(self, kb):
        self._seed(kb)
        r = HybridRetriever(storage=kb, embedding=FakeEmb(), reranker=None)
        assert await r.search("差旅报销", filter={"category": "legal"}) == []


# ---- retriever -------------------------------------------------------------


class TestRetriever:
    async def test_retrieve_builds_numbered_citation_prompt(self, kb):
        from agent.knowledge.retriever import RAGRetriever

        kb.insert_doc(doc_id="d1", title="报销制度", file_name="b.md", category="finance")
        res = chunk_text(
            "# 制度\n## 差旅\n差旅费报销标准每日三百元。", chunk_size=64, parent_size=200
        )
        pmap = kb.upsert_parents("d1", res.parents)
        recs = [
            type(
                "C",
                (),
                {
                    "id": f"c{c.ord}",
                    "doc_id": "d1",
                    "content": c.text,
                    "index_text": c.index_text(),
                    "seq": c.ord,
                    "parent_seq": pmap.get(c.parent_ord),
                    "heading_path": c.heading_path,
                    "page_no": c.page_no,
                    "category": "finance",
                    "source_type": "md",
                    "token_count": c.token_count,
                    "metadata": {},
                    "embedding": FakeEmb()._vec(c.text),
                },
            )()
            for c in res.children
        ]
        kb.upsert_chunks("d1", recs)
        kb.set_doc_status("d1", "ready", chunk_count=len(recs))

        retr = RAGRetriever(kb, FakeEmb(), hybrid=HybridRetriever(kb, FakeEmb(), None))
        ctx = await retr.retrieve("差旅费报销标准", top_k=3)
        assert ctx.backend == "hybrid"
        assert ctx.results
        assert "[1]" in ctx.formatted_prompt
        assert "禁止编造" in ctx.formatted_prompt


# ---- ingestion -------------------------------------------------------------


class TestIngestion:
    async def test_ingest_file_copies_chunks_and_indexes(self, kb, tmp_path):
        src = tmp_path / "baoxiao.md"
        src.write_text(
            "# 报销制度\n## 第三章 差旅报销\n差旅费报销标准为每日三百元，超过需审批。\n"
            "## 第四章 招待费\n招待费报销上限为合同金额的百分之十。\n",
            encoding="utf-8",
        )
        ing = KnowledgeIngestion(kb, FakeEmb())
        doc = await ing.ingest_file(str(src), category="finance")
        assert doc["status"] == "ready"
        assert doc["chunk_count"] >= 2
        # 源文件已复制入库（迁移自包含）
        assert (rag_config.kb_files_dir() / doc["source_relpath"]).is_file()
        # 入库后可混合检索
        r = HybridRetriever(storage=kb, embedding=FakeEmb(), reranker=None)
        hits = await r.search("差旅费报销标准", top_k=3)
        assert hits

    async def test_ingest_unsupported_raises(self, kb, tmp_path):
        bad = tmp_path / "x.xyz"
        bad.write_text("data", encoding="utf-8")
        ing = KnowledgeIngestion(kb, FakeEmb())
        from agent.knowledge.ingestion import IngestionError

        with pytest.raises(IngestionError):
            await ing.ingest_file(str(bad))

    async def test_reindex_rebuilds_vectors(self, kb, tmp_path):
        src = tmp_path / "a.md"
        src.write_text("# t\n## s\n差旅费报销标准每日三百元。\n", encoding="utf-8")
        ing = KnowledgeIngestion(kb, FakeEmb())
        await ing.ingest_file(str(src))
        kb.set_meta("old-model", 4)  # 模拟漂移
        assert kb.needs_reindex("fake-emb", 4)
        out = await ing.reindex()
        assert out["ok"] and out["done"] >= 1
        assert not kb.needs_reindex("fake-emb", 4)  # meta 已刷新

    async def test_portable_copy_db_and_files_survives(self, kb, tmp_path):
        """换机复制即用（#3）：ingest 后把整个 knowledge/ 目录拷到新路径，
        用新库直接向量检索命中（向量已持久化在 kb.db，无需重新 embedding），
        源文件与参数也随目录迁移。"""
        import shutil

        src = tmp_path / "baoxiao.md"
        src.write_text(
            "# 报销制度\n## 第三章 差旅报销\n差旅费报销标准为每日三百元。\n",
            encoding="utf-8",
        )
        ing = KnowledgeIngestion(kb, FakeEmb())
        doc = await ing.ingest_file(str(src), category="finance")
        assert doc["status"] == "ready"
        rag_config.save_rag_config({"rag_top_k": 9})  # 参数也落库，应随目录迁移

        # 复制整个数据根到“新机器”路径
        new_root = tmp_path / "moved" / "knowledge"
        shutil.copytree(rag_config.kb_dir(), new_root)

        # 用新库路径开一个全新 storage（模拟换机后首次打开）
        moved = KnowledgeStorage(str(new_root / "kb.db"))
        # 源文件随目录复制过去，仍可按相对路径解析到
        assert (new_root / "files" / doc["source_relpath"]).is_file()
        # 向量已持久化：不重新 embedding，直接用新库做向量检索命中
        vhits = moved.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=3)
        assert vhits and vhits[0]["similarity"] > 0
        # 混合检索也命中
        r = HybridRetriever(storage=moved, embedding=FakeEmb(), reranker=None)
        assert await r.search("差旅费报销标准", top_k=3)
        # 参数随 kb.db 迁移（kb_config 表在同一库）
        assert moved.get_all_config().get("rag_top_k") == "9"


# ---- rag_config ------------------------------------------------------------


class TestRagConfig:
    def test_save_hot_applies_and_persists_to_kbdb(self, kb, monkeypatch):
        """查询期参数保存即热应用（无需重启）+ 落 kb.db 持久化。"""
        monkeypatch.setattr(settings, "rag_top_k", 5)
        res = rag_config.save_rag_config({"rag_top_k": 99})  # 超上限 → clamp 20
        assert res["ok"] and res["restart_required"] is False
        assert settings.rag_top_k == 20  # 已热应用（clamp 到上限）
        assert res["hot_applied"] == ["rag_top_k"] and res["needs_reindex"] == []
        # 落库持久化：从 kb.db 读回应为 clamp 后的 "20"
        assert kb.get_all_config().get("rag_top_k") == "20"

    def test_save_flags_index_time_needs_reindex(self, kb):
        """索引期参数（分块大小）保存后标 needs_reindex，查询期参数标 hot_applied。"""
        res = rag_config.save_rag_config({"rag_chunk_size": 800, "rag_top_k": 6})
        assert res["restart_required"] is False
        assert "rag_chunk_size" in res["needs_reindex"]
        assert "rag_top_k" in res["hot_applied"]
        assert settings.rag_chunk_size == 800 and settings.rag_top_k == 6

    def test_load_applies_and_clamps(self, kb, monkeypatch):
        rag_config.save_rag_config({"rag_top_k": 99, "rag_chunk_size": 10})
        monkeypatch.setattr(settings, "rag_top_k", 5)
        monkeypatch.setattr(settings, "rag_chunk_size", 512)
        rag_config.load_rag_config()
        assert settings.rag_top_k == 20  # clamp 到上限
        assert settings.rag_chunk_size == 128  # clamp 到下限

    def test_paths_under_kb_dir(self, kb):
        assert rag_config.kb_db_path().endswith("kb.db")
        assert rag_config.kb_files_dir().name == "files"
        assert rag_config.rag_config_path().name == "rag_config.json"

    def test_load_migrates_legacy_json_into_kbdb(self, kb, monkeypatch):
        """库内无配置但旧 rag_config.json 存在 → 一次性导入并落 kb.db（迁移）。"""
        import json

        monkeypatch.setattr(settings, "rag_top_k", 5)
        rag_config.rag_config_path().parent.mkdir(parents=True, exist_ok=True)
        rag_config.rag_config_path().write_text(
            json.dumps({"rag_top_k": 12, "bogus_key": 1}), encoding="utf-8"
        )
        assert kb.get_all_config() == {}  # 库内初始无配置
        rag_config.load_rag_config()
        assert settings.rag_top_k == 12  # 从旧 JSON 迁移并应用
        # 已落 kb.db（白名单外的 bogus_key 被过滤），下次无需 JSON 也能加载
        assert kb.get_all_config().get("rag_top_k") == "12"
        assert "bogus_key" not in kb.get_all_config()

    def test_resolve_stored_path_only_when_file_exists(self, kb, tmp_path):
        """_resolve_stored_path：相对路径→绝对路径，仅文件存在时返回（否则空串）。"""
        from agent.knowledge.api import _resolve_stored_path

        assert _resolve_stored_path(None) == ""
        assert _resolve_stored_path("missing.md") == ""
        rag_config.kb_files_dir().mkdir(parents=True, exist_ok=True)
        (rag_config.kb_files_dir() / "real.md").write_text("x", encoding="utf-8")
        resolved = _resolve_stored_path("real.md")
        assert resolved.endswith("real.md") and resolved == str(
            rag_config.kb_files_dir() / "real.md"
        )


# ---- api 端点 --------------------------------------------------------------


class TestApi:
    @pytest.fixture
    def client(self, kb, monkeypatch):
        import agent.knowledge.retriever as retr_mod
        from agent.knowledge import api as kapi
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        monkeypatch.setattr(retr_mod, "build_default_embedding_client", lambda: FakeEmb())

        # TestClient 每个请求后关闭事件循环，端内 create_task 的后台导入无法跑完；
        # 导入管道已由 TestIngestion 直接 await 覆盖，这里把后台任务换为 no-op，
        # 只验端点接线（预校验/建档/响应形状），避免悬空任务在 teardown 报 event-loop-closed。
        async def _noop_ingest(*_a: object, **_k: object) -> None:
            return None

        monkeypatch.setattr(kapi, "_run_ingest", _noop_ingest)
        kapi.reset_for_testing()
        app = FastAPI()
        app.include_router(kapi.router)
        return TestClient(app)

    def test_config_get_set_hot_applies(self, client):
        r = client.get("/knowledge/v1/config")
        assert r.status_code == 200
        body = r.json()
        assert "config" in body and "index_time" in body
        r2 = client.post("/knowledge/v1/config", json={"config": {"rag_top_k": 7}})
        assert r2.status_code == 200
        j = r2.json()
        assert j["restart_required"] is False
        assert j["hot_applied"] == ["rag_top_k"]

    def test_status_shape(self, client):
        body = client.get("/knowledge/v1/status").json()
        assert "needs_reindex" in body and "reranker_available" in body

    def test_search_empty_kb(self, client):
        r = client.post("/knowledge/v1/search", json={"query": "差旅报销", "top_k": 3})
        assert r.status_code == 200 and r.json()["results"] == []

    def test_upload_registers_doc(self, client, tmp_path):
        src = tmp_path / "u.md"
        src.write_text("# t\n## s\n差旅费报销标准每日三百元。\n", encoding="utf-8")
        r = client.post(
            "/knowledge/v1/docs/upload", json={"file_path": str(src), "category": "finance"}
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "indexing" and body["doc_id"]
        lst = client.get("/knowledge/v1/docs").json()
        assert lst["total"] >= 1
        assert any(d["id"] == body["doc_id"] for d in lst["docs"])

    def test_upload_rejects_bad_ext(self, client, tmp_path):
        bad = tmp_path / "x.xyz"
        bad.write_text("d", encoding="utf-8")
        r = client.post("/knowledge/v1/docs/upload", json={"file_path": str(bad)})
        assert r.status_code == 400


# ---- LLM 增强 seam（默认用已启用模型）------------------------------


class TestLlmSeam:
    async def test_hyde_uses_enabled_model_by_default(self, kb, monkeypatch):
        import agent.knowledge.retriever as retr

        monkeypatch.setattr(settings, "rag_llm_enhance_enabled", True)
        monkeypatch.setattr(settings, "rag_hyde_enabled", True)
        calls: list[str] = []

        async def fake_llm(kind: str, prompt: str) -> str:
            calls.append(kind)
            return "假设性回答"

        monkeypatch.setattr(retr, "build_default_rag_llm", lambda: fake_llm)
        r = HybridRetriever(storage=kb, embedding=FakeEmb(), reranker=None)
        out = await r._maybe_hyde("差旅报销", None)
        assert calls == ["kb_hyde"] and "假设性回答" in out

    async def test_expand_uses_enabled_model_by_default(self, kb, monkeypatch):
        import agent.knowledge.retriever as retr

        monkeypatch.setattr(settings, "rag_llm_enhance_enabled", True)
        monkeypatch.setattr(settings, "rag_query_expansion_enabled", True)

        async def fake_llm(kind: str, prompt: str) -> str:
            return "报销标准\n差旅费用\n费用结算"

        monkeypatch.setattr(retr, "build_default_rag_llm", lambda: fake_llm)
        r = HybridRetriever(storage=kb, embedding=FakeEmb(), reranker=None)
        out = await r._maybe_expand("怎么报销", None)
        assert out[0] == "怎么报销" and len(out) == 4

    async def test_seams_noop_when_disabled(self, kb):
        # 默认关：不注入 llm 也不调用，原样返回
        r = HybridRetriever(storage=kb, embedding=FakeEmb(), reranker=None)
        assert await r._maybe_hyde("q", None) == "q"
        assert await r._maybe_expand("q", None) == ["q"]

    async def test_master_switch_off_hard_gates_llm(self, kb, monkeypatch):
        """总开关关 → 即使子开关全开也绝不调大模型（只用本地混合检索）。"""
        import agent.knowledge.retriever as retr

        monkeypatch.setattr(settings, "rag_llm_enhance_enabled", False)
        monkeypatch.setattr(settings, "rag_hyde_enabled", True)
        monkeypatch.setattr(settings, "rag_query_expansion_enabled", True)
        monkeypatch.setattr(settings, "rag_llm_contextual_enabled", True)
        calls: list[str] = []

        async def fake_llm(kind: str, prompt: str) -> str:
            calls.append(kind)
            return "不应被调用"

        monkeypatch.setattr(retr, "build_default_rag_llm", lambda: fake_llm)
        r = HybridRetriever(storage=kb, embedding=FakeEmb(), reranker=None)
        assert await r._maybe_hyde("差旅报销", None) == "差旅报销"
        assert await r._maybe_expand("怎么报销", None) == ["怎么报销"]
        ing = KnowledgeIngestion(kb, FakeEmb())
        assert await ing._llm_contextual_prefix("差旅费报销标准", None) == ""
        assert calls == []  # 硬闸：零大模型调用

    async def test_contextual_prefix_uses_enabled_model(self, kb, monkeypatch):
        import agent.knowledge.retriever as retr

        monkeypatch.setattr(settings, "rag_llm_enhance_enabled", True)
        monkeypatch.setattr(settings, "rag_llm_contextual_enabled", True)
        calls: list[str] = []

        async def fake_llm(kind: str, prompt: str) -> str:
            calls.append(kind)
            return "本段讲差旅报销标准"

        monkeypatch.setattr(retr, "build_default_rag_llm", lambda: fake_llm)
        ing = KnowledgeIngestion(kb, FakeEmb())
        out = await ing._llm_contextual_prefix("差旅费报销标准每日三百元", None)
        assert calls == ["kb_contextual"] and out == "本段讲差旅报销标准"
