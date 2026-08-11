"""Phase 18 审计留痕：AUTO_MODE_ENABLED / MODE_ROUTED / AUTO_MODE_DECISION。"""

from __future__ import annotations

import json

import aiosqlite
import pytest
from agent.api.autonomy import router as autonomy_router
from agent.audit.store import audit
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def app():
    a = FastAPI()
    a.include_router(autonomy_router)
    return a


async def _read_events(db_path: str) -> list[tuple[str, dict]]:
    async with aiosqlite.connect(db_path) as db:
        rows = await db.execute_fetchall("SELECT action, payload FROM audit")
    return [(r[0], json.loads(r[1])) for r in rows]


async def test_autonomy_confirm_writes_audit(app, tmp_path, monkeypatch):
    from agent.config import settings

    db_path = str(tmp_path / "audit.sqlite")
    monkeypatch.setattr(settings, "audit_db_path", db_path)

    client = TestClient(app)
    resp = client.post(
        "/autonomy/confirm",
        json={"sessionId": "sess-1", "consentVersion": "v1", "workMode": "full"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    events = await _read_events(db_path)
    actions = [a for a, _ in events]
    assert "AUTO_MODE_ENABLED" in actions
    payload = next(p for a, p in events if a == "AUTO_MODE_ENABLED")
    assert payload["session_id"] == "sess-1"
    assert payload["consent_version"] == "v1"


async def test_mode_routed_audit(tmp_path, monkeypatch):
    from agent.config import settings
    from agent.graph.nodes.mode_router import mode_router_node

    db_path = str(tmp_path / "audit.sqlite")
    monkeypatch.setattr(settings, "audit_db_path", db_path)

    state = {
        "user_prompt": "查询生产库昨天的订单量",
        "work_mode": "full",
        "run_id": "run-1",
    }
    out = await mode_router_node(state, llm=None)
    assert out["routing"] == "work"

    events = await _read_events(db_path)
    routed = [p for a, p in events if a == "MODE_ROUTED"]
    assert routed and routed[0]["routing"] == "work"
    assert routed[0]["overridden"] is True


async def test_auto_mode_decision_audit_shape(tmp_path):
    """AUTO_MODE_DECISION 事件字段完整性（经 audit() 直接写入验证 schema 兼容）。"""

    db_path = str(tmp_path / "audit.sqlite")
    await audit(
        "AUTO_MODE_DECISION",
        {
            "tool": "run_sql",
            "server": "database",
            "risk_level": "high",
            "action": "auto_select_recommended",
            "decided_by": "auto_mode",
            "autonomy": "auto",
            "work_mode": "operator",
            "recommendation_reason": "限定时间窗",
            "selected_option": "执行（限近 7 天）",
        },
        run_id="run-9",
        db_path=db_path,
    )
    events = await _read_events(db_path)
    assert any(a == "AUTO_MODE_DECISION" for a, _ in events)
