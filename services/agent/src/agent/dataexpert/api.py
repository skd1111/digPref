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

import json
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket
from pydantic import BaseModel, Field

from agent.dataexpert.events import (
    EVT_DATA_CHART_READY,
    EVT_DATA_EXPORT_DONE,
    EVT_DATA_PYTHON_RESULT,
    EVT_DATA_QUERY_RESULT,
    emit_event_sync,
)
from agent.dataexpert.metric_resolver import (
    MetricResolverConfigError,
    get_default_resolver,
)
from agent.dataexpert.models import generate_id, now_epoch
from agent.dataexpert.readonly.guard import (
    WriteBlockedError,
    enforce_readonly,
    enforce_select_only,
    inject_limit,
    is_heavy,
)
from agent.dataexpert.readonly.pool import ReadOnlyPool
from agent.dataexpert.storage import get_default_storage, load_result_parquet, save_result_parquet

router = APIRouter(prefix="/data", tags=["data-expert"])

# 内联阈值：超过此行数结果落 Parquet + WS Arrow 流，不进 HTTP JSON（设计红线）
ROW_INLINE_MAX = 500
# WS Arrow 流每批行数
ARROW_BATCH_ROWS = 5000


# ---- Pydantic schemas -------------------------------------------------------


class NL2SQLRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2048)
    source_id: str = ""


class NL2SQLResponse(BaseModel):
    sql: str
    is_heavy: bool
    tables_used: list[str] = Field(default_factory=list)
    dictionary_context: str = ""
    error: str = ""  # 生成 SQL 未通过白名单时的降级说明
    # v2.87 MetricResolver 透传：让前端 DataWorkbench 状态栏显示当前 resolver 类型
    metric_source_kind: str = ""  # "dict" / "platform" / "bridge" / ""（未识别）
    metric_confidence: float = 0.0  # 0-1


class MetricResolveRequest(BaseModel):
    """v2.87 · MetricResolver.resolve() 入参。

    用于前端主动识别指标（譬如"识别这些问句对应的指标给提示"），
    或 NL2SQL 前置增强（NL2SQLResponse.metric_source_kind 来源）。
    """

    question: str = Field(min_length=1, max_length=2048)
    source_id: str = ""


class MetricResolveResponse(BaseModel):
    """v2.87 · MetricResolver.resolve() 出参。

    ``resolved`` 为 None 表示识别失败（前端可回退到纯 NL2SQL）。
    """

    resolved: dict | None = None  # ResolvedQuery JSON（None 表示识别失败）
    error: str = ""  # 配置错误或实现未就绪时的兜底说明


class MetricListResponse(BaseModel):
    """v2.87 · MetricResolver.list_metrics() 出参。

    前端 DataWorkbench 左侧"指标浏览器"用。
    """

    metrics: list[dict] = Field(default_factory=list)  # MetricDef JSON 列表
    source_kind: str = ""  # 当前 resolver 类型


class SqlRunRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=8192)
    source_id: str = ""
    confirmed: bool = False  # HITL 确认（重查询需 true）
    # Rust 桥接注入的连接配置（keyring 已解析）；凭证只在内存传递，不落库不打日志
    connection: dict = Field(default_factory=dict)


class PythonRunRequest(BaseModel):
    script: str = Field(min_length=1, max_length=16384)
    task_id: str = ""  # 关联上一步 SQL 结果


class ChartRecommendRequest(BaseModel):
    columns: list[str]
    dtypes: list[str]
    row_count: int = 0


