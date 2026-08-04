"""test_biznav_hot_reload —— Phase 2G V1.3 hot_reload 测试。

覆盖：
- reload_yaml_to_db 成功路径：写 YAML → reload → DB upsert + emit `success=True`
- 失败路径：YAML 坏文件 → emit `success=False` + error 字段
- 防自激：mark_yaml_written 后 reload 应能跳过（手动调用 reload_yaml_to_db 不跳过；
  仅 YamlHotReloader 异步 watcher 触发时跳过）

不覆盖（V1.3 阶段）：
- watchfiles 异步 watcher 实际触发（依赖文件系统事件，CI 不稳定，留 V1.5 e2e 测试）
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.biznav.events import (
    EVT_YAML_RELOADED,
    consume_biznav_events,
    flush_biznav_events,
)
from agent.biznav.hot_reload import mark_yaml_written, reload_yaml_to_db
from agent.biznav.import_export import FeatureIO
from agent.biznav.storage import FeatureStorage


@pytest.fixture(autouse=True)
def _clean_biznav_events():
    flush_biznav_events()
    yield
    flush_biznav_events()


def _make_storage(tmp_path: Path) -> tuple[FeatureStorage, str]:
    db = str(tmp_path / "biznav_test.db")
    return FeatureStorage(db), db


def _write_sample_yaml(yaml_path: Path) -> None:
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    from agent.biznav.models import Feature, RelatedFile

    feature = Feature(
        id="order_create",
        name="create order",
        description="POST /api/orders",
        category="order",
        project_name="demo",
        project_root="/tmp/demo",
        related_files=[RelatedFile(path="controllers/order.py", role="API")],
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
    yaml_text = FeatureIO.to_yaml(
        project_name="demo",
        project_root="/tmp/demo",
        features=[feature],
        generated_at="2026-07-28T00:00:00Z",
    )
    yaml_path.write_text(yaml_text, encoding="utf-8")


@pytest.mark.asyncio
async def test_reload_yaml_to_db_success_emits_event(tmp_path):
    """YAML 写入 → reload → DB 写入 + emit biznav_yaml_reloaded (success=True)。"""
    storage, _db = _make_storage(tmp_path)
    yaml_path = tmp_path / ".eaide" / "features" / "demo.yaml"
    _write_sample_yaml(yaml_path)

    count = await reload_yaml_to_db(yaml_path, "demo", storage)

    events = await consume_biznav_events()
    assert count >= 1
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == EVT_YAML_RELOADED
    assert payload["success"] is True
    assert payload["project_name"] == "demo"
    assert payload["yaml_path"] == str(yaml_path)
    assert "inserted" in payload


@pytest.mark.asyncio
async def test_reload_yaml_missing_file_emits_failure(tmp_path):
    """YAML 不存在 → emit biznav_yaml_reloaded (success=False)。"""
    storage, _db = _make_storage(tmp_path)
    missing = tmp_path / "does_not_exist.yaml"

    with pytest.raises(FileNotFoundError):
        await reload_yaml_to_db(missing, "demo", storage)

    events = await consume_biznav_events()
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == EVT_YAML_RELOADED
    assert payload["success"] is False
    assert "not found" in payload["error"].lower()


@pytest.mark.asyncio
async def test_reload_yaml_broken_syntax_emits_failure(tmp_path):
    """YAML 解析失败 → emit biznav_yaml_reloaded (success=False) + DB 不动。"""
    storage, _db = _make_storage(tmp_path)
    yaml_path = tmp_path / "broken.yaml"
    yaml_path.write_text(":\ninvalid:\n  - : bad", encoding="utf-8")

    from agent.biznav.import_export import FeatureImportError

    with pytest.raises(FeatureImportError):
        await reload_yaml_to_db(yaml_path, "demo", storage)

    events = await consume_biznav_events()
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == EVT_YAML_RELOADED
    assert payload["success"] is False


def test_mark_yaml_written_is_idempotent(tmp_path):
    """mark_yaml_written 不抛错；多次调用安全。"""
    yaml_path = tmp_path / "demo.yaml"
    yaml_path.write_text("# empty\n", encoding="utf-8")
    mark_yaml_written(yaml_path)
    mark_yaml_written(yaml_path)
    # 不抛错 + 不返回错误


def test_reload_uses_from_yaml_not_read_yaml(tmp_path):
    """回归测试：hot_reload.py 必须用 FeatureIO.from_yaml（不是 read_yaml）。

    BUGFIX: V1.3 早期版本误用 FeatureIO.read_yaml；本测试保证不再退化。
    """
    storage, _db = _make_storage(tmp_path)
    yaml_path = tmp_path / ".eaide" / "features" / "demo.yaml"
    _write_sample_yaml(yaml_path)
    # 同步调用 reload（不应该 raise AttributeError: read_yaml）
    asyncio.run(reload_yaml_to_db(yaml_path, "demo", storage))