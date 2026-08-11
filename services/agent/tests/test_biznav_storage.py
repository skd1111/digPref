"""test_biznav_storage.py —— FeatureStorage 单元测试（Phase 2G V1.1）。

测试矩阵（8 个）：
- test_upsert_and_get
- test_list_by_project_isolated
- test_soft_delete_excludes_from_list
- test_hard_delete_cascades_file_index
- test_find_features_by_file
- test_concurrent_upsert_uses_version_lock
- test_edit_history_written_on_update
- test_rebuild_file_index
"""

from __future__ import annotations

import pytest
from agent.biznav.models import Feature, RelatedFile
from agent.biznav.storage import FeatureStorage, FeatureVersionConflict


def _make_feature(**overrides) -> Feature:
    base = dict(
        id="feat-1",
        name="订单管理",
        description="订单 CRUD",
        category="业务",
        project_name="demo",
        project_root="/tmp/demo",
        related_files=[RelatedFile(path="src/order/OrderService.java", role="service")],
        source="ai",
        ai_confidence=0.85,
        version=1,
    )
    base.update(overrides)
    return Feature(**base)


def test_upsert_and_get(tmp_path):
    db = str(tmp_path / "biznav.db")
    storage = FeatureStorage(db)
    f = _make_feature()
    storage.upsert(f)
    got = storage.get("feat-1", "demo")
    assert got is not None
    assert got.name == "订单管理"
    assert got.category == "业务"
    assert got.version == 1
    assert got.source == "ai"
    assert len(got.related_files) == 1
    assert got.related_files[0].path == "src/order/OrderService.java"


def test_list_by_project_isolated(tmp_path):
    db = str(tmp_path / "biznav.db")
    storage = FeatureStorage(db)
    storage.upsert(_make_feature(id="a", project_name="p1"))
    storage.upsert(_make_feature(id="b", project_name="p2"))
    storage.upsert(_make_feature(id="c", project_name="p1"))
    p1 = storage.list_by_project("p1")
    p2 = storage.list_by_project("p2")
    assert {f.id for f in p1} == {"a", "c"}
    assert {f.id for f in p2} == {"b"}


def test_soft_delete_excludes_from_list(tmp_path):
    db = str(tmp_path / "biznav.db")
    storage = FeatureStorage(db)
    storage.upsert(_make_feature(id="a"))
    storage.upsert(_make_feature(id="b"))
    storage.soft_delete("a", "demo")
    listed = storage.list_by_project("demo")
    assert {f.id for f in listed} == {"b"}
    listed_all = storage.list_by_project("demo", include_deleted=True)
    assert {f.id for f in listed_all} == {"a", "b"}


def test_hard_delete_cascades_file_index(tmp_path):
    db = str(tmp_path / "biznav.db")
    storage = FeatureStorage(db)
    storage.upsert(
        _make_feature(
            id="a",
            related_files=[
                RelatedFile(path="src/order/OrderService.java"),
                RelatedFile(path="src/order/OrderController.java"),
            ],
        )
    )
    # 反向索引已建
    hits = storage.find_features_by_file("src/order/OrderService.java", "demo")
    assert len(hits) == 1
    storage.delete("a", "demo")
    # 级联清
    hits_after = storage.find_features_by_file("src/order/OrderService.java", "demo")
    assert hits_after == []


def test_find_features_by_file(tmp_path):
    db = str(tmp_path / "biznav.db")
    storage = FeatureStorage(db)
    storage.upsert(_make_feature(id="a", related_files=[RelatedFile(path="src/order/X.java")]))
    storage.upsert(_make_feature(id="b", related_files=[RelatedFile(path="src/auth/X.java")]))
    storage.upsert(_make_feature(id="c", related_files=[RelatedFile(path="src/order/Y.java")]))
    hits = storage.find_features_by_file("src/order/X.java", "demo")
    assert {f.id for f in hits} == {"a"}


def test_concurrent_upsert_uses_version_lock(tmp_path):
    db = str(tmp_path / "biznav.db")
    storage = FeatureStorage(db)
    # 首次插入 → DB version=1
    storage.upsert(_make_feature(id="a", version=1))
    # 读回 + 改 name + 再次 upsert → DB version 变成 2
    f1 = storage.get("a", "demo")
    assert f1 is not None
    f1.name = "订单管理 v2"
    storage.upsert(f1)  # f1.version == 1 == DB → 成功，version 自增为 2
    # 此时再用旧 version=1 写入 → 应冲突
    f2 = _make_feature(id="a", version=1)
    with pytest.raises(FeatureVersionConflict):
        storage.upsert(f2)
    # 确认 DB 版本未被冲突修改
    got = storage.get("a", "demo")
    assert got is not None
    assert got.name == "订单管理 v2"
    assert got.version == 2


def test_edit_history_written_on_update(tmp_path):
    db = str(tmp_path / "biznav.db")
    storage = FeatureStorage(db)
    storage.upsert(_make_feature(id="a"))
    f = storage.get("a", "demo")
    assert f is not None
    f.name = "改后名字"
    storage.upsert(f)
    # 直接查 SQLite 验证
    import sqlite3

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT feature_id, before_json, after_json FROM feature_edit_history "
            "WHERE feature_id='a' ORDER BY id"
        ).fetchall()
    assert len(rows) == 2  # 一次 insert + 一次 update
    # 第二次：after_json 中 name 是 "改后名字"
    import json

    after = json.loads(rows[1][2])
    assert after["name"] == "改后名字"


def test_rebuild_file_index(tmp_path):
    db = str(tmp_path / "biznav.db")
    storage = FeatureStorage(db)
    storage.upsert(
        _make_feature(
            id="a",
            related_files=[
                RelatedFile(path="old/a.java"),
                RelatedFile(path="old/b.java"),
            ],
        )
    )
    storage.rebuild_file_index("a", ["new/x.java", "new/y.java"])
    # 旧路径已被重建清除
    assert storage.find_features_by_file("old/a.java", "demo") == []
    # 新路径可查到 feature a
    hits = storage.find_features_by_file("new/x.java", "demo")
    assert {f.id for f in hits} == {"a"}
    # 重建后索引里只有 new 路径
    import sqlite3

    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT file_path FROM feature_file_index WHERE feature_id='a' ORDER BY file_path"
        ).fetchall()
    assert [r[0] for r in rows] == ["new/x.java", "new/y.java"]