class ExportRequest(BaseModel):
    task_id: str = ""  # 优先：服务端从 Parquet 取数（整表不经前端，设计红线）
    columns: list[str] = Field(default_factory=list)  # 兼容：内联小结果前端直传
    rows: list[list[Any]] = Field(default_factory=list)
    title: str = "数据报表"
    # 导出路径选择（2026-08-18）：前端 save 对话框选中的目标文件路径；
    # 空 = 默认临时目录（旧行为）
    output_path: str = ""


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
async def sync_schema(source_id: str, body: dict | None = None) -> dict:
    """同步 Schema 元数据（缺口 2：真实拉取表结构 + 中文注释）。"""
    storage = get_default_storage()
    source = await storage.get_source(source_id)

    # 连接配置：请求体优先（Rust 注入），其次数据源登记的 connection_config
    cfg = (body or {}).get("connection") or {}

    if source is None:
        # 资产型数据源（systems.yaml）从未登记进 data_expert.db：
        # 只要 Rust 注入了连接就直接同步并顺手登记（connection_ref 不落凭证，
        # 否则 SQL 能跑、schema 同步却永远 404 —— BUGFIX #126）
        if not cfg:
            raise HTTPException(status_code=404, detail=f"source not found: {source_id}")
        source = {
            "name": source_id,
            "type": str(cfg.get("type") or "mysql"),
            "connection_ref": "",
        }

    if not cfg:
        cfg = source.get("connection_config", {}) or {}
    if not cfg:
        raise HTTPException(status_code=400, detail="缺少数据源连接配置（connection）")

    pool = ReadOnlyPool(cfg)
    try:
        tables = await pool.fetch_schema()
    except ValueError as e:
        # 类型未配置/不支持 → 400 明确提示，不静默返回空 schema（BUGFIX #97）
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        await pool.close()

    await storage.upsert_source(
        source_id=source_id,
        name=source["name"],
        source_type=source["type"],
        connection_ref=source.get("connection_ref", ""),
        schema_cache=tables,
        updated_at=now_epoch(),
    )
    emit_event_sync(
        "data_source_sync",
        {
            "kind": "data_source_sync",
            "source_id": source_id,
            "tables": len(tables),
        },
    )
    return {
        "ok": True,
        "source_id": source_id,
        "synced_at": now_epoch(),
        "tables_synced": len(tables),
    }


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


# ---- v2.87 MetricResolver 抽象层 API 端点 --------------------------------------
# 这些端点让前端 DataWorkbench：
#   1. 主动识别问句对应的指标（/metric/resolve）
#   2. 列出可用指标（/metric/list）—— 左侧"指标浏览器"
# V0 切换仅环境变量 EAIDE_METRIC_RESOLVER 生效（config/data_expert.yaml 是预留模板，
# 当前无代码读取，yaml 接线留待 V1）；未来客户有 IMS / Quick BI / DataFinder 时换 platform 实现。
@router.post("/metric/resolve", response_model=MetricResolveResponse)
async def metric_resolve(req: MetricResolveRequest) -> MetricResolveResponse:
    """v2.87 · 把自然语言问句解析为结构化查询意图（ResolvedQuery）。

    返回 ``resolved`` 为 dict（Pydantic v2 model_dump）或 None（识别失败）。
    错误场景（unknown type / platform 缺 base_url）走 ``error`` 字段兜底。
    """
    try:
        resolver = get_default_resolver()
    except MetricResolverConfigError as e:
        return MetricResolveResponse(resolved=None, error=f"配置错误：{e}")

    resolved = await resolver.resolve(
        req.question,
        context={"source_id": req.source_id},
    )
    if resolved is None:
        return MetricResolveResponse(resolved=None, error="")
    # ResolvedQuery.model_dump() 返回 dict（前端按 TS 镜像解析）
    return MetricResolveResponse(resolved=resolved.model_dump(), error="")


@router.get("/metric/list", response_model=MetricListResponse)
async def metric_list(project: str | None = None) -> MetricListResponse:
    """v2.87 · 列出可用指标（前端"指标浏览器"用）。

    V0 默认 DictMetricResolver 返回 _DEFAULT_DICTIONARY._global 全局条目；
    V1 PlatformMetricResolver 走 IMS HTTP API；V1.5 BridgeMetricResolver 走 dws 视图。
    """
    try:
        resolver = get_default_resolver()
    except MetricResolverConfigError:
        # 配置错误：返空 + source_kind 空字符串；前端按需提示
        return MetricListResponse(metrics=[], source_kind="")

    # 当前 resolver 类型（"dict" / "platform" / "bridge"）
    source_kind = type(resolver).__name__.replace("MetricResolver", "").lower()

    try:
        metrics = await resolver.list_metrics(project=project)
    except NotImplementedError:
        # Platform / Bridge V0 占位 —— 优雅返回空列表
        return MetricListResponse(metrics=[], source_kind=source_kind)

    return MetricListResponse(
        metrics=[m.model_dump() for m in metrics],
        source_kind=source_kind,
    )


