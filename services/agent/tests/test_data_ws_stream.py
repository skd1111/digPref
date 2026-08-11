"""Phase 7 补齐 · WS /data/stream/{task_id} Arrow 流测试（缺口 5）。

协议：
  首帧（text）: {"kind":"meta", columns, dtypes, row_count}
  中间帧（binary）: Arrow IPC 批（每批 ≤ ARROW_BATCH_ROWS 行，独立完整 IPC stream）
  末帧（text）: {"kind":"done", "done":true, "row_count"}
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pandas as pd
import pyarrow as pa
import pytest
from agent.config import settings
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_expert_db_path", str(tmp_path / "d.db"))
    monkeypatch.setattr(settings, "data_result_dir", str(tmp_path / "results"))
    monkeypatch.setattr(settings, "env", "prod")
    from agent.dataexpert.storage import reset_default_storage

    reset_default_storage()
    from agent.dataexpert.api import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c
    reset_default_storage()


def _run_large_query(client) -> str:
    """执行一条 600 行查询，返回 task_id。"""
    df = pd.DataFrame({"n": list(range(600))})
    with patch("agent.dataexpert.api.ReadOnlyPool") as pool_cls:
        pool_cls.return_value.execute_sql = AsyncMock(return_value=df)
        pool_cls.return_value.close = AsyncMock()
        r = client.post(
            "/data/sql/run",
            json={
                "sql": "SELECT n FROM t WHERE n >= 0",
                "connection": {"type": "sqlite", "path": ":memory:"},
            },
        )
    return r.json()["task_id"]


def test_ws_stream_roundtrip(client):
    task_id = _run_large_query(client)
    with client.websocket_connect(f"/data/stream/{task_id}") as ws:
        meta = json.loads(ws.receive_text())
        assert meta["kind"] == "meta"
        assert meta["columns"] == ["n"]
        assert meta["row_count"] == 600

        total = 0
        while True:
            msg = ws.receive()
            if msg.get("text") is not None:
                end = json.loads(msg["text"])
                assert end["done"] is True
                assert end["row_count"] == 600
                break
            # 每帧是独立完整的 IPC stream（含 schema）
            reader = pa.ipc.open_stream(pa.BufferReader(msg["bytes"]))
            total += sum(b.num_rows for b in reader)
        assert total == 600


def test_ws_stream_unknown_task_4404(client):
    """未知 task → 服务端关闭连接，关闭码 4404。"""
    msg: dict | None = None
    try:
        with client.websocket_connect("/data/stream/nonexistent") as ws:
            msg = ws.receive()
    except WebSocketDisconnect as e:
        assert e.code == 4404
        return
    # TestClient 部分版本将关闭帧作为 close/disconnect 消息返回（不抛异常）
    assert msg is not None and msg.get("type") in ("websocket.close", "websocket.disconnect")
    assert msg.get("code") == 4404


def test_export_by_task_id(client):
    """缺口 5：导出按 task_id 服务端取数（前端不传 rows）。"""
    task_id = _run_large_query(client)
    r = client.post("/data/export/csv", json={"task_id": task_id, "title": "测试"})
    body = r.json()
    assert body["row_count"] == 600
    assert body.get("path")
    assert body.get("md5")


def test_export_unknown_task_404(client):
    r = client.post("/data/export/csv", json={"task_id": "nope"})
    assert r.status_code == 404


def test_export_without_anything_400(client):
    r = client.post("/data/export/csv", json={})
    assert r.status_code == 400
