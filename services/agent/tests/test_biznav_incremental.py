"""test_biznav_incremental —— Phase 2G V1.3 incremental 测试。

覆盖：
- 受影响 feature JOIN：upsert feature → 修改关联文件 → 触发 _handle_changes → emit
- 不受影响文件不 emit
- 多个 feature 受同一文件影响只 emit 一次（聚合在 batch 里）

不覆盖（V1.3 阶段）：
- watchfiles 异步 watcher 实际触发（依赖文件系统事件）
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.biznav.events import (
    EVT_FEATURE_AFFECTED,
    consume_biznav_events,
    flush_biznav_events,
)
from agent.biznav.incremental import _should_skip
from agent.biznav.models import Feature, RelatedFile
from agent.biznav.storage import FeatureStorage


@pytest.fixture(autouse=True)
def _clean_biznav_events():
    flush_biznav_events()
    yield
    flush_biznav_events()


def _make_storage(tmp_path: Path) -> FeatureStorage:
    db = str(tmp_path / "biznav_test.db")
    return FeatureStorage(db)


def _make_feature(fid: str, files: list[str], category: str = "order") -> Feature:
    return Feature(
        id=fid,
        name=f"feature-{fid}",
        description="test",
        category=category,
        project_name="demo",
        project_root="/tmp/demo",
        related_files=[RelatedFile(path=p, role="API") for p in files],
        related_apis=[],
        related_tables=[],
        business_rules=[],
        source="manual",
        ai_confidence=None,
        version=1,
        created_at=1_000_000,
        updated_at=1_000_000,
        deleted_at=None,
    )


def test_should_skip_filters_ignored_dirs():
    """忽略 .git / node_modules / __pycache__ / .eaide 等。"""
    assert _should_skip("/repo/.git/HEAD") is True
    assert _should_skip("/repo/node_modules/foo/bar.js") is True
    assert _should_skip("/repo/__pycache__/x.cpython-312.pyc") is True
    assert _should_skip("/repo/.eaide/features/demo.yaml") is True
    assert _should_skip("/repo/src/main.py") is False
    assert _should_skip("/repo/controllers/order.py") is False


@pytest.mark.asyncio
async def test_handle_changes_emits_affected_feature(tmp_path):
    """修改关联文件 → emit biznav_feature_affected + 列出受影响 feature。"""
    from agent.biznav.incremental import AffectedFeaturesWatcher

    project_root = tmp_path
    storage = _make_storage(tmp_path)
    # 准备一个 feature 关联 'controllers/order.py'
    f = _make_feature("order_create", ["controllers/order.py"])
    storage.upsert(f)

    watcher = AffectedFeaturesWatcher(project_root, "demo", storage)
    # 直接调 _handle_changes（不走 watchfiles）
    await watcher._handle_changes([str(project_root / "controllers" / "order.py")])  # noqa: SLF001

    events = await consume_biznav_events()
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == EVT_FEATURE_AFFECTED
    assert payload["project_name"] == "demo"
    assert len(payload["affected"]) == 1
    assert payload["affected"][0]["feature_id"] == "order_create"
    assert any("controllers/order.py" in p for p in payload["affected"][0]["files"])


@pytest.mark.asyncio
async def test_handle_changes_unrelated_file_emits_nothing(tmp_path):
    """修改不关联任何 feature 的文件 → emit 空。"""
    from agent.biznav.incremental import AffectedFeaturesWatcher

    storage = _make_storage(tmp_path)
    f = _make_feature("order_create", ["controllers/order.py"])
    storage.upsert(f)

    watcher = AffectedFeaturesWatcher(tmp_path, "demo", storage)
    await watcher._handle_changes([str(tmp_path / "README.md")])  # noqa: SLF001

    events = await consume_biznav_events()
    assert events == []


@pytest.mark.asyncio
async def test_handle_changes_aggregates_multiple_features(tmp_path):
    """同一文件被 2 个 feature 关联 → 1 个 SSE event + 2 个 affected entries。"""
    from agent.biznav.incremental import AffectedFeaturesWatcher

    storage = _make_storage(tmp_path)
    storage.upsert(_make_feature("f1", ["shared/util.py"]))
    storage.upsert(_make_feature("f2", ["shared/util.py"]))

    watcher = AffectedFeaturesWatcher(tmp_path, "demo", storage)
    await watcher._handle_changes([str(tmp_path / "shared" / "util.py")])  # noqa: SLF001

    events = await consume_biznav_events()
    assert len(events) == 1  # 1 个 batch event（不是 2 个）
    affected_ids = {e["feature_id"] for e in events[0][1]["affected"]}
    assert affected_ids == {"f1", "f2"}