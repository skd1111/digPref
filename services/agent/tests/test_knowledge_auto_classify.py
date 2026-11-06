"""test_knowledge_auto_classify.py —— 知识库 LLM 自动分类验证。

测试矩阵（5 个）：
- test_get_kb_categories_reads_from_dictdb: get_kb_categories 正确读取
- test_auto_classify_normal_path: 正常分类路径
- test_auto_classify_invalid_key_returns_empty: LLM 返回非法值 → 空串
- test_auto_classify_exception_returns_empty: LLM 异常 → 空串
- test_auto_classify_label_fallback: LLM 返回 label 而非 key → 反向匹配
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.datadict.storage import DictStorage


@pytest.fixture()
def dict_db(tmp_path):
    """创建临时 dict.db 并返回路径（含种子数据）。"""
    db = str(tmp_path / "dict.db")
    DictStorage(db)  # 构造时自动 seed
    return db


# ---- get_kb_categories -------------------------------------------------------

def test_get_kb_categories_reads_from_dictdb(dict_db):
    """get_kb_categories 应从 dict.db 读取 category='知识库分类' 的条目。"""
    with patch("agent.paths.data_root") as mock_root:
        from pathlib import Path
        mock_root.return_value = Path(dict_db).parent
        from agent.knowledge.category import get_kb_categories

        cats = get_kb_categories()
        assert len(cats) == 9
        keys = {c["key"] for c in cats}
        assert keys == {
            "finance",
            "legal",
            "compliance",
            "data_security",
            "hr",
            "operations",
            "risk",
            "marketing",
            "engineering",
        }
        # 每条都有 label 和 description
        for c in cats:
            assert c["label"]
            assert "description" in c


def test_get_kb_categories_empty_on_bad_db(tmp_path):
    """dict.db 不可用时返回空列表（不抛异常）。"""
    with (
        patch("agent.paths.data_root") as mock_root,
        patch("agent.datadict.storage.DictStorage", side_effect=RuntimeError("db unavailable")),
    ):
        mock_root.return_value = tmp_path / "nonexistent"
        from agent.knowledge.category import get_kb_categories

        cats = get_kb_categories()
        assert cats == []


# ---- _auto_classify ----------------------------------------------------------

@pytest.fixture()
def ingestion_instance():
    """创建一个最小化的 Ingestion 实例（mock 掉 storage）。"""
    from agent.knowledge.ingestion import KnowledgeIngestion
    mock_storage = MagicMock()
    ing = KnowledgeIngestion.__new__(KnowledgeIngestion)
    ing.storage = mock_storage
    return ing


@pytest.mark.asyncio
async def test_auto_classify_normal_path(ingestion_instance, dict_db):
    """正常分类：LLM 返回合法 key → 返回该 key。"""
    with patch("agent.paths.data_root") as mock_root:
        from pathlib import Path
        mock_root.return_value = Path(dict_db).parent

        mock_llm = AsyncMock(return_value="finance")
        result = await ingestion_instance._auto_classify(
            "这是一份关于财务报销的文档", "报销指南", "baoxiao.pdf", mock_llm
        )
        assert result == "finance"


@pytest.mark.asyncio
async def test_auto_classify_invalid_key_returns_empty(ingestion_instance, dict_db):
    """LLM 返回不在合法 key 列表中的值 → 返回空串。"""
    with patch("agent.paths.data_root") as mock_root:
        from pathlib import Path
        mock_root.return_value = Path(dict_db).parent

        mock_llm = AsyncMock(return_value="unknown_category_xyz")
        result = await ingestion_instance._auto_classify(
            "一些文档内容", "标题", "file.txt", mock_llm
        )
        assert result == ""


@pytest.mark.asyncio
async def test_auto_classify_exception_returns_empty(ingestion_instance, dict_db):
    """LLM 抛异常 → 返回空串（best-effort）。"""
    with patch("agent.paths.data_root") as mock_root:
        from pathlib import Path
        mock_root.return_value = Path(dict_db).parent

        mock_llm = AsyncMock(side_effect=RuntimeError("LLM timeout"))
        result = await ingestion_instance._auto_classify(
            "文档内容", "标题", "file.txt", mock_llm
        )
        assert result == ""


@pytest.mark.asyncio
async def test_auto_classify_label_fallback(ingestion_instance, dict_db):
    """LLM 返回 label（如 '财务/财税'）而非 key → 反向匹配到 'finance'。"""
    with patch("agent.paths.data_root") as mock_root:
        from pathlib import Path
        mock_root.return_value = Path(dict_db).parent

        mock_llm = AsyncMock(return_value="财务/财税")
        result = await ingestion_instance._auto_classify(
            "关于税务审计的文档", "税务指南", "tax.docx", mock_llm
        )
        assert result == "finance"


@pytest.mark.asyncio
async def test_auto_classify_no_categories_returns_empty(ingestion_instance, tmp_path):
    """数据字典无分类条目 → 不调用 LLM，直接返回空串。"""
    # 创建一个空的 dict.db（无种子数据）
    empty_db = str(tmp_path / "empty_dict.db")
    from agent.datadict.models import DictItem
    storage = DictStorage.__new__(DictStorage)
    storage._db_path = empty_db
    from pathlib import Path
    Path(empty_db).parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(empty_db)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS dict_items("
        "key TEXT PRIMARY KEY, category TEXT, label TEXT, value TEXT,"
        " description TEXT, source TEXT, updated_by TEXT,"
        " created_at INTEGER, updated_at INTEGER)"
    )
    conn.close()

    with (
        patch("agent.paths.data_root") as mock_root,
        patch("agent.datadict.storage.DictStorage") as mock_cls,
    ):
        mock_root.return_value = tmp_path / "empty_dict_parent"
        mock_cls.return_value.list.return_value = []  # 无分类条目
        mock_llm = AsyncMock()
        result = await ingestion_instance._auto_classify(
            "文档内容", "标题", "file.txt", mock_llm
        )
        assert result == ""
        mock_llm.assert_not_called()
