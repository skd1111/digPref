# services/agent/tests/test_knowledge_docs_search.py
"""知识库文档列表搜索（FTS5 title+file_name 全文检索）单元测试。

覆盖：
- docs FTS 增量同步（insert → 命中；delete → 不命中；activate_version 切换）
- list_docs(q=...) 中文分词搜索 + BM25 排序
- list_docs(category=...) 分类过滤
- list_docs(q=..., category=...) 组合过滤
- count_docs 同步支持 q/category
- 旧库升级：无 docs_fts 表时自动创建并回填
"""

from __future__ import annotations

import pytest
from agent.config import settings
from agent.knowledge import rag_config
from agent.knowledge.storage import KnowledgeStorage

# ---- fixtures ---------------------------------------------------------------


@pytest.fixture
def kb(tmp_path, monkeypatch):
    """把知识库数据根钉到 tmp（测试隔离）。"""
    monkeypatch.setattr(settings, "rag_kb_dir", str(tmp_path / "knowledge"))
    monkeypatch.setattr(settings, "rag_rerank_enabled", False)
    return KnowledgeStorage(rag_config.kb_db_path())


def _seed(
    kb: KnowledgeStorage,
    *,
    doc_id: str,
    title: str,
    file_name: str,
    category: str = "",
    is_active: bool = True,
    group_id: str | None = None,
) -> None:
    """快速插入一条文档（跳过文件解析）。"""
    kb.insert_doc(
        doc_id=doc_id,
        title=title,
        file_name=file_name,
        source_type="md",
        category=category,
        is_active=is_active,
        status="ready",
        group_id=group_id,
    )


# ---- FTS 增量同步 -----------------------------------------------------------


class TestDocsFtsSync:
    """文档增删改 → docs_fts 自动同步。"""

    def test_insert_then_searchable(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="差旅报销制度", file_name="baoxiao.md")
        hits = kb.list_docs(q="报销")
        assert len(hits) == 1
        assert hits[0]["doc_id"] == "d1"

    def test_hard_delete_then_not_searchable(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="差旅报销制度", file_name="baoxiao.md")
        kb.hard_delete_doc("d1")
        hits = kb.list_docs(q="报销")
        assert len(hits) == 0

    def test_soft_delete_then_not_searchable(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="数据安全规范", file_name="security.md")
        kb.soft_delete_doc("d1")
        hits = kb.list_docs(q="安全")
        assert len(hits) == 0

    def test_activate_version_swaps_fts(self, kb: KnowledgeStorage) -> None:
        """切换生效版本后，旧版本移出 FTS、新版本移入。"""
        _seed(
            kb, doc_id="v1", title="旧版报销制度", file_name="old.md", is_active=True, group_id="g1"
        )
        _seed(
            kb,
            doc_id="v2",
            title="新版差旅规则",
            file_name="new.md",
            is_active=False,
            group_id="g1",
        )
        # v1 生效 → 搜"旧版"命中 v1
        hits = kb.list_docs(q="旧版")
        assert [h["doc_id"] for h in hits] == ["v1"]
        # 切到 v2 → v1 移出 FTS，v2 移入
        kb.activate_version("v2")
        hits_old = kb.list_docs(q="旧版")
        assert len(hits_old) == 0
        hits_new = kb.list_docs(q="差旅")
        assert len(hits_new) == 1
        assert hits_new[0]["doc_id"] == "v2"


# ---- list_docs 搜索 --------------------------------------------------------


class TestListDocsSearch:
    """list_docs(q=...) 搜索行为。"""

    def test_chinese_tokenization(self, kb: KnowledgeStorage) -> None:
        """中文分词：搜'报销'应命中含'差旅报销'的文档。"""
        _seed(kb, doc_id="d1", title="差旅报销制度", file_name="baoxiao.md")
        _seed(kb, doc_id="d2", title="数据安全规范", file_name="security.md")
        hits = kb.list_docs(q="报销")
        assert len(hits) == 1
        assert hits[0]["doc_id"] == "d1"

    def test_file_name_search(self, kb: KnowledgeStorage) -> None:
        """搜索可命中文件名。"""
        _seed(kb, doc_id="d1", title="制度文件", file_name="compliance_2026.pdf")
        hits = kb.list_docs(q="compliance")
        assert len(hits) == 1
        assert hits[0]["doc_id"] == "d1"

    def test_no_match_returns_empty(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="报销制度", file_name="a.md")
        hits = kb.list_docs(q="不存在的关键词")
        assert hits == []

    def test_empty_query_returns_all(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="文档A", file_name="a.md")
        _seed(kb, doc_id="d2", title="文档B", file_name="b.md")
        hits = kb.list_docs(q="")
        assert len(hits) == 2

    def test_none_query_returns_all(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="文档A", file_name="a.md")
        hits = kb.list_docs(q=None)
        assert len(hits) == 1