@router.post("/nl2sql", response_model=NL2SQLResponse)
async def nl2sql(req: NL2SQLRequest) -> NL2SQLResponse:
    """自然语言 → SQL（不执行，返回待确认 SQL + is_heavy）。

    v2.87 设计补强：
      1. 前置调用 ``MetricResolver.resolve()`` 识别指标（dict / platform / bridge）
      2. 把 ``source_kind`` + ``confidence`` 透传给前端（状态栏显示）
      3. 业务逻辑（Schema 链接 + Few-shot + 生成 SQL）保持不变 —— V0 dict 模式包装
         现有 ``dictionary.translate()``，零业务代码改动
    """
    from agent.dataexpert.nl2sql import dictionary, linker
    from agent.dataexpert.nl2sql.generator import SqlCase, to_sql

    storage = get_default_storage()

    # 获取数据源 schema
    schema_cache: list[dict] = []
    if req.source_id:
        source = await storage.get_source(req.source_id)
        if source:
            schema_cache = source.get("schema_cache", [])

    # ---- v2.87 指标识别（MetricResolver 抽象层） ---------------------------------
    # 默认 DictMetricResolver（包装 dictionary.translate），失败时优雅降级
    metric_source_kind = ""
    metric_confidence = 0.0
    try:
        resolver = get_default_resolver()
        resolved = await resolver.resolve(
            req.question,
            context={"source_id": req.source_id},
        )
        if resolved is not None:
            metric_source_kind = resolved.source_kind
            metric_confidence = resolved.confidence
            # V0 简化：把 ResolvedQuery.metric.name 注入 prompt（V1 Platform 会用 SQL 模板）
            # 此处暂不破坏既有 to_sql 签名 —— 仅作为 hint 留存
    except (MetricResolverConfigError, NotImplementedError):
        # 配置错误 / 占位实现抛错 —— 优雅降级到原纯 NL2SQL 流程
        pass

    # Schema 链接（选 3-5 表；本地 embedding 向量检索，未配置/不可达自动退化关键字）
    emb_client = linker.build_embedding_client()
    tables = await linker.select_tables(req.question, schema_cache, embedding=emb_client)

    # Few-shot 飞轮（Vanna 范式）：从历史分析任务选最相似的已确认 SQL 作参考案例
    few_shot: list[SqlCase] = []
    try:
        history = await storage.list_tasks(limit=50)
        cases = await linker.select_few_shot(req.question, history, embedding=emb_client)
        few_shot = [SqlCase(c["question"], c["sql"]) for c in cases]
    except Exception:
        pass  # few-shot 是增强项，失败不阻断主链路

    # 业务字典
    dict_ctx = dictionary.translate(req.question, req.source_id)

    # 生成 SQL（BUGFIX #128：真接 LMRouter —— 此前 to_sql 从未拿到 router，
    # 永远返回 V0 占位「SELECT 1;」，前端却误报「已生成 SQL」）
    from agent.llm.router import LMRouter

    sql = await to_sql(req.question, tables, dict_ctx, few_shot=few_shot, llm_router=LMRouter())

    # 模型不可用时的失败占位不得当「已生成」下发 —— 明确报错，前端展示 ❌ 原因
    if sql.startswith("-- V0 占位") or sql.startswith("-- LLM 调用失败"):
        return NL2SQLResponse(
            sql="",
            is_heavy=False,
            tables_used=[t.name for t in tables],
            dictionary_context=dict_ctx,
            error="SQL 生成失败：模型服务不可用（请确认 Ollama / 内网 / 云端模型已启用），可改用 SQL 模式手写查询",
            metric_source_kind=metric_source_kind,
            metric_confidence=metric_confidence,
        )

    # 缺口 10 前置校验：生成的 SQL 必须是单条 SELECT，否则丢弃，绝不下发执行
    try:
        enforce_select_only(sql)
    except WriteBlockedError as e:
        return NL2SQLResponse(
            sql="",
            is_heavy=False,
            tables_used=[t.name for t in tables],
            dictionary_context=dict_ctx,
            error=f"生成的 SQL 含非查询语句（{e.token}），已拒绝",
            metric_source_kind=metric_source_kind,
            metric_confidence=metric_confidence,
        )

    # 判断是否重查询
    heavy = is_heavy(sql)

    return NL2SQLResponse(
        sql=sql,
        is_heavy=heavy,
        tables_used=[t.name for t in tables],
        dictionary_context=dict_ctx,
        metric_source_kind=metric_source_kind,
        metric_confidence=metric_confidence,
    )


