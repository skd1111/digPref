"""sqlite-vec 统一向量存储测试（2026-09-01）。

覆盖：
    - vector_store 基础：序列化 / 建表（维度漂移重建）/ upsert / 余弦扫描
    - L2 语义缓存持久层：跨实例（重启）语义命中
    - 知识库分块向量层：upsert_chunks → search_by_vector / search_by_text
    - intent_memory 存量迁移：旧版 vec_json 列 → vec0 虚拟表
"""

from __future__ import annotations

import sqlite3

import pytest
from agent import vector_store as vs

# ---- vector_store 基础 --------------------------------------------------------


class TestVectorStoreBasics:
    def test_serialize_roundtrip(self):
        vec = [0.1, -0.5, 1.0, 0.0]
        blob = vs.serialize(vec)
        assert len(blob) == 4 * len(vec)
        assert vs.deserialize(blob) == pytest.approx(vec, rel=1e-6)

    def test_ensure_table_and_dim_rebuild(self):
        conn = sqlite3.connect(":memory:")
        assert vs.load_extension(conn)
        assert vs.ensure_vec_table(conn, "t_vec", 4)
        assert vs.table_dim(conn, "t_vec") == 4
        # 同维幂等；换维重建
        assert vs.ensure_vec_table(conn, "t_vec", 4)
        assert vs.ensure_vec_table(conn, "t_vec", 8)
        assert vs.table_dim(conn, "t_vec") == 8
        conn.close()

    def test_upsert_replace_delete_load(self):
        conn = sqlite3.connect(":memory:")
        vs.load_extension(conn)
        vs.ensure_vec_table(conn, "t_vec", 2)
        assert vs.upsert(conn, "t_vec", 1, [1.0, 0.0])
        assert vs.upsert(conn, "t_vec", 1, [0.0, 1.0])  # 覆盖（先删后插）
        assert vs.upsert(conn, "t_vec", 2, [0.0, 1.0])
        assert not vs.upsert(conn, "t_vec", 3, [0.0, 0.0])  # 零向量拒绝入库
        rows = dict(vs.load_all(conn, "t_vec"))
        assert set(rows) == {1, 2}
        assert rows[1] == pytest.approx([0.0, 1.0])
        vs.delete(conn, "t_vec", 2)
        assert set(dict(vs.load_all(conn, "t_vec"))) == {1}
        conn.close()

    def test_cosine_scan_semantics(self):
        """余弦扫描与原内存 _cosine 同语义：正交 0、同向 1、零向量 0。"""
        conn = sqlite3.connect(":memory:")
        vs.load_extension(conn)
        vs.ensure_vec_table(conn, "t_vec", 2)
        vs.upsert(conn, "t_vec", 1, [1.0, 0.0])
        vs.upsert(conn, "t_vec", 2, [0.0, 1.0])
        expr = vs.cosine_expr("embedding")
        scores = dict(conn.execute(f"SELECT rowid, {expr} FROM t_vec", (vs.serialize([1.0, 0.0]),)))
        assert scores[1] == pytest.approx(1.0, abs=1e-6)
        assert scores[2] == pytest.approx(0.0, abs=1e-6)
        # 零向量查询 → COALESCE 兜 0.0（不抛错）
        zero = dict(conn.execute(f"SELECT rowid, {expr} FROM t_vec", (vs.serialize([0.0, 0.0]),)))
        assert zero[1] == 0.0
        conn.close()


# ---- L2 语义缓存持久层 ---------------------------------------------------------


class TestL2Persistence:
    def test_cross_instance_semantic_hit(self):
        """进程重启（新实例、空内存层）后仍可从 router.db 语义命中。"""
        from agent.llm.cache_l2 import L2Cache

        c1 = L2Cache(enable=True)
        c1.put("ollama", "查订单", "订单列表")

        c2 = L2Cache(enable=True)  # 全新实例 = 模拟重启
        assert c2.stats()["size"] == 0  # 内存层为空
        assert c2.get("ollama", "查订单") == "订单列表"
        assert c2.stats()["hits"] >= 1

    def test_cross_instance_expired_miss(self):
        from agent.llm.cache_l2 import L2Cache

        c1 = L2Cache(enable=True, ttl_sec=0.001)
        c1.put("ollama", "查库存", "库存")
        import time

        time.sleep(0.05)
        c2 = L2Cache(enable=True, ttl_sec=0.001)
        assert c2.get("ollama", "查库存") is None