# ---- list_docs 分类过滤 ----------------------------------------------------


class TestListDocsCategory:
    """list_docs(category=...) 分类过滤。"""

    def test_filter_by_category(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="报销制度", file_name="a.md", category="finance")
        _seed(kb, doc_id="d2", title="安全规范", file_name="b.md", category="compliance")
        hits = kb.list_docs(category="finance")
        assert len(hits) == 1
        assert hits[0]["doc_id"] == "d1"

    def test_empty_category_no_filter(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="A", file_name="a.md", category="finance")
        _seed(kb, doc_id="d2", title="B", file_name="b.md", category="legal")
        hits = kb.list_docs(category="")
        assert len(hits) == 2


# ---- 组合过滤 ---------------------------------------------------------------


class TestCombinedFilter:
    """list_docs(q=..., category=...) 搜索 + 分类组合。"""

    def test_search_and_category(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="报销制度", file_name="a.md", category="finance")
        _seed(kb, doc_id="d2", title="报销流程", file_name="b.md", category="legal")
        _seed(kb, doc_id="d3", title="安全规范", file_name="c.md", category="finance")
        # 搜"报销" + 分类 finance → 只命中 d1
        hits = kb.list_docs(q="报销", category="finance")
        assert len(hits) == 1
        assert hits[0]["doc_id"] == "d1"

    def test_search_match_but_category_mismatch(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="报销制度", file_name="a.md", category="legal")
        hits = kb.list_docs(q="报销", category="finance")
        assert hits == []


# ---- count_docs 同步支持 ---------------------------------------------------


class TestCountDocs:
    """count_docs 同步支持 q/category。"""

    def test_count_with_query(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="报销制度", file_name="a.md")
        _seed(kb, doc_id="d2", title="安全规范", file_name="b.md")
        assert kb.count_docs(q="报销") == 1
        assert kb.count_docs(q="安全") == 1
        assert kb.count_docs(q="不存在") == 0

    def test_count_with_category(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="A", file_name="a.md", category="finance")
        _seed(kb, doc_id="d2", title="B", file_name="b.md", category="legal")
        assert kb.count_docs(category="finance") == 1

    def test_count_combined(self, kb: KnowledgeStorage) -> None:
        _seed(kb, doc_id="d1", title="报销制度", file_name="a.md", category="finance")
        _seed(kb, doc_id="d2", title="报销流程", file_name="b.md", category="legal")
        assert kb.count_docs(q="报销", category="finance") == 1


# ---- 旧库升级 ---------------------------------------------------------------


class TestOldDbUpgrade:
    """旧库无 docs_fts 表时，打开后自动创建并回填。"""

    def test_rebuild_on_open(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(settings, "rag_kb_dir", str(tmp_path / "knowledge"))
        db_path = rag_config.kb_db_path()
        # 第一次打开并插入文档
        kb1 = KnowledgeStorage(db_path)
        _seed(kb1, doc_id="d1", title="差旅报销", file_name="a.md")
        # 确认 FTS 已同步
        assert len(kb1.list_docs(q="报销")) == 1
        # 手动清空 docs_fts 模拟旧库
        import sqlite3

        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM docs_fts")
        conn.commit()
        conn.close()
        # 重新打开 → _connect 会 _rebuild_docs_fts
        kb2 = KnowledgeStorage(db_path)
        hits = kb2.list_docs(q="报销")
        assert len(hits) == 1
        assert hits[0]["doc_id"] == "d1"
