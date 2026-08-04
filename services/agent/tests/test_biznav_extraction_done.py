"""test_biznav_extraction_done —— Phase 2G V1.3 extraction_done emit 测试。

覆盖：
- api.py /biznav/extract 端点返回 job_id 后，后台任务完成时 emit biznav_extraction_done
- 失败场景：mock extractor.extract_all 抛错 → emit success=False + error 字段
- 成功场景：mock extractor 返回 ExtractionResult → emit success=True + features_generated

策略：直接调 FastAPI TestClient 触发 /biznav/extract；后台任务用 asyncio.run 拉出
emit 的事件。
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.biznav.events import (
    EVT_EXTRACTION_DONE,
    consume_biznav_events,
    flush_biznav_events,
)


@pytest.fixture(autouse=True)
def _clean_biznav_events():
    flush_biznav_events()
    yield
    flush_biznav_events()


@pytest.mark.asyncio
async def test_extraction_done_event_emitted_on_success(tmp_path, monkeypatch):
    """提取任务成功 → emit success=True。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent.biznav import api as biznav_api
    from agent.biznav.extractor import ExtractionResult
    from agent.biznav import extractor as biznav_extractor

    # 准备一个空项目目录（_run 要 root_path.exists() 为真）
    project_root = tmp_path / "demo_project"
    project_root.mkdir()

    # mock FeatureExtractor.extract_all 返回成功结果
    class _FakeExtractor:
        def __init__(self, **kwargs):
            pass

        async def extract_all(self):
            return ExtractionResult(
                total_files=12, processed_files=12, features_generated=3,
                job_id=0, errors=[],
            )

    # patch 真实模块（api.py inline `from .extractor import FeatureExtractor`）
    monkeypatch.setattr(biznav_extractor, "FeatureExtractor", _FakeExtractor)

    test_app = FastAPI()
    test_app.include_router(biznav_api.router)

    with TestClient(test_app) as client:
        resp = client.post(
            "/biznav/extract",
            json={"project_name": "demo", "project_root": str(project_root)},
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]

    for _ in range(20):
        events = await consume_biznav_events()
        if events:
            break
        await asyncio.sleep(0.05)

    assert len(events) >= 1
    done_events = [e for e in events if e[0] == EVT_EXTRACTION_DONE]
    assert len(done_events) == 1
    kind, payload = done_events[0]
    assert payload["success"] is True
    assert payload["job_id"] == job_id
    assert payload["project_name"] == "demo"
    assert payload["features_generated"] == 3
    assert payload.get("error") is None


@pytest.mark.asyncio
async def test_extraction_done_event_emitted_on_failure(tmp_path, monkeypatch):
    """提取任务抛错 → emit success=False + error 字段。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent.biznav import api as biznav_api
    from agent.biznav import extractor as biznav_extractor

    project_root = tmp_path / "demo_project"
    project_root.mkdir()

    class _CrashingExtractor:
        def __init__(self, **kwargs):
            pass

        async def extract_all(self):
            raise RuntimeError("simulated LLM crash")

    monkeypatch.setattr(biznav_extractor, "FeatureExtractor", _CrashingExtractor)

    test_app = FastAPI()
    test_app.include_router(biznav_api.router)

    with TestClient(test_app) as client:
        resp = client.post(
            "/biznav/extract",
            json={"project_name": "demo", "project_root": str(project_root)},
        )
        assert resp.status_code == 200

    for _ in range(20):
        events = await consume_biznav_events()
        if events:
            break
        await asyncio.sleep(0.05)

    done_events = [e for e in events if e[0] == EVT_EXTRACTION_DONE]
    assert len(done_events) == 1
    kind, payload = done_events[0]
    assert payload["success"] is False
    assert "simulated LLM crash" in payload["error"]