# ---- 知识库分块向量层 ----------------------------------------------------------


class TestKnowledgeChunks:
    def test_upsert_and_search(self, tmp_path):
        from agent.knowledge.models import KnowledgeChunk
        from agent.knowledge.storage import KnowledgeStorage

        storage = KnowledgeStorage(str(tmp_path / "knowledge.db"))
        chunks = [
            KnowledgeChunk(
                id="c1", doc_id="d1", seq=0, content="增值税应纳税额计算", embedding=[1.0, 0.0]
            ),
            KnowledgeChunk(
                id="c2", doc_id="d1", seq=1, content="企业所得税税前扣除", embedding=[0.0, 1.0]
            ),
            KnowledgeChunk(id="c3", doc_id="d2", seq=0, content="无向量分块", embedding=None),
        ]
        storage.upsert_chunks("d1", chunks[:2])
        storage.upsert_chunks("d2", chunks[2:])

        hits = storage.search_by_vector([0.9, 0.1], top_k=2, similarity_threshold=0.0)
        assert len(hits) == 2
        assert hits[0]["chunk"].id == "c1"  # 与查询向量最相近
        assert hits[0]["similarity"] > hits[1]["similarity"]

        # 阈值过滤
        strict = storage.search_by_vector([0.9, 0.1], top_k=2, similarity_threshold=0.99)
        assert len(strict) == 1

        # 文本兜底通道
        text_hits = storage.search_by_text("所得税", limit=5)
        assert [h["chunk"].id for h in text_hits] == ["c2"]

        assert storage.get_chunk("c1").content == "增值税应纳税额计算"
        assert len(storage.get_chunks_by_doc("d1")) == 2
        assert storage.get_stats().total_chunks == 3

    def test_delete_chunks_removes_vectors(self, tmp_path):
        from agent.knowledge.models import KnowledgeChunk
        from agent.knowledge.storage import KnowledgeStorage

        storage = KnowledgeStorage(str(tmp_path / "knowledge.db"))
        storage.upsert_chunks(
            "d1", [KnowledgeChunk(id="c1", doc_id="d1", seq=0, content="x", embedding=[1.0, 0.0])]
        )
        assert storage.delete_chunks_by_doc("d1") == 1
        assert storage.search_by_vector([1.0, 0.0], top_k=3) == []


# ---- intent_memory 存量迁移 ----------------------------------------------------


class TestIntentMemoryMigration:
    async def test_legacy_vec_json_migrated(self, monkeypatch):
        """旧库的 vec_json 文本列自动迁入 vec0，且旧列被移除。"""
        from agent.graph import intent_memory as im

        # 手工建旧版 schema（带 vec_json 列）并塞一条带向量的案例
        conn = sqlite3.connect(str(im._db_path()))
        conn.executescript(
            "CREATE TABLE IF NOT EXISTS intent_examples ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,"
            " query_text TEXT NOT NULL, intent_category TEXT NOT NULL,"
            " entities_json TEXT NOT NULL DEFAULT '[]',"
            " vec_json TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT 'auto',"
            " status TEXT NOT NULL DEFAULT 'neutral', ts TEXT NOT NULL);"
        )
        conn.execute(
            "INSERT INTO intent_examples (run_id, query_text, intent_category, vec_json, ts)"
            " VALUES ('r1', '查询订单表', 'data_query', '[0.0, 1.0]', 'now')"
        )
        conn.commit()
        conn.close()

        async def _fake_embed(text: str):
            return [0.0, 1.0] if "订单" in text else [-1.0, 0.0]

        monkeypatch.setattr(im, "_embed_text", _fake_embed)
        hits = await im.retrieve_examples("统计订单表行数")
        assert [h["query_text"] for h in hits] == ["查询订单表"]

        # 旧列已移除
        conn = sqlite3.connect(str(im._db_path()))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(intent_examples)")}
        conn.close()
        assert "vec_json" not in cols