@router.post("/sql/run")
async def run_sql(req: SqlRunRequest) -> dict:
    """执行只读 SQL（SELECT 白名单 + 黑名单双层 guard + LIMIT + HITL）。"""
    # 安全层 1：SELECT 白名单（缺口 10：除 dev 环境外仅 SELECT/WITH）
    # 安全层 2：写操作黑名单（纵深防御）
    try:
        enforce_select_only(req.sql)
        enforce_readonly(req.sql)
    except WriteBlockedError as e:
        # 记 DATA_WRITE_BLOCKED 审计
        emit_event_sync(
            "data_write_blocked",
            {
                "kind": "data_write_blocked",
                "sql": req.sql[:200],
                "token": e.token,
            },
        )
        raise HTTPException(status_code=403, detail=str(e))

    # 安全层 3：重查询 HITL
    if is_heavy(req.sql) and not req.confirmed:
        return {
            "needs_confirm": True,
            "message": "检测到多表 JOIN / 全表扫描，请确认后重新提交（confirmed=true）",
            "sql": inject_limit(req.sql),
        }

    # 解析连接配置：请求体优先（Rust 注入），其次数据源登记的 connection_config
    source_cfg = dict(req.connection)
    storage = get_default_storage()
    if not source_cfg and req.source_id:
        source = await storage.get_source(req.source_id)
        source_cfg = (source or {}).get("connection_config", {}) or {}
    if not source_cfg:
        raise HTTPException(status_code=400, detail="缺少数据源连接配置（connection 或 source_id）")
    # 类型未配置（Rust 注入 type="" 等）→ 400 明确提示，
    # 不允许静默走兜底返回 0 行（BUGFIX #97）
    if not str(source_cfg.get("type") or "").strip():
        raise HTTPException(
            status_code=400,
            detail="数据源未配置数据库类型（db_type），请在「系统资产」中编辑该数据源并选择类型后重试",
        )

    # 真实执行（pool 内部再次 enforce_readonly + inject_limit，纵深防御）
    pool = ReadOnlyPool(source_cfg)
    start = time.perf_counter()
    try:
        df = await pool.execute_sql(req.sql)
    except WriteBlockedError as e:
        emit_event_sync(
            "data_write_blocked",
            {
                "kind": "data_write_blocked",
                "sql": req.sql[:200],
                "token": e.token,
            },
        )
        raise HTTPException(status_code=403, detail=str(e))
    except ValueError as e:
        # 未知数据源类型等配置错误 → 400（BUGFIX #97）
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        await pool.close()
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # 结果分流 + 任务落库（缺口 1/4）
    task_id = generate_id()
    row_count = len(df)
    columns = [str(c) for c in df.columns]
    dtypes = [str(t) for t in df.dtypes]
    inline = row_count <= ROW_INLINE_MAX
    result_data_ref = "" if inline else save_result_parquet(df, task_id)

    await storage.insert_task(
        task_id=task_id,
        name=req.sql[:64],
        user_id="local",
        query_sql=req.sql,
        result_metadata={"columns": columns, "dtypes": dtypes, "row_count": row_count},
        result_data_ref=result_data_ref,
        created_at=now_epoch(),
    )

    # 记 DATA_SQL_RUN 审计
    emit_event_sync(
        EVT_DATA_QUERY_RESULT,
        {
            "kind": EVT_DATA_QUERY_RESULT,
            "sql": req.sql[:500],
            "elapsed_ms": elapsed_ms,
            "row_count": row_count,
            "task_id": task_id,
        },
    )

    return {
        "ok": True,
        "task_id": task_id,
        "sql": req.sql,
        "columns": columns,
        "dtypes": dtypes,
        "rows": df.values.tolist() if inline else [],
        "result_data_ref": result_data_ref,
        "stream_ref": "" if inline else f"/data/stream/{task_id}",
        "row_count": row_count,
        "elapsed_ms": elapsed_ms,
        "truncated": False,
    }


@router.post("/python/run")
async def run_python(req: PythonRunRequest) -> dict:
    """沙箱执行 Python（缺口 8：task_id 非空时注入上一步 SQL 结果 df）。"""
    from agent.dataexpert.sandbox.executor import run

    # 解析上一步 SQL 结果的 Parquet 引用（子进程直接读盘）
    df_input_ref = ""
    if req.task_id:
        task = await get_default_storage().get_task(req.task_id)
        df_input_ref = (task or {}).get("result_data_ref", "") or ""

    result = await run(req.script, df_input_ref=df_input_ref)

    emit_event_sync(
        EVT_DATA_PYTHON_RESULT,
        {
            "kind": EVT_DATA_PYTHON_RESULT,
            "ok": result.ok,
            "elapsed_s": result.elapsed_s,
            "error": result.error[:500] if result.error else "",
        },
    )

    resp: dict = {
        "ok": result.ok,
        "out_df_ref": result.out_df_ref,
        "stdout": result.stdout[:2000],
        "error": result.error[:2000],
        "elapsed_s": result.elapsed_s,
        "columns": [],
        "dtypes": [],
        "rows": [],
        "row_count": 0,
    }

    # 输出 DataFrame 存在时：回传头部（≤ ROW_INLINE_MAX 行）供前端网格展示
    if result.ok and result.out_df_ref:
        try:
            out_df = load_result_parquet(result.out_df_ref)
            resp["columns"] = [str(c) for c in out_df.columns]
            resp["dtypes"] = [str(t) for t in out_df.dtypes]
            resp["row_count"] = len(out_df)
            resp["rows"] = out_df.head(ROW_INLINE_MAX).values.tolist()
        except Exception:
            pass

    return resp


