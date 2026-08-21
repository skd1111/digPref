"""Phase 7 补齐 · sql/run 真实链路测试（缺口 1/4/10）。

覆盖：
  - SELECT 白名单第一层（DELETE 等非 SELECT → 403）
  - 重查询 HITL：多表 JOIN 未确认 → needs_confirm（不执行）
  - confirmed=true 后真实执行（mock ReadOnlyPool）
  - 结果落 analysis_tasks（/data/tasks 可查）
  - 大小结果分流：≤500 行内联；>500 行落 Parquet + stream_ref
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pandas as pd
import pytest
from agent.config import settings
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_expert_db_path", str(tmp_path / "d.db"))
    monkeypatch.setattr(settings, "data_result_dir", str(tmp_path / "results"))
    monkeypatch.setattr(settings, "env", "prod")
    monkeypatch.setattr(settings, "data_allow_non_select_in_dev", False)
    from agent.dataexpert.storage import reset_default_storage

    reset_default_storage()
    from agent.dataexpert.api import router

    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as c:
        yield c
    reset_default_storage()


def test_run_sql_write_blocked(client):
    """写操作 → 403（白名单/黑名单双层）。"""
    r = client.post("/data/sql/run", json={"sql": "DELETE FROM t"})
    assert r.status_code == 403


def test_run_sql_non_select_blocked(client):
    """缺口 10：非 SELECT（SET）→ 403。"""
    r = client.post("/data/sql/run", json={"sql": "SET @x = 1"})
    assert r.status_code == 403


def test_run_sql_needs_confirm(client):
    """多表 JOIN 未确认 → needs_confirm，不执行。"""
    r = client.post(
        "/data/sql/run",
        json={"sql": "SELECT a.x, b.y FROM a JOIN b ON a.id=b.id WHERE a.x>0"},
    )
    body = r.json()
    assert body["needs_confirm"] is True


def test_run_sql_missing_connection(client):
    """无 connection 且无 source_id → 400（BUGFIX #97）。

    Rust Tauri 端应在此之前 fail-fast，把更可操作的错误透传给用户
    （参见 ``apps/desktop/src-tauri/src/commands/dataexpert.rs::data_run_sql``）。
    本测试守住后端契约：源信息缺失时返回 400。
    """
    r = client.post("/data/sql/run", json={"sql": "SELECT * FROM t WHERE id=1"})
    assert r.status_code == 400
    # 错误文案要可定位：用户看到后知道是「没传连接」而不是「DB 抛错」
    detail = r.json()["detail"]
    assert "数据源" in detail or "connection" in detail or "source_id" in detail


def test_run_sql_empty_source_id_no_connection(client):
    """BUGFIX #97 + #52：source_id=""  + connection={} → 400 且 detail 可读。

    Tauri 端 ``data_run_sql`` 在 source_id 为空时会传 ``{"connection": {}, "source_id": ""}``
    走到后端；后端契约保证此时返回 400，避免静默返回 0 行假阳性。
    """
    r = client.post(
        "/data/sql/run",
        json={"sql": "SELECT * FROM t WHERE id=1", "source_id": "", "connection": {}},
    )
    assert r.status_code == 400
    assert "缺少" in r.json()["detail"] or "connection" in r.json()["detail"]


def test_run_sql_executes_and_persists(client):
    """真实执行 + 任务落库。"""
    df = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    with patch("agent.dataexpert.api.ReadOnlyPool") as pool_cls:
        pool_cls.return_value.execute_sql = AsyncMock(return_value=df)
        pool_cls.return_value.close = AsyncMock()
        r = client.post(
            "/data/sql/run",
            json={
                "sql": "SELECT * FROM t WHERE id=1",
                "connection": {"type": "sqlite", "path": ":memory:"},
            },
        )
    body = r.json()
    assert body["ok"] is True
    assert body["row_count"] == 2
    assert body["columns"] == ["id", "name"]
    assert body["rows"] == [[1, "a"], [2, "b"]]
    assert body["task_id"]
    assert body["stream_ref"] == ""  # 小结果内联

    t = client.get("/data/tasks").json()
    assert t["count"] == 1
    assert t["tasks"][0]["query_sql"].startswith("SELECT")


def test_run_sql_large_result_goes_parquet(client):
    """>500 行 → Parquet + stream_ref，rows 为空。"""
    df = pd.DataFrame({"n": list(range(600))})
    with patch("agent.dataexpert.api.ReadOnlyPool") as pool_cls:
        pool_cls.return_value.execute_sql = AsyncMock(return_value=df)
        pool_cls.return_value.close = AsyncMock()
        r = client.post(
            "/data/sql/run",
            json={
                "sql": "SELECT n FROM t WHERE n>0",
                "connection": {"type": "sqlite", "path": ":memory:"},
            },
        )
    body = r.json()
    assert body["rows"] == []
    assert body["result_data_ref"]
    assert body["stream_ref"] == f"/data/stream/{body['task_id']}"


def test_run_sql_confirmed_heavy_executes(client):
    """confirmed=true 的重查询放行执行。"""
    df = pd.DataFrame({"x": [1]})
    with patch("agent.dataexpert.api.ReadOnlyPool") as pool_cls:
        pool_cls.return_value.execute_sql = AsyncMock(return_value=df)
        pool_cls.return_value.close = AsyncMock()
        r = client.post(
            "/data/sql/run",
            json={
                "sql": "SELECT a.x, b.y FROM a JOIN b ON a.id=b.id WHERE a.x>0",
                "confirmed": True,
                "connection": {"type": "sqlite", "path": ":memory:"},
            },
        )
    assert r.json()["ok"] is True


def test_nl2sql_rejects_non_select_generated(client):
    """缺口 10：LLM 生成非 SELECT → 丢弃不下发（sql 为空 + error 说明）。"""
    with patch(
        "agent.dataexpert.nl2sql.generator.to_sql",
        new=AsyncMock(return_value="DROP TABLE t_order"),
    ):
        r = client.post("/data/nl2sql", json={"question": "删表"})
    body = r.json()
    assert body["sql"] == ""
    assert "拒绝" in body["error"]


# ---- BUGFIX #128：NL2SQL 真接 LMRouter，失败占位不得冒充「已生成」 --------


def test_nl2sql_generates_real_sql_via_router(client):
    """模型可用 → 真生成 SQL（剥围栏后下发），不再是 V0 占位。"""
    with patch(
        "agent.llm.router.LMRouter.generate_raw",
        new=AsyncMock(
            return_value="好的，以下是查询：\n```sql\nSELECT COUNT(*) FROM sm_process_tb;\n```"
        ),
    ):
        r = client.post("/data/nl2sql", json={"question": "查询有哪些流程"})
    body = r.json()
    assert body["sql"] == "SELECT COUNT(*) FROM sm_process_tb;"
    assert body["error"] == ""


def test_nl2sql_router_unavailable_returns_error_not_placeholder(client):
    """全链模型不可用 → sql 为空 + error 说明，前端展示 ❌ 而非假「已生成」。"""
    with patch(
        "agent.llm.router.LMRouter.generate_raw",
        new=AsyncMock(side_effect=RuntimeError("ollama: connection refused")),
    ):
        r = client.post("/data/nl2sql", json={"question": "查询有哪些流程"})
    body = r.json()
    assert body["sql"] == ""
    assert "生成失败" in body["error"]


# ---- V2 接线：few-shot 飞轮 + 向量 schema 链接真传到生成器 ----------------


def test_nl2sql_injects_few_shot_from_history(client):
    """历史已确认 SQL（analysis_tasks）自动注入生成 few-shot（Vanna 飞轮）。"""
    import asyncio

    from agent.dataexpert.storage import get_default_storage

    async def _seed() -> None:
        await get_default_storage().insert_task(
            task_id="t-hist-1",
            name="查询流程总数",
            user_id="u1",
            query_sql="SELECT COUNT(*) FROM sm_process_tb;",
            created_at=1,
        )

    asyncio.run(_seed())

    with patch(
        "agent.dataexpert.nl2sql.generator.to_sql",
        new=AsyncMock(return_value="SELECT COUNT(*) FROM sm_process_tb;"),
    ) as mock_to_sql:
        r = client.post("/data/nl2sql", json={"question": "流程有多少"})
    assert r.status_code == 200
    few_shot = mock_to_sql.call_args.kwargs["few_shot"]
    assert len(few_shot) == 1
    assert few_shot[0].sql == "SELECT COUNT(*) FROM sm_process_tb;"
    # llm_router 也必须真传（BUGFIX #128 回归守护）
    assert mock_to_sql.call_args.kwargs["llm_router"] is not None


def test_nl2sql_few_shot_failure_does_not_block(client):
    """few-shot 是增强项：选取异常不阻断主链路（降级纯 NL2SQL）。"""
    with (
        patch(
            "agent.dataexpert.nl2sql.linker.select_few_shot",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch(
            "agent.llm.router.LMRouter.generate_raw",
            new=AsyncMock(return_value="SELECT COUNT(*) FROM sm_process_tb;"),
        ),
    ):
        r = client.post("/data/nl2sql", json={"question": "流程有多少"})
    assert r.status_code == 200
    assert r.json()["sql"] == "SELECT COUNT(*) FROM sm_process_tb;"


# ---- BUGFIX #97：数据源类型缺失不得静默返回 0 行 --------------------------


def test_run_sql_empty_type_returns_400(client):
    """Rust 注入 type=""（资产未配 db_type）→ 400 明确提示，不返回空结果。"""
    r = client.post(
        "/data/sql/run",
        json={
            "sql": "SELECT * FROM t WHERE id=1",
            "connection": {"type": "", "host": "127.0.0.1"},
        },
    )
    assert r.status_code == 400
    assert "db_type" in r.json()["detail"]


def test_run_sql_unknown_type_returns_400(client):
    """未知类型 → pool 快速失败 → 400（不会静默走兜底）。"""
    r = client.post(
        "/data/sql/run",
        json={
            "sql": "SELECT * FROM t WHERE id=1",
            "connection": {"type": "mongodb"},
        },
    )
    assert r.status_code == 400
    assert "数据源类型" in r.json()["detail"]


async def test_pool_empty_type_raises():
    """ReadOnlyPool：type 空串/缺失/未知 → ValueError（fail-fast）。"""
    from agent.dataexpert.readonly.pool import ReadOnlyPool

    for cfg in ({"type": ""}, {}, {"type": "mongodb"}):
        with pytest.raises(ValueError, match="数据源类型"):
            await ReadOnlyPool(cfg).execute_sql("SELECT 1")


# ---- BUGFIX #126：资产型数据源未登记时 schema 同步不得 404 ------------------


def test_sync_schema_unregistered_source_with_injected_connection(client):
    """systems.yaml 资产源没登记进 data_expert.db，只要 Rust 注入了连接就能同步并登记。"""
    tables = [
        {
            "name": "sm_scene_link_tb",
            "comment": "",
            "columns": [{"name": "id", "dtype": "int", "comment": ""}],
        }
    ]
    with patch("agent.dataexpert.api.ReadOnlyPool") as pool_cls:
        pool_cls.return_value.fetch_schema = AsyncMock(return_value=tables)
        pool_cls.return_value.close = AsyncMock()
        r = client.post(
            "/data/sources/asset-172/sync",
            json={"connection": {"type": "mysql", "host": "172.1.1.96"}},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["tables_synced"] == 1
    # 顺手登记进 data_sources（后续 list_sources 可见，schema_cache 非空）
    srcs = client.get("/data/sources").json()["sources"]
    assert any(s["id"] == "asset-172" and s["schema_cache"] for s in srcs)


def test_sync_schema_unregistered_source_without_connection_404(client):
    """未登记且无注入连接 → 仍 404（原契约不变）。"""
    r = client.post("/data/sources/no-such-source/sync", json={})
    assert r.status_code == 404
