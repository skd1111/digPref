"""test_biznav_import_export.py —— FeatureIO YAML/JSON 同步测试（Phase 2G V1.1）。

测试矩阵（7 个）：
- test_yaml_round_trip
- test_json_round_trip
- test_sync_yaml_to_db_replaces_ai_source
- test_sync_yaml_to_db_preserves_manual_source
- test_from_yaml_invalid_schema_raises
- test_yaml_unicode_chinese
- test_sync_db_to_yaml_round_trip
"""
from __future__ import annotations

import json

import pytest

from agent.biznav.import_export import FeatureIO, FeatureImportError
from agent.biznav.models import Feature, RelatedFile
from agent.biznav.storage import FeatureStorage


def _feat(id: str, **kw) -> Feature:
    base = dict(
        id=id,
        name="订单管理",
        description="订单 CRUD",
        category="业务",
        project_name="demo",
        project_root="/tmp/demo",
        related_files=[RelatedFile(path="src/order/X.java", role="service")],
        source="ai",
        ai_confidence=0.9,
        version=1,
    )
    base.update(kw)
    return Feature(**base)


def test_yaml_round_trip(tmp_path):
    text = FeatureIO.to_yaml(
        project_name="demo",
        project_root="/tmp/demo",
        features=[_feat("a")],
        generated_at="2026-07-28T10:00:00Z",
    )
    features = FeatureIO.from_yaml(text)
    assert len(features) == 1
    f = features[0]
    assert f.id == "a"
    assert f.name == "订单管理"
    assert f.category == "业务"
    assert f.related_files[0].path == "src/order/X.java"


def test_json_round_trip(tmp_path):
    text = FeatureIO.to_json([_feat("a"), _feat("b")])
    features = FeatureIO.from_json(text)
    assert len(features) == 2
    assert {f.id for f in features} == {"a", "b"}


def test_sync_yaml_to_db_replaces_ai_source(tmp_path):
    db = str(tmp_path / "biznav.db")
    storage = FeatureStorage(db)
    storage.upsert(_feat("a", name="原名", source="ai"))

    new_yaml = FeatureIO.to_yaml(
        "demo", "/tmp/demo",
        [_feat("a", name="新名")],
        "2026-07-28T10:00:00Z",
    )
    report = FeatureIO.sync_yaml_to_db(new_yaml, "demo", storage)
    assert report.inserted == 0
    assert report.updated == 1
    assert report.conflicts == []
    got = storage.get("a", "demo")
    assert got is not None
    assert got.name == "新名"
    assert got.source == "ai"  # YAML 覆盖后 source 强制 'ai'


def test_sync_yaml_to_db_preserves_manual_source(tmp_path):
    db = str(tmp_path / "biznav.db")
    storage = FeatureStorage(db)
    storage.upsert(_feat("a", name="DB 原名", source="manual"))

    yaml_text = FeatureIO.to_yaml(
        "demo", "/tmp/demo",
        [_feat("a", name="YAML 新名")],
        "2026-07-28T10:00:00Z",
    )
    report = FeatureIO.sync_yaml_to_db(yaml_text, "demo", storage)
    assert report.skipped == 1
    assert len(report.conflicts) == 1
    assert report.conflicts[0]["feature_id"] == "a"
    # DB 保留
    got = storage.get("a", "demo")
    assert got is not None
    assert got.name == "DB 原名"
    assert got.source == "manual"


def test_from_yaml_invalid_schema_raises(tmp_path):
    bad = "features:\n  - name: 'X'\n"  # 缺 id / category
    with pytest.raises(FeatureImportError):
        FeatureIO.from_yaml(bad)
    # 语法错误
    with pytest.raises(FeatureImportError):
        FeatureIO.from_yaml("not: [valid: yaml: example")


def test_yaml_unicode_chinese(tmp_path):
    text = FeatureIO.to_yaml(
        "demo", "/tmp/demo",
        [_feat("a", name="订单管理", description="包含中文描述与 emoji 测试 ✓")],
        "2026-07-28T10:00:00Z",
    )
    # allow_unicode=True → 直接含中文
    assert "订单管理" in text
    assert "✓" in text
    features = FeatureIO.from_yaml(text)
    assert features[0].description == "包含中文描述与 emoji 测试 ✓"


def test_sync_db_to_yaml_round_trip(tmp_path):
    db = str(tmp_path / "biznav.db")
    storage = FeatureStorage(db)
    storage.upsert(_feat("a"))
    storage.upsert(_feat("b", category="路由"))
    text = FeatureIO.sync_db_to_yaml(
        storage, project_name="demo", project_root="/tmp/demo"
    )
    features = FeatureIO.from_yaml(text)
    assert {f.id for f in features} == {"a", "b"}
    a = next(f for f in features if f.id == "a")
    assert a.name == "订单管理"
