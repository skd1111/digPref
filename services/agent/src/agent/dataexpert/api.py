"""Phase 7 V0 · FastAPI /data/* 路由 —— 数据专家工作台后端。

端点（implementation/data-expert.md §2.4）：
  - GET  /data/sources              —— 数据源列表
  - POST /data/sources/{id}/sync    —— 同步 Schema 元数据
  - POST /data/nl2sql               —— 自然语言 → SQL（不执行，返回待确认 SQL + is_heavy）
  - POST /data/sql/run              —— 执行只读 SQL（guard + LIMIT + HITL）
  - POST /data/python/run           —— 沙箱执行 Python
  - POST /data/chart/recommend      —— 图表推荐（task='chart_reco' 本地）
  - POST /data/export/{fmt}         —— 导出（PII 脱敏 + 水印 + 审计）
  - POST /data/templates            —— 保存/更新报表模板
  - GET  /data/tasks                —— 历史分析任务列表
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.dataexpert.events import (
    EVT_DATA_CHART_READY,
    EVT_DATA_EXPORT_DONE,
    EVT_DATA_PYTHON_RESULT,
    EVT_DATA_QUERY_RESULT,
    emit_event_sync,
)
from agent.dataexpert.models import generate_id, now_epoch
from agent.dataexpert.readonly.guard import (
    WriteBlockedError,
    enforce_readonly,
    inject_limit,
    is_heavy,
)
from agent.dataexpert.storage import get_default_storage

router = APIRouter(prefix="/data", tags=["data-expert"])


# ---- Pydantic schemas -------------------------------------------------------

class NL2SQLRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2048)
    source_id: str = ""


class NL2SQLResponse(BaseModel):
    sql: str
    is_heavy: bool
    tables_used: list[str] = Field(default_factory=list)
    dictionary_context: str = ""


class SqlRunRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=8192)
    source_id: str = ""
    confirmed: bool = False  # HITL 确认（重查询需 true）


class PythonRunRequest(BaseModel):
    script: str = Field(min_length=1, max_length=16384)
    task_id: str = ""  # 关联上一步 SQL 结果


class ChartRecommendRequest(BaseModel):
    columns: list[str]
    dtypes: list[str]
    row_count: int = 0


class ExportRequest(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    title: str = "数据报表"


class TestConnectionRequest(BaseModel):
    """测试数据库连接请求体。"""
    db_type: str = Field(min_length=1, description="数据库类型")
    host: str = "127.0.0.1"
    port: int | None = None
    database: str = ""
    username: str = ""
    password: str = ""
    path: str = ""


class TemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = ""
    task_id: str = ""
    schedule_cron: str = ""
    export_format: str = "excel"
    is_public: bool = False


# ---- 端点 -------------------------------------------------------------------

@router.get("/sources")
async def list_sources() -> dict:
    """数据源列表。"""
    storage = get_default_storage()
    sources = await storage.list_sources()
    return {"sources": sources, "count": len(sources)}


@router.post("/sources/{source_id}/sync")
async def sync_schema(source_id: str) -> dict:
    """同步 Schema 元数据（V0：刷新 updated_at）。"""
    storage = get_default_storage()
    source = await storage.get_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail=f"source not found: {source_id}")
    # V0：仅更新时间戳（V1 真实连接 DB 拉 schema）
    await storage.upsert_source(
        source_id=source_id,
        name=source["name"],
        source_type=source["type"],
        connection_ref=source.get("connection_ref", ""),
        schema_cache=source.get("schema_cache", []),
        updated_at=now_epoch(),
    )
    return {"ok": True, "source_id": source_id, "synced_at": now_epoch()}


@router.post("/test_connection")
async def test_connection(req: TestConnectionRequest) -> dict:
    """测试数据库连接（支持主流 + 国产/信创数据库）。"""
    from agent.dataexpert.readonly.pool import DB_TYPE_REGISTRY, ReadOnlyPool

    port = req.port
    if port is None:
        port = DB_TYPE_REGISTRY.get(req.db_type, {}).get("port", 0)

    config = {
        "type": req.db_type,
        "host": req.host,
        "port": port,
        "database": req.database,
        "user": req.username,
        "username": req.username,
        "password": req.password,
        "path": req.path,
    }
    pool = ReadOnlyPool(config)
    try:
        result = await pool.test_connection()
        return result
    finally:
        await pool.close()


@router.post("/nl2sql", response_model=NL2SQLResponse)
async def nl2sql(req: NL2SQLRequest) -> NL2SQLResponse:
    """自然语言 → SQL（不执行，返回待确认 SQL + is_heavy）。"""
    from agent.dataexpert.nl2sql import dictionary, linker
    from agent.dataexpert.nl2sql.generator import to_sql

    storage = get_default_storage()

    # 获取数据源 schema
    schema_cache: list[dict] = []
    if req.source_id:
        source = await storage.get_source(req.source_id)
        if source:
            schema_cache = source.get("schema_cache", [])

    # Schema 链接（选 3-5 表）
    tables = await linker.select_tables(req.question, schema_cache)

    # 业务字典
    dict_ctx = dictionary.translate(req.question, req.source_id)

    # 生成 SQL
    sql = await to_sql(req.question, tables, dict_ctx)

    # 判断是否重查询
    heavy = is_heavy(sql)

    return NL2SQLResponse(
        sql=sql,
        is_heavy=heavy,
        tables_used=[t.name for t in tables],
        dictionary_context=dict_ctx,
    )


@router.post("/sql/run")
async def run_sql(req: SqlRunRequest) -> dict:
    """执行只读 SQL（guard + LIMIT + HITL）。"""
    # 安全层 1：写操作硬拦截
    try:
        enforce_readonly(req.sql)
    except WriteBlockedError as e:
        # 记 DATA_WRITE_BLOCKED 审计
        emit_event_sync("data_write_blocked", {
            "kind": "data_write_blocked",
            "sql": req.sql[:200],
            "token": e.token,
        })
        raise HTTPException(status_code=403, detail=str(e))

    # 安全层 2：重查询 HITL
    if is_heavy(req.sql) and not req.confirmed:
        return {
            "needs_confirm": True,
            "message": "检测到多表 JOIN / 全表扫描，请确认后重新提交（confirmed=true）",
            "sql": inject_limit(req.sql),
        }

    # 安全层 3：强制 LIMIT
    safe_sql = inject_limit(req.sql)

    # V0：模拟执行（真实实现走 ReadOnlyPool）
    start = time.perf_counter()
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # 记 DATA_SQL_RUN 审计
    emit_event_sync(EVT_DATA_QUERY_RESULT, {
        "kind": EVT_DATA_QUERY_RESULT,
        "sql": safe_sql[:500],
        "elapsed_ms": elapsed_ms,
        "row_count": 0,
    })

    return {
        "ok": True,
        "sql": safe_sql,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "elapsed_ms": elapsed_ms,
        "truncated": False,
    }


@router.post("/python/run")
async def run_python(req: PythonRunRequest) -> dict:
    """沙箱执行 Python。"""
    from agent.dataexpert.sandbox.executor import run

    result = await run(req.script)

    emit_event_sync(EVT_DATA_PYTHON_RESULT, {
        "kind": EVT_DATA_PYTHON_RESULT,
        "ok": result.ok,
        "elapsed_s": result.elapsed_s,
        "error": result.error[:500] if result.error else "",
    })

    return {
        "ok": result.ok,
        "out_df_ref": result.out_df_ref,
        "stdout": result.stdout[:2000],
        "error": result.error[:2000],
        "elapsed_s": result.elapsed_s,
    }


@router.post("/chart/recommend")
async def chart_recommend(req: ChartRecommendRequest) -> dict:
    """图表推荐（task='chart_reco' 本地）。"""
    from agent.dataexpert.viz.recommender import recommend_chart

    reco = recommend_chart(req.columns, req.dtypes, req.row_count)

    emit_event_sync(EVT_DATA_CHART_READY, {
        "kind": EVT_DATA_CHART_READY,
        "chart_type": reco["chart_type"],
        "reason": reco["reason"],
    })

    return reco


@router.post("/export/{fmt}")
async def export_data(fmt: str, req: ExportRequest) -> dict:
    """导出（PII 脱敏 + 水印 + 审计）。"""
    if fmt == "excel":
        from agent.dataexpert.export.excel import export_excel
        meta = export_excel(req.columns, req.rows, title=req.title)
    elif fmt == "pdf":
        from agent.dataexpert.export.pdf import export_pdf
        meta = export_pdf(req.columns, req.rows, title=req.title)
    elif fmt == "csv":
        from agent.dataexpert.export.csv import export_csv
        meta = export_csv(req.columns, req.rows, title=req.title)
    else:
        raise HTTPException(status_code=400, detail=f"unsupported format: {fmt}")

    # 记 DATA_EXPORT 审计
    emit_event_sync(EVT_DATA_EXPORT_DONE, {
        "kind": EVT_DATA_EXPORT_DONE,
        "format": fmt,
        "row_count": meta.get("row_count", 0),
        "md5": meta.get("md5", ""),
        "path": meta.get("path", ""),
    })

    return meta


@router.post("/templates")
async def save_template(req: TemplateRequest) -> dict:
    """保存/更新报表模板。"""
    storage = get_default_storage()
    template_id = generate_id()
    await storage.upsert_template(
        template_id=template_id,
        name=req.name,
        description=req.description,
        task_id=req.task_id,
        schedule_cron=req.schedule_cron,
        export_format=req.export_format,
        is_public=req.is_public,
    )
    return {"ok": True, "template_id": template_id}


@router.get("/tasks")
async def list_tasks(user_id: str | None = None, limit: int = 50) -> dict:
    """历史分析任务列表。"""
    storage = get_default_storage()
    tasks = await storage.list_tasks(user_id=user_id, limit=min(limit, 200))
    return {"tasks": tasks, "count": len(tasks)}
