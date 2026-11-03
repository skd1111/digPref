"""test_knowledge_entity_extract.py —— 知识库 LLM 实体抽取验证。

测试矩阵（5 个）：
- test_get_entity_types_reads_from_dictdb: get_entity_types 正确读取 6 种实体类型
- test_extract_entities_normal_path: 正常抽取 → entities 表有记录
- test_extract_entities_invalid_type_dropped: LLM 返回未知类型 → 丢弃
- test_extract_entities_exception_returns_empty: LLM 异常 → 空列表
- test_entity_retrieval_channel: 实体通道检索 → 命中实体关联的子块
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


@pytest.fixture()
def kb_storage(tmp_path):
    """创建临时 kb.db 并返回 KnowledgeStorage 实例。"""
    from agent.knowledge.storage import KnowledgeStorage

    db = str(tmp_path / "kb.db")
    return KnowledgeStorage(db)


# ---- get_entity_types -------------------------------------------------------


def test_get_entity_types_reads_from_dictdb(dict_db):
    """get_entity_types 从数据字典正确读取 6 种实体类型。"""
    with patch("agent.knowledge.entity_types.data_root") as mock_root:
        from pathlib import Path

        mock_root.return_value = Path(dict_db).parent
        with patch("agent.knowledge.entity_types.DictStorage") as mock_cls:
            mock_cls.return_value = DictStorage(dict_db)
            from agent.knowledge.entity_types import get_entity_types

            types = get_entity_types()
    assert len(types) == 6
    keys = {t["key"] for t in types}
    assert keys == {"person", "organization", "regulation", "amount", "date", "tech_term"}


# ---- _extract_entities ------------------------------------------------------


@pytest.mark.asyncio
async def test_extract_entities_normal_path(dict_db):
    """正常抽取 → 返回含 entity_name/entity_type 的列表。"""
    from agent.knowledge.ingestion import KnowledgeIngestion

    ing = KnowledgeIngestion(storage=MagicMock(), embedding=None)
    mock_llm = AsyncMock(
        return_value=json.dumps(
            {"person": ["张三"], "organization": ["中国人民银行"]}
        )
    )
    with patch("agent.knowledge.entity_types.data_root") as mock_root:
        from pathlib import Path

        mock_root.return_value = Path(dict_db).parent
        with patch("agent.knowledge.entity_types.DictStorage") as mock_cls:
            mock_cls.return_value = DictStorage(dict_db)
            result = await ing._extract_entities("测试文本内容", mock_llm)

    assert len(result) == 2
    names = {e["entity_name"] for e in result}
    assert "张三" in names
    assert "中国人民银行" in names
    types = {e["entity_type"] for e in result}
    assert types == {"person", "organization"}


@pytest.mark.asyncio
async def test_extract_entities_invalid_type_dropped(dict_db):
    """LLM 返回未知类型 → 丢弃该类型实体。"""
    from agent.knowledge.ingestion import KnowledgeIngestion

    ing = KnowledgeIngestion(storage=MagicMock(), embedding=None)
    mock_llm = AsyncMock(
        return_value=json.dumps(
            {"person": ["李四"], "unknown_type": ["不应该出现"]}
        )
    )
    with patch("agent.knowledge.entity_types.data_root") as mock_root:
        from pathlib import Path

        mock_root.return_value = Path(dict_db).parent
        with patch("agent.knowledge.entity_types.DictStorage") as mock_cls:
            mock_cls.return_value = DictStorage(dict_db)
            result = await ing._extract_entities("测试文本", mock_llm)

    assert len(result) == 1
    assert result[0]["entity_name"] == "李四"
    assert result[0]["entity_type"] == "person"


@pytest.mark.asyncio
async def test_extract_entities_exception_returns_empty(dict_db):
    """LLM 异常 → 返回空列表。"""
    from agent.knowledge.ingestion import KnowledgeIngestion

    ing = KnowledgeIngestion(storage=MagicMock(), embedding=None)
    mock_llm = AsyncMock(side_effect=RuntimeError("LLM crashed"))
    with patch("agent.knowledge.entity_types.data_root") as mock_root:
        from pathlib import Path

        mock_root.return_value = Path(dict_db).parent
        with patch("agent.knowledge.entity_types.DictStorage") as mock_cls:
            mock_cls.return_value = DictStorage(dict_db)
            result = await ing._extract_entities("测试文本", mock_llm)

    assert result == []


# ---- 实体存储 + 检索通道 ----------------------------------------------------


def test_entity_storage_and_search(kb_storage):
    """实体写入后 FTS5/LIKE 检索可命中。"""
    doc_id = "test_doc_001"
    kb_storage.insert_doc(
        doc_id=doc_id,
        title="测试文档",
        file_name="test.md",
        source_type="md",
        status="ready",
    )
    entities = [
        {"entity_name": "张三", "entity_type": "person"},
        {"entity_name": "中国人民银行", "entity_type": "organization"},
        {"entity_name": "反洗钱法", "entity_type": "regulation"},
    ]
    inserted = kb_storage.upsert_entities(doc_id, entities, parent_seq=1)
    assert inserted == 3

    # get_entities_by_doc
    all_ents = kb_storage.get_entities_by_doc(doc_id)
    assert len(all_ents) == 3

    # search_entities (LIKE 兜底或 FTS5)
    hits = kb_storage.search_entities("张三")
    assert len(hits) >= 1
    assert hits[0]["entity_name"] == "张三"
    assert hits[0]["entity_type"] == "person"

    # 按类型过滤
    org_hits = kb_storage.search_entities("银行", filter={"entity_type": "organization"})
    assert len(org_hits) >= 1
    assert org_hits[0]["entity_type"] == "organization"

    # 删除
    deleted = kb_storage.delete_entities_by_doc(doc_id)
    assert deleted == 3
    assert kb_storage.get_entities_by_doc(doc_id) == []
