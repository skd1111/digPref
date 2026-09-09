# services/agent/tests/test_knowledge_versions.py
"""知识库文件版本管理（逻辑资料 group → 多版本 + 全局唯一生效版本）单元/集成测试。

覆盖：
- storage：旧库迁移回填、activate_version 切换、三通道检索只命中生效版本、
  版本号/计数、list_docs 每组只返回生效版本、删除单版本自动切换 + 整组删除；
- ingestion：新版本就绪后安全切换（旧版本失活但保留、切回无需重建）、reindex 覆盖全部版本；
- api：base_doc_id 新版本上传归组、versions/activate 端点、delete?all_versions。
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest
from agent.config import settings
from agent.knowledge import rag_config
from agent.knowledge.chunker import chunk_text
from agent.knowledge.ingestion import KnowledgeIngestion
from agent.knowledge.storage import (
    KnowledgeStorage,
    get_default_storage,
    reset_default_storage,
)

# ---- 测试替身 ---------------------------------------------------------------


class FakeEmb:
    """4 维玩具向量：按关键词映射到不同轴（确定性，无真实 ONNX）。"""

    model = "fake-emb"

    def __init__(self, dim: int = 4) -> None:
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
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


@pytest.fixture(autouse=True)
def _reset_storage_singleton():
    """每个用例前后清空默认存储单例，避免跨用例指向不同 tmp 库。"""
    reset_default_storage()
    yield
    reset_default_storage()


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """把知识库数据根钉到 tmp，并让 fixture 实例即默认单例（API 端点与 kb 同库）。"""
    monkeypatch.setattr(settings, "rag_kb_dir", str(tmp_path / "knowledge"))
    monkeypatch.setattr(settings, "rag_rerank_enabled", False)
    reset_default_storage()
    return get_default_storage()


def _seed_version(
    kb: KnowledgeStorage,
    *,
    doc_id: str,
    group_id: str,
    version: int,
    is_active: bool,
    text: str,
    category: str = "finance",
) -> None:
    """直接落一个版本：docs 行 + 父块 + 子块（含向量），跳过文件解析。"""
    kb.insert_doc(
        doc_id=doc_id,
        title=f"v{version}",
        file_name=f"f{version}.md",
        source_type="md",
        category=category,
        group_id=group_id,
        version=version,
        is_active=is_active,
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


def _hit_doc_ids(hits: list[dict]) -> set[str]:
    return {h["chunk"].doc_id for h in hits}


# ---- storage：迁移 / 切换 / 检索过滤 ---------------------------------------


class TestVersionStorage:
    def test_migration_backfills_legacy_docs(self, tmp_path, monkeypatch):
        """旧库（docs 无 group_id/version/is_active）打开即自动补列 + 回填单版本组。"""
        monkeypatch.setattr(settings, "rag_kb_dir", str(tmp_path / "knowledge"))
        db_path = rag_config.kb_db_path()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE docs (doc_id TEXT PRIMARY KEY, title TEXT NOT NULL DEFAULT '',"
            " file_name TEXT NOT NULL DEFAULT '', source_type TEXT NOT NULL DEFAULT 'markdown',"
            " source_relpath TEXT, category TEXT NOT NULL DEFAULT '',"
            " size_bytes INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'pending',"
            " chunk_count INTEGER NOT NULL DEFAULT 0, error TEXT,"
            " metadata_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL,"
            " updated_at INTEGER NOT NULL, deleted_at INTEGER)"
        )
        conn.execute(
            "INSERT INTO docs (doc_id, title, created_at, updated_at)"
            " VALUES ('legacy1', '旧文档', 1, 1)"
        )
        conn.commit()
        conn.close()

        migrated = KnowledgeStorage(db_path)  # 打开触发 _migrate_docs_columns
        doc = migrated.get_doc("legacy1")
        assert doc is not None
        assert doc["group_id"] == "legacy1"  # 回填=自身 doc_id
        assert doc["version"] == 1
        assert doc["is_active"] is True
        # 迁移后旧文档仍是生效版本、可在列表出现
        assert [d["doc_id"] for d in migrated.list_docs()] == ["legacy1"]

    def test_activate_switches_and_vector_retrieval_follows(self, kb):
        _seed_version(
            kb,
            doc_id="g1v1",
            group_id="g1",
            version=1,
            is_active=True,
            text="# 制度\n## 差旅\n差旅费报销标准每日三百元。",
        )
        _seed_version(
            kb,
            doc_id="g1v2",
            group_id="g1",
            version=2,
            is_active=False,
            text="# 制度\n## 差旅\n差旅费报销标准上调为每日五百元。",
        )
        # 初始 v1 生效：向量检索只命中 v1
        assert _hit_doc_ids(kb.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=5)) == {"g1v1"}
        # v2 分块在库但不参与召回
        assert kb.get_chunks_by_doc("g1v2")
        # 切到 v2（指定用另一版本）
        assert kb.activate_version("g1v2") is True
        assert _hit_doc_ids(kb.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=5)) == {"g1v2"}
        assert kb.get_doc("g1v1")["is_active"] is False
        # v1 分块仍在库（切回无需重建索引）
        assert kb.get_chunks_by_doc("g1v1")
        # 切回 v1：向量仍在，O(1) 生效
        assert kb.activate_version("g1v1") is True
        assert _hit_doc_ids(kb.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=5)) == {"g1v1"}

    def test_fts_and_text_channels_respect_active(self, kb):
        _seed_version(
            kb,
            doc_id="g1v1",
            group_id="g1",
            version=1,
            is_active=True,
            text="# 制度\n## 差旅\n差旅费报销标准每日三百元。",
        )
        _seed_version(
            kb,
            doc_id="g1v2",
            group_id="g1",
            version=2,
            is_active=False,
            text="# 制度\n## 差旅\n差旅费报销标准每日五百元新版。",
        )
        assert _hit_doc_ids(kb.search_by_fts("差旅报销", limit=5)) == {"g1v1"}
        assert _hit_doc_ids(kb.search_by_text("报销", limit=5)) == {"g1v1"}
        kb.activate_version("g1v2")
        assert _hit_doc_ids(kb.search_by_fts("差旅报销", limit=5)) == {"g1v2"}
        assert _hit_doc_ids(kb.search_by_text("报销", limit=5)) == {"g1v2"}

    def test_activate_unknown_doc_returns_false(self, kb):
        assert kb.activate_version("nope") is False

    def test_next_version_count_and_group_listing(self, kb):
        _seed_version(kb, doc_id="g1v1", group_id="g1", version=1, is_active=True, text="差旅报销")
        assert kb.next_version("g1") == 2
        assert kb.group_id_of("g1v1") == "g1"
        _seed_version(
            kb, doc_id="g1v2", group_id="g1", version=2, is_active=False, text="差旅报销新版"
        )
        assert kb.next_version("g1") == 3
        assert kb.version_count("g1") == 2
        assert [v["version"] for v in kb.list_group_versions("g1")] == [2, 1]  # 倒序
        # group_id_of 未知文档回退自身
        assert kb.group_id_of("unknown") == "unknown"

    def test_list_docs_returns_active_per_group_with_count(self, kb):
        _seed_version(kb, doc_id="g1v1", group_id="g1", version=1, is_active=True, text="差旅报销")
        _seed_version(
            kb, doc_id="g1v2", group_id="g1", version=2, is_active=False, text="差旅报销新"
        )
        _seed_version(kb, doc_id="g2v1", group_id="g2", version=1, is_active=True, text="数据安全")
        docs = kb.list_docs()
        assert {d["doc_id"] for d in docs} == {"g1v1", "g2v1"}  # 每组只返回生效版本
        g1 = next(d for d in docs if d["doc_id"] == "g1v1")
        assert g1["version_count"] == 2 and g1["version"] == 1 and g1["is_active"] is True
        assert kb.count_docs() == 2  # 逻辑资料数
        assert kb.get_stats().total_docs == 2
        # active_only=False →返回全部版本
        assert len(kb.list_docs(active_only=False)) == 3

    def test_delete_single_active_version_auto_activates_newest(self, kb):
        _seed_version(
            kb, doc_id="g1v1", group_id="g1", version=1, is_active=False, text="差旅报销旧"
        )
        _seed_version(
            kb, doc_id="g1v2", group_id="g1", version=2, is_active=True, text="差旅报销新"
        )
        assert kb.hard_delete_doc("g1v2", delete_file=False) is True
        assert kb.get_doc("g1v2") is None
        assert kb.get_doc("g1v1")["is_active"] is True  # 自动切到剩余最新
        assert _hit_doc_ids(kb.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=5)) == {"g1v1"}

    def test_delete_all_versions_clears_group(self, kb):
        _seed_version(
            kb, doc_id="g1v1", group_id="g1", version=1, is_active=False, text="差旅报销旧"
        )
        _seed_version(
            kb, doc_id="g1v2", group_id="g1", version=2, is_active=True, text="差旅报销新"
        )
        assert kb.hard_delete_doc("g1v1", delete_file=False, all_versions=True) is True
        assert kb.get_doc("g1v1") is None and kb.get_doc("g1v2") is None
        assert kb.version_count("g1") == 0
        assert kb.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=5) == []
        assert kb.has_chunks() is False

    def test_set_category_updates_whole_group_and_chunks(self, kb):
        _seed_version(
            kb,
            doc_id="g1v1",
            group_id="g1",
            version=1,
            is_active=False,
            text="差旅报销旧",
            category="finance",
        )
        _seed_version(
            kb,
            doc_id="g1v2",
            group_id="g1",
            version=2,
            is_active=True,
            text="差旅报销新",
            category="finance",
        )
        assert kb.set_category("g1v2", "legal") is True
        # 整组两个版本的 docs.category 都改
        assert kb.get_doc("g1v1")["category"] == "legal"
        assert kb.get_doc("g1v2")["category"] == "legal"
        # 子块 category 同步（供按类检索）
        chunks = kb.get_chunks_by_doc("g1v1")
        assert chunks and all(c.metadata.get("category") == "legal" for c in chunks)
        # 按类过滤检索命中新分类、不再命中旧分类（仅生效版 g1v2 参与召回）
        assert kb.search_by_fts("差旅报销", limit=5, filter={"category": "legal"})
        assert kb.search_by_fts("差旅报销", limit=5, filter={"category": "finance"}) == []

    def test_set_category_unknown_doc_returns_false(self, kb):
        assert kb.set_category("nope", "legal") is False


# ---- ingestion：新版本就绪安全切换 -----------------------------------------


class TestVersionIngestion:
    async def test_new_version_cutover_on_ready(self, kb, tmp_path):
        ing = KnowledgeIngestion(kb, FakeEmb())
        v1 = tmp_path / "baoxiao_v1.md"
        v1.write_text("# 报销制度\n## 差旅报销\n差旅费报销标准为每日三百元。\n", encoding="utf-8")
        doc1 = await ing.ingest_file(str(v1), category="finance")
        assert doc1["status"] == "ready"
        gid = doc1["group_id"]
        assert gid == doc1["doc_id"] and doc1["version"] == 1 and doc1["is_active"] is True

        # 新版本：is_active=False 建档，就绪后自动切换为生效，旧版本失活但保留
        v2 = tmp_path / "baoxiao_v2.md"
        v2.write_text(
            "# 报销制度\n## 差旅报销\n差旅费报销标准上调为每日五百元。\n", encoding="utf-8"
        )
        doc2 = await ing.ingest_file(
            str(v2),
            category="finance",
            group_id=gid,
            version=kb.next_version(gid),
            is_active=False,
        )
        assert doc2["status"] == "ready" and doc2["version"] == 2 and doc2["is_active"] is True
        assert kb.get_doc(doc1["doc_id"])["is_active"] is False  # 旧版本失活
        assert kb.get_chunks_by_doc(doc1["doc_id"])  # 旧版本分块仍在库
        assert _hit_doc_ids(kb.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=10)) == {
            doc2["doc_id"]
        }
        # 指定用旧版本：切回 v1（无需重建）
        assert kb.activate_version(doc1["doc_id"]) is True
        assert _hit_doc_ids(kb.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=10)) == {
            doc1["doc_id"]
        }

    async def test_failed_new_version_keeps_old_active(self, kb, tmp_path):
        """新版本索引失败 → 停在 is_active=0/failed，旧生效版本不受影响。"""
        ing = KnowledgeIngestion(kb, FakeEmb())
        v1 = tmp_path / "ok.md"
        v1.write_text("# 报销制度\n## 差旅报销\n差旅费报销标准为每日三百元。\n", encoding="utf-8")
        doc1 = await ing.ingest_file(str(v1))
        gid = doc1["group_id"]

        bad = tmp_path / "bad.md"
        bad.write_text("   \n  ", encoding="utf-8")  # 解析后无有效文本 → IngestionError
        from agent.knowledge.ingestion import IngestionError

        with pytest.raises(IngestionError):
            await ing.ingest_file(str(bad), group_id=gid, version=2, is_active=False)
        # 旧版本仍生效、仍可检索
        assert kb.get_doc(doc1["doc_id"])["is_active"] is True
        assert _hit_doc_ids(kb.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=5)) == {
            doc1["doc_id"]
        }

    async def test_reindex_covers_all_versions(self, kb, tmp_path):
        ing = KnowledgeIngestion(kb, FakeEmb())
        v1 = tmp_path / "a.md"
        v1.write_text("# t\n## s\n差旅费报销标准每日三百元。\n", encoding="utf-8")
        doc1 = await ing.ingest_file(str(v1))
        gid = doc1["group_id"]
        v2 = tmp_path / "b.md"
        v2.write_text("# t\n## s\n差旅费报销标准每日五百元。\n", encoding="utf-8")
        doc2 = await ing.ingest_file(str(v2), group_id=gid, version=2, is_active=False)

        # reindex 事实源覆盖全部版本（含非生效），保证切到任意旧版本向量都可用
        n1 = len(kb.get_chunks_by_doc(doc1["doc_id"]))
        n2 = len(kb.get_chunks_by_doc(doc2["doc_id"]))
        assert len(kb.iter_chunks_for_reindex()) == n1 + n2

        kb.set_meta("old-model", 4)  # 模拟模型漂移
        assert kb.needs_reindex("fake-emb", 4)
        out = await ing.reindex()
        assert out["ok"] and not kb.needs_reindex("fake-emb", 4)
        # 两个版本重嵌入后分别切为生效都能向量检索
        kb.activate_version(doc1["doc_id"])
        assert kb.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=5)
        kb.activate_version(doc2["doc_id"])
        assert kb.search_by_vector(FakeEmb()._vec("差旅报销"), top_k=5)


# ---- api 端点 --------------------------------------------------------------


class TestVersionApi:
    @pytest.fixture
    def client(self, kb, monkeypatch):
        import agent.knowledge.retriever as retr_mod
        from agent.knowledge import api as kapi
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        monkeypatch.setattr(retr_mod, "build_default_embedding_client", lambda: FakeEmb())

        async def _noop_ingest(*_a: object, **_k: object) -> None:
            return None

        monkeypatch.setattr(kapi, "_run_ingest", _noop_ingest)
        kapi.reset_for_testing()  # api 层 _storage=None → 下次取 get_default_storage()（=kb）
        app = FastAPI()
        app.include_router(kapi.router)
        return TestClient(app)

    def test_upload_new_version_groups_and_stays_inactive(self, client, kb, tmp_path):
        _seed_version(
            kb, doc_id="base1", group_id="base1", version=1, is_active=True, text="差旅报销"
        )
        src = tmp_path / "v2.md"
        src.write_text("# t\n差旅费报销标准新版。\n", encoding="utf-8")
        r = client.post(
            "/knowledge/v1/docs/upload", json={"file_path": str(src), "base_doc_id": "base1"}
        )
        assert r.status_code == 200 and r.json()["status"] == "indexing"
        nd = kb.get_doc(r.json()["doc_id"])
        assert nd["group_id"] == "base1" and nd["version"] == 2
        assert nd["is_active"] is False  # 索引期间不生效
        assert kb.get_doc("base1")["is_active"] is True  # 旧版本仍生效

    def test_upload_new_version_bad_base_404(self, client, tmp_path):
        src = tmp_path / "v2.md"
        src.write_text("差旅报销", encoding="utf-8")
        r = client.post(
            "/knowledge/v1/docs/upload", json={"file_path": str(src), "base_doc_id": "nonexistent"}
        )
        assert r.status_code == 404

    def test_versions_endpoint(self, client, kb):
        _seed_version(
            kb, doc_id="g1v1", group_id="g1", version=1, is_active=False, text="差旅报销旧"
        )
        _seed_version(
            kb, doc_id="g1v2", group_id="g1", version=2, is_active=True, text="差旅报销新"
        )
        r = client.get("/knowledge/v1/docs/g1v1/versions")
        assert r.status_code == 200
        body = r.json()
        assert body["group_id"] == "g1" and body["active_doc_id"] == "g1v2"
        assert [v["version"] for v in body["versions"]] == [2, 1]
        assert body["versions"][0]["is_active"] is True

    def test_versions_404(self, client):
        assert client.get("/knowledge/v1/docs/nope/versions").status_code == 404

    def test_activate_endpoint_switches(self, client, kb):
        _seed_version(
            kb, doc_id="g1v1", group_id="g1", version=1, is_active=False, text="差旅报销旧"
        )
        _seed_version(
            kb, doc_id="g1v2", group_id="g1", version=2, is_active=True, text="差旅报销新"
        )
        r = client.post("/knowledge/v1/docs/g1v1/activate")
        assert r.status_code == 200 and r.json()["active_doc_id"] == "g1v1"
        assert kb.get_doc("g1v1")["is_active"] is True
        assert kb.get_doc("g1v2")["is_active"] is False

    def test_activate_404(self, client):
        assert client.post("/knowledge/v1/docs/nope/activate").status_code == 404

    def test_delete_all_versions(self, client, kb):
        _seed_version(
            kb, doc_id="g1v1", group_id="g1", version=1, is_active=False, text="差旅报销旧"
        )
        _seed_version(
            kb, doc_id="g1v2", group_id="g1", version=2, is_active=True, text="差旅报销新"
        )
        r = client.delete("/knowledge/v1/docs/g1v1?all_versions=true")
        assert r.status_code == 200 and r.json()["deleted"] is True
        assert kb.get_doc("g1v1") is None and kb.get_doc("g1v2") is None

    def test_list_docs_active_only_shape(self, client, kb):
        _seed_version(kb, doc_id="g1v1", group_id="g1", version=1, is_active=True, text="差旅报销")
        _seed_version(
            kb, doc_id="g1v2", group_id="g1", version=2, is_active=False, text="差旅报销新"
        )
        body = client.get("/knowledge/v1/docs").json()
        assert body["total"] == 1  # 一份逻辑资料
        d = body["docs"][0]
        assert d["id"] == "g1v1" and d["group_id"] == "g1" and d["version"] == 1
        assert d["is_active"] is True and d["version_count"] == 2

    def test_set_category_endpoint(self, client, kb):
        _seed_version(
            kb,
            doc_id="g1v1",
            group_id="g1",
            version=1,
            is_active=True,
            text="差旅报销",
            category="finance",
        )
        r = client.post("/knowledge/v1/docs/g1v1/category", json={"category": "legal"})
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True and body["category"] == "legal" and body["group_id"] == "g1"
        assert kb.get_doc("g1v1")["category"] == "legal"

    def test_set_category_404(self, client):
        r = client.post("/knowledge/v1/docs/nope/category", json={"category": "legal"})
        assert r.status_code == 404
