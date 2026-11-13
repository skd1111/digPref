"""test_ops_tasks.py —— 业务办理台账（业务汇总）路由测试（2026-11-09）。

覆盖：发起建档（上传第一份材料即建档）→ 审核打回自动转「人工确认」→
人工改判后回到「正在办理」→ 人工挂起 / 恢复 → 导出后「结束」→ 多维查询。
"""

from __future__ import annotations

import base64
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

CASE_ID = "bank__ops_open"


@pytest.fixture
def client(tmp_path, monkeypatch):
    db = str(tmp_path / "ops.db")
    audit_db = str(tmp_path / "audit.sqlite")
    monkeypatch.setenv("EAIDE_OPS_DB", db)
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", audit_db)

    from agent.ops import api as ops_api

    ops_api._reset_storage_for_tests()
    ops_api._reset_case_storage_for_tests()
    ops_api._reset_task_storage_for_tests()
    app = FastAPI()
    app.include_router(ops_api.router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _stub_system_doc_audit(monkeypatch):
    """系统合规审核默认返空报告：台账用例只关心专家验收主链路。"""
    from agent.ops import api as ops_api

    async def _empty(file_name, text, *, basis="document", llm=None):
        return {
            "audited": True,
            "degraded": False,
            "error": "",
            "basis": "document",
            "doc_category": "other",
            "risk_types": [],
            "overall_risk_level": "",
            "findings": [],
            "elapsed_ms": 0,
            "audited_at": 0,
        }

    monkeypatch.setattr(ops_api, "run_doc_audit", _empty)


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _upload(client: TestClient, name: str = "营业执照.txt") -> dict:
    r = client.post(
        "/ops/case/files",
        json={
            "case_id": CASE_ID,
            "team_id": "due_diligence_team",
            "member_key": "客户身份识别专家",
            "file_name": name,
            "content_base64": _b64("统一社会信用代码 91310000XXXX"),
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def _start_task(client: TestClient, **overrides) -> dict:
    body = {
        "case_id": CASE_ID,
        "project_name": "bank",
        "feature_id": "ops_open",
        "feature_label": "对公开户辅助",
        "category": "corp_account",
        "category_label": "对公账户管理",
        "title": "对公开户辅助",
        "operator": "客户经理张三",
    }
    body.update(overrides)
    r = client.post("/ops/tasks/start", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _mock_llm(monkeypatch, reply: str):
    from agent.ops import api as ops_api

    async def fake(_messages):
        return reply

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: fake)


def _task_of(client: TestClient, case_id: str = CASE_ID) -> dict | None:
    r = client.get("/ops/tasks", params={"case_id": case_id})
    assert r.status_code == 200, r.text
    rows = r.json()["tasks"]
    return rows[0] if rows else None


def test_first_material_creates_task_with_labels(client):
    """业务发起（上传第一份材料）即建档，带业务大类 / 小类标签。"""
    task = _start_task(client)
    assert task["status"] == "in_progress"
    assert task["category_label"] == "对公账户管理"
    assert task["feature_label"] == "对公开户辅助"

    _upload(client)

    rows = client.get("/ops/tasks").json()["tasks"]
    assert len(rows) == 1, "一条业务只应有一行台账"
    assert rows[0]["status"] == "in_progress"
    assert rows[0]["operator"] == "客户经理张三"


def test_upload_without_start_still_creates_task(client):
    """兜底：即便前端建档调用失败，上传材料也保证业务进了汇总。"""
    assert _task_of(client) is None
    _upload(client)
    task = _task_of(client)
    assert task is not None
    assert task["status"] == "in_progress"


def test_start_is_idempotent(client):
    """重复建档不产生第二行。"""
    first = _start_task(client)
    second = _start_task(client)
    assert first["id"] == second["id"]
    assert len(client.get("/ops/tasks").json()["tasks"]) == 1


@pytest.mark.asyncio
async def test_rejected_material_moves_task_to_manual_review(client, monkeypatch):
    """专家打回 → 系统自动把业务转「人工确认」，并列出待处理项。"""
    _start_task(client)
    row = _upload(client)
    _mock_llm(
        monkeypatch,
        '{"verdict": "rejected", "note": "执照已过有效期", "fields": [], "evidence": []}',
    )
    r = client.post(f"/ops/case/files/{row['id']}/review")
    assert r.status_code == 200, r.text

    task = _task_of(client)
    assert task is not None
    assert task["status"] == "manual_review"
    assert task["status_label"] == "人工确认"
    assert len(task["pending_items"]) == 1
    assert task["pending_items"][0]["file_name"] == "营业执照.txt"


@pytest.mark.asyncio
async def test_review_failure_moves_task_to_manual_review(client, monkeypatch):
    """审核异常（模型不可用）同样转「人工确认」——需要人工接入。"""
    from agent.ops import api as ops_api

    _start_task(client)
    row = _upload(client)

    async def broken(_messages):
        raise RuntimeError("三级模型均不可用")

    monkeypatch.setattr(ops_api, "_make_summarize_llm", lambda: broken)
    r = client.post(f"/ops/case/files/{row['id']}/review")
    assert r.status_code == 502, r.text

    task = _task_of(client)
    assert task is not None
    assert task["status"] == "manual_review"
    assert len(task["pending_items"]) == 1


@pytest.mark.asyncio
async def test_override_returns_task_to_in_progress(client, monkeypatch):
    """人工改判通过 → 待处理项清空 → 回到「正在办理」。"""
    _start_task(client)
    row = _upload(client)
    _mock_llm(
        monkeypatch,
        '{"verdict": "rejected", "note": "执照已过有效期", "fields": [], "evidence": []}',
    )
    client.post(f"/ops/case/files/{row['id']}/review")
    assert _task_of(client)["status"] == "manual_review"

    r = client.post(
        f"/ops/case/files/{row['id']}/override",
        json={"status": "passed", "note": "人工确认通过"},
    )
    assert r.status_code == 200, r.text

    task = _task_of(client)
    assert task["status"] == "in_progress"
    assert task["pending_items"] == []


def test_suspend_and_resume(client):
    """人工挂起 → 界面冻结（Case 接口带回挂起状态）；恢复 → 回到办理中。"""
    _start_task(client)
    _upload(client)
    task = _task_of(client)

    r = client.post(
        f"/ops/tasks/{task['id']}/status", json={"action": "suspend", "note": "等客户补件"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "suspended"
    assert r.json()["status_label"] == "挂起"

    # Case 接口带出台账状态 → 前端据此冻结界面
    case = client.get("/ops/case", params={"project_name": "bank", "feature_id": "ops_open"}).json()
    assert case["task"]["status"] == "suspended"

    r = client.post(f"/ops/tasks/{task['id']}/status", json={"action": "resume"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "in_progress"


def test_suspended_task_keeps_status_on_review(client):
    """挂起期间系统不自动改状态（人工优先）。"""
    _start_task(client)
    _upload(client)
    task = _task_of(client)
    client.post(f"/ops/tasks/{task['id']}/status", json={"action": "suspend"})

    # 审核打回本应转人工确认，但挂起优先
    client.post(
        f"/ops/case/files/{client.get('/ops/case', params={'case_id': CASE_ID}).json()['files'][0]['id']}/override",
        json={"status": "rejected", "note": "有问题"},
    )
    assert _task_of(client)["status"] == "suspended"


def test_invalid_action_rejected(client):
    _start_task(client)
    task = _task_of(client)
    r = client.post(f"/ops/tasks/{task['id']}/status", json={"action": "fly"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_export_closes_task(client, tmp_path, monkeypatch):
    """导出交付物 → 业务「结束」。"""
    _mock_llm(monkeypatch, "资料齐全，验收通过。")
    _start_task(client)
    row = _upload(client)
    client.post(f"/ops/case/files/{row['id']}/override", json={"status": "passed", "note": "ok"})

    target = tmp_path / "out" / "交付物.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    r = client.post(
        "/ops/case/export",
        json={
            "case_id": CASE_ID,
            "target_path": str(target),
            "feature_name": "对公开户辅助",
            "team_name": "尽职调查专家团",
        },
    )
    assert r.status_code == 200, r.text

    task = _task_of(client)
    assert task["status"] == "closed"
    assert task["status_label"] == "结束"
    assert task["closed_at"] > 0


@pytest.mark.asyncio
async def test_new_material_reopens_closed_task(client, tmp_path, monkeypatch):
    """已结束的业务补了新材料 → 重新进入「正在办理」。"""
    _mock_llm(monkeypatch, "资料齐全，验收通过。")
    _start_task(client)
    row = _upload(client)
    client.post(f"/ops/case/files/{row['id']}/override", json={"status": "passed", "note": "ok"})
    target = tmp_path / "out" / "交付物.zip"
    target.parent.mkdir(parents=True, exist_ok=True)
    client.post(
        "/ops/case/export",
        json={"case_id": CASE_ID, "target_path": str(target), "feature_name": "对公开户辅助"},
    )
    assert _task_of(client)["status"] == "closed"

    _upload(client, name="补充材料.txt")
    task = _task_of(client)
    assert task["status"] == "in_progress"
    assert task["closed_at"] == 0


def test_list_tasks_multi_dimension_filters(client):
    """多维查询：大类 / 小类 / 状态 / 关键词 / 日期区间 + 状态计数。"""
    _start_task(client)
    _start_task(
        client,
        case_id="bank__corp_close",
        feature_id="corp_close",
        feature_label="对公销户辅助",
        title="对公销户辅助",
    )
    _start_task(
        client,
        case_id="bank__aml_task",
        feature_id="aml_tasks",
        feature_label="预警任务列表",
        category="aml",
        category_label="反洗钱辅助分析",
        title="预警任务列表",
    )
    # 一笔挂起
    first = _task_of(client)
    client.post(f"/ops/tasks/{first['id']}/status", json={"action": "suspend"})

    body = client.get("/ops/tasks").json()
    assert body["total"] == 3
    assert body["counts"]["suspended"] == 1
    assert body["counts"]["in_progress"] == 2

    # 按大类
    rows = client.get("/ops/tasks", params={"category": "aml"}).json()["tasks"]
    assert len(rows) == 1
    assert rows[0]["category_label"] == "反洗钱辅助分析"

    # 按小类
    rows = client.get("/ops/tasks", params={"feature_id": "corp_close"}).json()["tasks"]
    assert len(rows) == 1
    assert rows[0]["feature_label"] == "对公销户辅助"

    # 按状态（多状态逗号分隔）
    rows = client.get("/ops/tasks", params={"status": "suspended,in_progress"}).json()["tasks"]
    assert len(rows) == 3
    rows = client.get("/ops/tasks", params={"status": "closed"}).json()["tasks"]
    assert rows == []

    # 按关键词
    rows = client.get("/ops/tasks", params={"keyword": "销户"}).json()["tasks"]
    assert len(rows) == 1

    # 按日期区间（发起时间在区间内 / 区间外）
    now = int(time.time())
    rows = client.get("/ops/tasks", params={"date_from": now - 60, "date_to": now + 60}).json()[
        "tasks"
    ]
    assert len(rows) == 3
    rows = client.get("/ops/tasks", params={"date_from": now + 3600}).json()["tasks"]
    assert rows == []


def test_task_detail_lists_pending_items(client):
    """详情：待处理项 + 材料进度。"""
    _start_task(client)
    _upload(client)
    task = _task_of(client)
    client.post(f"/ops/case/files/{task['id']}/status", json={"action": "suspend"})

    r = client.get(f"/ops/tasks/{task['id']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task"]["id"] == task["id"]
    assert body["file_total"] == 1
    assert body["file_stats"].get("pending") == 1
