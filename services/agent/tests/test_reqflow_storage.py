"""test_reqflow_storage.py —— reqflow 存储层测试（批次/卡片 CRUD + 编号 + 版本）。"""

from __future__ import annotations

import pytest
from agent.reqflow.storage import ReqCardStorage


@pytest.fixture
def storage(tmp_path):
    return ReqCardStorage(str(tmp_path / "reqcards.db"))


def test_create_batch_auto_id(storage):
    b = storage.create_batch(project_name="proj", name="2026-08 批次")
    assert b.id.startswith("BAT-")
    assert b.name == "2026-08 批次"
    assert b.status == "open"


def test_create_batch_default_name(storage):
    b = storage.create_batch(project_name="proj")
    assert b.name  # 缺省名非空（日期）


def test_create_card_auto_number_daily(storage):
    b = storage.create_batch(project_name="proj")
    c1 = storage.create_card(batch_id=b.id, project_name="proj", system_name="订单系统", title="A")
    c2 = storage.create_card(batch_id=b.id, project_name="proj", system_name="订单系统", title="B")
    assert c1.id.startswith("REQ-")
    assert c1.id != c2.id
    assert int(c1.id.rsplit("-", 1)[1]) + 1 == int(c2.id.rsplit("-", 1)[1])
    assert c1.version == 1


def test_update_card_status_validated(storage):
    b = storage.create_batch(project_name="proj")
    c = storage.create_card(batch_id=b.id, project_name="proj", system_name="s", title="A")
    storage.update_card(c.id, status="pending_approval")
    assert storage.get_card(c.id).status == "pending_approval"
    with pytest.raises(ValueError):
        storage.update_card(c.id, status="done")  # 跳级非法


def test_update_card_bumps_version_and_snapshots(storage):
    b = storage.create_batch(project_name="proj")
    c = storage.create_card(batch_id=b.id, project_name="proj", system_name="s", title="旧标题")
    storage.update_card(c.id, title="新标题")
    latest = storage.get_card(c.id)
    assert latest.version == 2
    assert latest.title == "新标题"
    versions = storage.list_versions(c.id)
    assert [v["version"] for v in versions] == [1]  # 倒序；v1 = 旧标题快照
    snap = storage.get_version(c.id, 1)
    assert snap["title"] == "旧标题"
    assert snap["version"] == 1


def test_update_card_no_fields_no_version_bump(storage):
    b = storage.create_batch(project_name="proj")
    c = storage.create_card(batch_id=b.id, project_name="proj", system_name="s", title="A")
    storage.update_card(c.id)
    assert storage.get_card(c.id).version == 1


def test_get_version_missing_raises(storage):
    b = storage.create_batch(project_name="proj")
    c = storage.create_card(batch_id=b.id, project_name="proj", system_name="s", title="A")
    with pytest.raises(KeyError):
        storage.get_version(c.id, 99)


def test_delete_only_draft(storage):
    b = storage.create_batch(project_name="proj")
    c = storage.create_card(batch_id=b.id, project_name="proj", system_name="s", title="A")
    storage.update_card(c.id, status="pending_approval")
    with pytest.raises(ValueError):
        storage.delete_card(c.id)
    # draft 可删（先驳回回不去 draft，造一张新 draft 验证）
    c2 = storage.create_card(batch_id=b.id, project_name="proj", system_name="s", title="B")
    storage.delete_card(c2.id)
    assert storage.get_card(c2.id) is None


def test_list_cards_by_batch_status_feature(storage):
    b = storage.create_batch(project_name="proj")
    c1 = storage.create_card(
        batch_id=b.id, project_name="proj", system_name="s", title="A", feature_ids=["f1"]
    )
    storage.create_card(batch_id=b.id, project_name="proj", system_name="s", title="B")
    assert len(storage.list_cards(batch_id=b.id)) == 2
    assert [c.id for c in storage.list_cards(feature_id="f1")] == [c1.id]
    storage.update_card(c1.id, status="pending_approval")
    assert len(storage.list_cards(batch_id=b.id, status="pending_approval")) == 1


def test_batch_stats(storage):
    b = storage.create_batch(project_name="proj")
    storage.create_card(batch_id=b.id, project_name="proj", system_name="s", title="A")
    stats = storage.batch_stats(b.id)
    assert stats["total"] == 1
    assert stats["draft"] == 1
    assert stats["done"] == 0


def test_list_batches_filter_project(storage):
    storage.create_batch(project_name="p1")
    storage.create_batch(project_name="p2")
    assert len(storage.list_batches()) == 2
    assert len(storage.list_batches(project_name="p1")) == 1
