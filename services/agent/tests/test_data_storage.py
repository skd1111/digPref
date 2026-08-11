"""Phase 7 V0 · 存储测试 —— data_expert.db CRUD + Parquet 落盘/读回。

验收硬门槛（design §11）：
  - data_expert.db 物理隔离（与 audit/router/knowledge 独立）
  - 结果集大对象走 Parquet 文件（不塞进 SQLite）
"""

import time
from pathlib import Path

import pytest
from agent.dataexpert.storage import DataExpertStorage


@pytest.fixture
def storage(tmp_path):
    """临时数据库实例。"""
    db_path = str(tmp_path / "test_data_expert.db")
    return DataExpertStorage(db_path=db_path)


# ---- data_sources CRUD ---------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_get_source(storage):
    """upsert + get 数据源。"""
    await storage.upsert_source(
        source_id="ds_001",
        name="核心账务",
        source_type="mysql",
        connection_ref="keyring://core-db",
        schema_cache=[{"name": "t_account", "columns": []}],
        updated_at=int(time.time()),
    )
    src = await storage.get_source("ds_001")
    assert src is not None
    assert src["name"] == "核心账务"
    assert src["type"] == "mysql"
    assert src["connection_ref"] == "keyring://core-db"
    assert len(src["schema_cache"]) == 1


@pytest.mark.asyncio
async def test_upsert_source_conflict_update(storage):
    """upsert 冲突时更新。"""
    await storage.upsert_source(source_id="ds_001", name="旧名", source_type="mysql")
    await storage.upsert_source(source_id="ds_001", name="新名", source_type="oracle")
    src = await storage.get_source("ds_001")
    assert src["name"] == "新名"
    assert src["type"] == "oracle"


@pytest.mark.asyncio
async def test_list_sources(storage):
    """list_sources 返回所有数据源。"""
    await storage.upsert_source(source_id="ds_a", name="A", source_type="mysql")
    await storage.upsert_source(source_id="ds_b", name="B", source_type="csv")
    sources = await storage.list_sources()
    assert len(sources) == 2


@pytest.mark.asyncio
async def test_get_source_not_found(storage):
    """不存在的数据源返回 None。"""
    src = await storage.get_source("nonexist")
    assert src is None


# ---- analysis_tasks CRUD -------------------------------------------------------


@pytest.mark.asyncio
async def test_insert_and_get_task(storage):
    """insert + get 分析任务。"""
    await storage.insert_task(
        task_id="task_001",
        name="月度坏账率",
        user_id="user_1",
        query_sql="SELECT * FROM t_loan",
        result_metadata={"columns": ["id", "amount"], "row_count": 100},
        result_data_ref="/tmp/result.parquet",
        chart_config={"type": "bar"},
        created_at=int(time.time()),
    )
    task = await storage.get_task("task_001")
    assert task is not None
    assert task["name"] == "月度坏账率"
    assert task["user_id"] == "user_1"
    assert task["result_metadata"]["row_count"] == 100
    assert task["chart_config"]["type"] == "bar"


@pytest.mark.asyncio
async def test_list_tasks_by_user(storage):
    """list_tasks 按 user_id 过滤。"""
    await storage.insert_task(task_id="t1", name="T1", user_id="u1", created_at=1)
    await storage.insert_task(task_id="t2", name="T2", user_id="u2", created_at=2)
    await storage.insert_task(task_id="t3", name="T3", user_id="u1", created_at=3)
    tasks = await storage.list_tasks(user_id="u1")
    assert len(tasks) == 2
    assert all(t["user_id"] == "u1" for t in tasks)


@pytest.mark.asyncio
async def test_list_tasks_limit(storage):
    """list_tasks 限制返回数。"""
    for i in range(10):
        await storage.insert_task(task_id=f"t{i}", name=f"T{i}", user_id="u", created_at=i)
    tasks = await storage.list_tasks(limit=5)
    assert len(tasks) == 5


@pytest.mark.asyncio
async def test_get_task_not_found(storage):
    """不存在的任务返回 None。"""
    task = await storage.get_task("nonexist")
    assert task is None


# ---- report_templates CRUD -----------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_list_template(storage):
    """upsert + list 报表模板。"""
    await storage.upsert_template(
        template_id="tpl_001",
        name="月度报表",
        description="每月自动生成",
        task_id="task_001",
        export_format="excel",
        created_by="admin",
        is_public=True,
    )
    templates = await storage.list_templates()
    assert len(templates) == 1
    assert templates[0]["name"] == "月度报表"
    assert templates[0]["is_public"] == 1


@pytest.mark.asyncio
async def test_upsert_template_conflict(storage):
    """模板 upsert 冲突时更新。"""
    await storage.upsert_template(template_id="tpl_1", name="旧", task_id="t1")
    await storage.upsert_template(template_id="tpl_1", name="新", task_id="t2")
    templates = await storage.list_templates()
    assert len(templates) == 1
    assert templates[0]["name"] == "新"


# ---- Parquet 落盘 / 读回 --------------------------------------------------------


@pytest.mark.asyncio
async def test_parquet_save_and_load(tmp_path, monkeypatch):
    """Parquet 落盘后能正确读回。"""
    pytest.importorskip("pandas")
    import importlib.util

    # pandas 可能来自 config/driver/_site（driver_bootstrap 注入），但未必带
    # parquet 引擎 —— pyarrow / fastparquet 都没有时跳过（BUGFIX #706 建议修法）
    if (
        importlib.util.find_spec("pyarrow") is None
        and importlib.util.find_spec("fastparquet") is None
    ):
        pytest.skip("parquet engine not installed (need pyarrow or fastparquet)")
    import pandas as pd
    from agent.dataexpert.storage import load_result_parquet, save_result_parquet

    # 临时修改 settings.data_result_dir
    monkeypatch.setattr("agent.dataexpert.storage.settings.data_result_dir", str(tmp_path))

    df = pd.DataFrame({"id": [1, 2, 3], "amount": [100.5, 200.0, -50.3]})
    path = save_result_parquet(df, "task_pq_001")

    assert Path(path).exists()
    assert path.endswith(".parquet")

    df_loaded = load_result_parquet(path)
    assert len(df_loaded) == 3
    assert list(df_loaded.columns) == ["id", "amount"]
    assert df_loaded["amount"].iloc[2] == pytest.approx(-50.3)


# ---- 物理隔离验证 ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_physical_isolation(tmp_path):
    """data_expert.db 是独立文件（物理隔离）。"""
    db_path = str(tmp_path / "isolated" / "data_expert.db")
    st = DataExpertStorage(db_path=db_path)
    await st.upsert_source(source_id="x", name="X", source_type="csv")
    assert Path(db_path).exists()
    # 确认是独立的 SQLite 文件
    assert Path(db_path).stat().st_size > 0
