"""test_datadict_seed_options.py —— 数据字典种子数据（业务下拉选项）验证。

测试矩阵（7 个）：
- test_all_six_categories_seeded: 7 个分组全部写入
- test_kb_category_count: 知识库分类 4 条
- test_db_type_count_and_metadata: 数据库类型 16 条 + JSON 元数据可解析
- test_env_type_values: 环境类型 value 正确
- test_im_channel_metadata: IM 渠道 icon/color JSON 元数据正确
- test_entity_type_count: 实体类型 6 条
- test_seed_items_has_no_duplicate_keys: SEED_ITEMS 无重复
"""

from __future__ import annotations

import json

import pytest
from agent.datadict.storage import SEED_ITEMS, DictStorage


@pytest.fixture()
def storage(tmp_path):
    """创建临时 dict.db 并返回 DictStorage 实例（自动 seed）。"""
    db = str(tmp_path / "dict.db")
    return DictStorage(db)


# ---- 分组计数 ----------------------------------------------------------------


def test_all_six_categories_seeded(storage):
    """7 个业务下拉分组全部存在于 seed 数据中。"""
    cats = set(storage.categories())
    expected = {
        "知识库分类",
        "资产类型",
        "数据库类型",
        "环境类型",
        "IM渠道",
        "模型部署类型",
        "实体类型",
    }
    assert expected.issubset(cats), f"缺少分组: {expected - cats}"


def test_kb_category_count(storage):
    """知识库分类应有 9 条种子数据（4 基础 + 5 扩展业务域）。"""
    items = storage.list(category="知识库分类")
    assert len(items) == 9
    keys = {item.key for item in items}
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


def test_kb_category_backfill_on_existing_db(tmp_path):
    """已有库（非空）也会补齐「知识库分类」缺失项，不覆盖用户改过的条目。"""
    import sqlite3

    db = str(tmp_path / "dict.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS dict_items("
        "key TEXT PRIMARY KEY, category TEXT, label TEXT, value TEXT,"
        " description TEXT, source TEXT, updated_by TEXT,"
        " created_at INTEGER, updated_at INTEGER)"
    )
    # 已有一条被用户改过标签的分类项
    conn.execute(
        "INSERT INTO dict_items(key, category, label, value, description, source,"
        " updated_by, created_at, updated_at)"
        " VALUES('finance','知识库分类','财务（用户改名）','finance','用户自定义','manual',"
        "'admin',1,1)"
    )
    conn.commit()
    conn.close()

    storage = DictStorage(db)
    items = storage.list(category="知识库分类")
    keys = {item.key for item in items}
    # 缺失的分类被补齐
    assert {"hr", "operations", "risk", "marketing", "engineering"}.issubset(keys)
    # 用户改过的那条不被覆盖
    finance = next(i for i in items if i.key == "finance")
    assert finance.label == "财务（用户改名）"
    assert finance.source == "manual"


def test_db_type_count_and_metadata(storage):
    """数据库类型应有 16 条种子数据，且 JSON 元数据可解析。"""
    items = storage.list(category="数据库类型")
    assert len(items) == 16

    # 验证 MySQL 元数据
    mysql = next((i for i in items if i.key == "mysql"), None)
    assert mysql is not None
    meta = json.loads(mysql.description)
    assert meta["defaultPort"] == 3306
    assert meta["dbCategory"] == "mainstream"
    assert meta["placeholderDb"] == "my_database"
    assert meta["placeholderUser"] == "readonly"

    # 验证国产信创：达梦
    dm = next((i for i in items if i.key == "dm"), None)
    assert dm is not None
    dm_meta = json.loads(dm.description)
    assert dm_meta["defaultPort"] == 5236
    assert dm_meta["dbCategory"] == "xinchuang"


def test_env_type_values(storage):
    """环境类型的 value 应为 dev/test/staging/prod。"""
    items = storage.list(category="环境类型")
    assert len(items) == 4
    values = {item.value for item in items}
    assert values == {"dev", "test", "staging", "prod"}


def test_im_channel_metadata(storage):
    """IM 渠道的 JSON 元数据包含 icon 和 color。"""
    items = storage.list(category="IM渠道")
    assert len(items) == 3

    wecom = next((i for i in items if i.key == "wecom"), None)
    assert wecom is not None
    meta = json.loads(wecom.description)
    assert meta["icon"] == "💬"
    assert meta["color"] == "#07c160"

    feishu = next((i for i in items if i.key == "feishu"), None)
    assert feishu is not None
    feishu_meta = json.loads(feishu.description)
    assert feishu_meta["icon"] == "🚀"


def test_entity_type_count(storage):
    """实体类型应有 6 条种子数据。"""
    items = storage.list(category="实体类型")
    assert len(items) == 6
    keys = {item.key for item in items}
    assert keys == {"person", "organization", "regulation", "amount", "date", "tech_term"}


# ---- SEED_ITEMS 常量自检 -----------------------------------------------------


def test_seed_items_has_no_duplicate_keys():
    """SEED_ITEMS 内同一 (category, key) 不应重复。"""
    seen: set[tuple[str, str]] = set()
    for item in SEED_ITEMS:
        pair = (item["category"], item["key"])
        assert pair not in seen, f"重复种子条目: {pair}"
        seen.add(pair)