@router.post("/chart/recommend")
async def chart_recommend(req: ChartRecommendRequest) -> dict:
    """图表推荐（task='chart_reco' 本地）。"""
    from agent.dataexpert.viz.recommender import recommend_chart

    reco = recommend_chart(req.columns, req.dtypes, req.row_count)

    emit_event_sync(
        EVT_DATA_CHART_READY,
        {
            "kind": EVT_DATA_CHART_READY,
            "chart_type": reco["chart_type"],
            "reason": reco["reason"],
        },
    )

    return reco


@router.post("/export/{fmt}")
async def export_data(fmt: str, req: ExportRequest) -> dict:
    """导出（PII 脱敏 + 水印 + 审计）。优先按 task_id 服务端取数。

    小结果集（≤ ROW_INLINE_MAX）内联返回不落 Parquet，result_data_ref 为空；
    此时回退用请求体的 columns/rows（BUGFIX #104），不得直接 404。
    """
    columns, rows = req.columns, req.rows
    if req.task_id:
        task = await get_default_storage().get_task(req.task_id)
        ref = (task or {}).get("result_data_ref", "")
        if task and ref:
            df = load_result_parquet(ref)
            columns = [str(c) for c in df.columns]
            rows = df.values.tolist()
        elif not columns and not rows:
            raise HTTPException(status_code=404, detail="task result not found")
        # task 缺失/无 ref 但请求体带数 → 回退内联数据（小结果集导出链路）
    elif not columns and not rows:
        raise HTTPException(status_code=400, detail="task_id 或 columns/rows 必须提供其一")

    # 导出路径选择（2026-08-18）：前端对话框选中的目标路径，基础合法性校验
    output_path: str | None = (req.output_path or "").strip() or None
    if output_path is not None:
        p = Path(output_path)
        if ".." in p.parts or not p.name:
            raise HTTPException(status_code=400, detail="非法导出路径")
        output_path = str(p)

    if fmt == "excel":
        from agent.dataexpert.export.excel import export_excel

        meta = export_excel(columns, rows, title=req.title, output_path=output_path)
    elif fmt == "pdf":
        from agent.dataexpert.export.pdf import export_pdf

        meta = export_pdf(columns, rows, title=req.title, output_path=output_path)
    elif fmt == "csv":
        from agent.dataexpert.export.csv import export_csv

        meta = export_csv(columns, rows, title=req.title, output_path=output_path)
    else:
        raise HTTPException(status_code=400, detail=f"unsupported format: {fmt}")

    # 记 DATA_EXPORT 审计
    emit_event_sync(
        EVT_DATA_EXPORT_DONE,
        {
            "kind": EVT_DATA_EXPORT_DONE,
            "format": fmt,
            "row_count": meta.get("row_count", 0),
            "md5": meta.get("md5", ""),
            "path": meta.get("path", ""),
        },
    )

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


@router.websocket("/stream/{task_id}")
async def stream_result(ws: WebSocket, task_id: str) -> None:
    """大结果集 Arrow IPC 二进制流（缺口 5，设计红线：不走 SSE/JSON）。

    协议：首帧 text(meta) → N 帧 binary(Arrow IPC 批) → 末帧 text(done)。
    每批是独立完整的 IPC stream（含 schema），前端逐批 tableFromIPC 后合并。
    """
    await ws.accept()
    storage = get_default_storage()
    task = await storage.get_task(task_id)
    ref = (task or {}).get("result_data_ref", "")
    if not task or not ref:
        await ws.close(code=4404)
        return

    try:
        import pyarrow as pa

        df = load_result_parquet(ref)
        raw_meta = task.get("result_metadata") or {}
        meta = raw_meta if isinstance(raw_meta, dict) else {}
        await ws.send_text(
            json.dumps(
                {"kind": "meta", **meta},
                ensure_ascii=False,
                default=str,
            )
        )

        table = pa.Table.from_pandas(df, preserve_index=False)
        for batch in table.to_batches(max_chunksize=ARROW_BATCH_ROWS):
            sink = pa.BufferOutputStream()
            with pa.ipc.new_stream(sink, batch.schema) as writer:
                writer.write_batch(batch)
            await ws.send_bytes(sink.getvalue().to_pybytes())

        await ws.send_text(
            json.dumps(
                {"kind": "done", "done": True, "row_count": len(df)},
            )
        )
    finally:
        await ws.close()
