"""智能路由数据层（Phase 2C v2）—— router.db 的 aiosqlite 封装。

职责：
    - 首次访问自动建表（executescript(schema.sql)）
    - llm_backends CRUD（模型管理面板的后端支撑）
    - routing_decisions 决策日志写入（全链路 Trace）
    - cost_daily 日聚合（成本统计）

设计对齐 audit/store.py：aiosqlite + executescript + 进程内 asyncio.Lock。
router.db 路径来自 settings.llm_router_db_path（相对路径 → 测试 chdir 隔离）。
Schema 单一真源：读取同目录 schema.sql，不内联 SQL。
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from agent.config import settings
from agent.llm.models import LLMBackend, RoutingDecision

_LOCK = asyncio.Lock()

# schema.sql 与本文件同目录，单一真源
_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _db_path() -> str:
    """每次读 settings —— 测试用 monkeypatch 改路径后立即生效。"""
    return settings.llm_router_db_path


def _load_schema() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


@asynccontextmanager
async def _connect():
    """打开连接并确保 schema 就位，退出时保证关闭（照搬 audit/store.py 的用法）。

    用 async with aiosqlite.connect(...) 直接管理生命周期——不返回裸连接，
    避免 worker 线程比事件循环活得久导致 'Event loop is closed'。
    """
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(path) as db:
        await db.executescript(_load_schema())
        yield db


# ---- 后端配置 CRUD ---------------------------------------------------------


async def upsert_backend(backend: LLMBackend) -> list[str]:
    """新增或更新一个后端（按 name 主键 upsert）。

    同驻留只允许 1 个启用：当保存的 backend 为 enabled=1 时，在同一事务内
    把同数据驻留（local / private / cloud）其它已启用后端全部置为停用，
    返回被停用的后端名列表。
    """
    row = backend.to_row()
    disabled: list[str] = []
    async with _LOCK, _connect() as db:
        await db.execute(
            """
                INSERT INTO llm_backends
                    (name, type, base_url, api_key_ref, model_name, capabilities,
                     max_context, cost_per_1k_tokens, timeout_seconds,
                     data_residency, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    type=excluded.type,
                    base_url=excluded.base_url,
                    api_key_ref=excluded.api_key_ref,
                    model_name=excluded.model_name,
                    capabilities=excluded.capabilities,
                    max_context=excluded.max_context,
                    cost_per_1k_tokens=excluded.cost_per_1k_tokens,
                    timeout_seconds=excluded.timeout_seconds,
                    data_residency=excluded.data_residency,
                    enabled=excluded.enabled
                """,
            (
                row["name"],
                row["type"],
                row["base_url"],
                row["api_key_ref"],
                row["model_name"],
                json.dumps(row["capabilities"], ensure_ascii=False),
                row["max_context"],
                row["cost_per_1k_tokens"],
                row["timeout_seconds"],
                row["data_residency"],
                1 if row["enabled"] else 0,
            ),
        )
        if row["enabled"]:
            cur = await db.execute(
                "SELECT name FROM llm_backends WHERE data_residency=? AND name<>? AND enabled=1",
                (row["data_residency"], row["name"]),
            )
            rows = await cur.fetchall()
            disabled = [r[0] for r in rows]
            if disabled:
                await db.execute(
                    "UPDATE llm_backends SET enabled=0 "
                    "WHERE data_residency=? AND name<>? AND enabled=1",
                    (row["data_residency"], row["name"]),
                )
        await db.commit()
    return disabled


def _row_to_backend(row: tuple) -> LLMBackend:
    (
        name,
        type_,
        base_url,
        api_key_ref,
        model_name,
        caps,
        max_ctx,
        cost,
        timeout,
        residency,
        enabled,
    ) = row
    try:
        capabilities = json.loads(caps) if caps else []
    except (json.JSONDecodeError, TypeError):
        capabilities = []
    # max_context 未显式设置（NULL）→ 全局默认上下文长度回退（两级回退）
    if max_ctx is None:
        from agent.llm.gen_limits import default_context_window

        max_ctx = default_context_window()
    return LLMBackend(
        name=name,
        type=type_,
        base_url=base_url,
        model_name=model_name,
        api_key_ref=api_key_ref,
        capabilities=capabilities,
        max_context=max_ctx,
        cost_per_1k_tokens=cost if cost is not None else 0.0,
        timeout_seconds=timeout if timeout is not None else 30,
        data_residency=residency or "local",
        enabled=bool(enabled),
    )


_SELECT_COLS = (
    "name, type, base_url, api_key_ref, model_name, capabilities, "
    "max_context, cost_per_1k_tokens, timeout_seconds, data_residency, enabled"
)


async def list_backends(*, enabled_only: bool = False) -> list[LLMBackend]:
    async with _connect() as db:
        sql = f"SELECT {_SELECT_COLS} FROM llm_backends"
        if enabled_only:
            sql += " WHERE enabled=1"
        sql += " ORDER BY name"
        cur = await db.execute(sql)
        rows = await cur.fetchall()
    return [_row_to_backend(r) for r in rows]


async def get_backend(name: str) -> LLMBackend | None:
    async with _connect() as db:
        cur = await db.execute(f"SELECT {_SELECT_COLS} FROM llm_backends WHERE name=?", (name,))
        row = await cur.fetchone()
    return _row_to_backend(row) if row else None


async def delete_backend(name: str) -> bool:
    async with _LOCK, _connect() as db:
        cur = await db.execute("DELETE FROM llm_backends WHERE name=?", (name,))
        await db.commit()
        return cur.rowcount > 0


# ---- feature → backend 绑定（Phase 2F） -------------------------------------


async def get_feature_backend(feature: str) -> str | None:
    """读某功能绑定的 backend 名（如 'codenav' → 'deepseek-cloud'）。"""
    async with _connect() as db:
        cur = await db.execute(
            "SELECT backend_name FROM feature_backend WHERE feature=?", (feature,)
        )
        row = await cur.fetchone()
    return row[0] if row and row[0] else None


async def set_feature_backend(feature: str, backend_name: str | None) -> None:
    """绑定 / 解绑。

    backend_name=None → 解绑（删除行）；否则 upsert。
    """
    import time as _t

    async with _LOCK, _connect() as db:
        if backend_name is None or backend_name == "":
            await db.execute("DELETE FROM feature_backend WHERE feature=?", (feature,))
        else:
            await db.execute(
                """
                    INSERT INTO feature_backend (feature, backend_name, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(feature) DO UPDATE SET
                      backend_name=excluded.backend_name,
                      updated_at=excluded.updated_at
                    """,
                (feature, backend_name, int(_t.time())),
            )
        await db.commit()


# ---- 决策日志 + 成本聚合 ---------------------------------------------------


async def record_decision(
    decision: RoutingDecision,
    *,
    task_category: str | None = None,
    est_tokens: int = 0,
    now: int | None = None,
) -> None:
    """写一条路由决策 + 增量更新 cost_daily。

    now: 可注入的秒级时间戳（测试用）；缺省取 time.time()。
    task_category: 覆盖 decision.task_category（用于成本聚合的分组键）。
    """
    ts = int(now if now is not None else time.time())
    cat = task_category or (decision.task_category.value if decision.task_category else "unknown")
    date_str = time.strftime("%Y-%m-%d", time.gmtime(ts))
    async with _LOCK, _connect() as db:
        await db.execute(
            """
                INSERT INTO routing_decisions
                    (request_id, user_id, task_category, sensitivity,
                     primary_backend, actual_backend, fallback_used, cache_hit,
                     estimated_cost, actual_cost, latency_ms, quality_score,
                     trace_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
            (
                decision.request_id,
                decision.user_id,
                cat,
                decision.sensitivity.value if decision.sensitivity else None,
                decision.primary_backend,
                decision.actual_backend,
                1 if decision.fallback_used else 0,
                1 if decision.cache_hit else 0,
                decision.estimated_cost,
                decision.actual_cost,
                decision.latency_ms,
                decision.quality_score,
                json.dumps(decision.trace_dict(), ensure_ascii=False),
                ts,
            ),
        )
        # 缓存命中不计成本/tokens（cache_hit → actual_cost=0）
        backend = decision.actual_backend or decision.primary_backend or "unknown"
        await db.execute(
            """
                INSERT INTO cost_daily
                    (date, user_id, backend, task_category,
                     total_tokens, total_cost, call_count)
                VALUES (?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(date, user_id, backend, task_category) DO UPDATE SET
                    total_tokens = total_tokens + excluded.total_tokens,
                    total_cost   = total_cost + excluded.total_cost,
                    call_count   = call_count + 1
                """,
            (
                date_str,
                decision.user_id,
                backend,
                cat,
                0 if decision.cache_hit else est_tokens,
                decision.actual_cost,
            ),
        )
        await db.commit()


async def recent_decisions(limit: int = 50) -> list[dict]:
    async with _connect() as db:
        cur = await db.execute(
            """
            SELECT request_id, user_id, task_category, primary_backend,
                   actual_backend, fallback_used, cache_hit, latency_ms,
                   estimated_cost, actual_cost, trace_json, created_at
            FROM routing_decisions ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        )
        rows = await cur.fetchall()
    out = []
    for r in rows:
        try:
            trace = json.loads(r[10]) if r[10] else {}
        except (json.JSONDecodeError, TypeError):
            trace = {}
        out.append(
            {
                "request_id": r[0],
                "user_id": r[1],
                "task_category": r[2],
                "primary_backend": r[3],
                "actual_backend": r[4],
                "fallback_used": bool(r[5]),
                "cache_hit": bool(r[6]),
                "latency_ms": r[7],
                "estimated_cost": r[8],
                "actual_cost": r[9],
                "trace": trace,
                "created_at": r[11],
            }
        )
    return out


async def cost_summary(date: str | None = None) -> list[dict]:
    """成本统计。date 为空则返回全部；否则过滤某日。"""
    async with _connect() as db:
        if date:
            cur = await db.execute(
                "SELECT date, user_id, backend, task_category, total_tokens, "
                "total_cost, call_count FROM cost_daily WHERE date=? ORDER BY total_cost DESC",
                (date,),
            )
        else:
            cur = await db.execute(
                "SELECT date, user_id, backend, task_category, total_tokens, "
                "total_cost, call_count FROM cost_daily ORDER BY date DESC, total_cost DESC"
            )
        rows = await cur.fetchall()
    return [
        {
            "date": r[0],
            "user_id": r[1],
            "backend": r[2],
            "task_category": r[3],
            "total_tokens": r[4],
            "total_cost": r[5],
            "call_count": r[6],
        }
        for r in rows
    ]


# ---- 评分权重（Phase 2C V2） --------------------------------------------------


DEFAULT_WEIGHTS = {
    "capability": 0.35,
    "cost": 0.25,
    "latency": 0.20,
    "compliance": 0.15,
    "availability": 0.05,
}


async def get_router_weights() -> dict:
    """读 router_weights 单行（id=1）；不存在则返回默认值。"""
    async with _connect() as db:
        cur = await db.execute(
            "SELECT capability, cost, latency, compliance, availability FROM router_weights WHERE id=1"
        )
        row = await cur.fetchone()
    if not row:
        return dict(DEFAULT_WEIGHTS)
    return {
        "capability": row[0],
        "cost": row[1],
        "latency": row[2],
        "compliance": row[3],
        "availability": row[4],
    }


async def set_router_weights(weights: dict) -> None:
    """upsert 评分权重（id=1 单行）。

    校验由调用方（engine_api WeightsBody）完成；这里只落库。
    """
    import time as _t

    async with _LOCK:
        async with _connect() as db:
            await db.execute(
                """
                INSERT INTO router_weights (id, capability, cost, latency, compliance, availability, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    capability = excluded.capability,
                    cost = excluded.cost,
                    latency = excluded.latency,
                    compliance = excluded.compliance,
                    availability = excluded.availability,
                    updated_at = excluded.updated_at
                """,
                (
                    float(weights["capability"]),
                    float(weights["cost"]),
                    float(weights["latency"]),
                    float(weights["compliance"]),
                    float(weights["availability"]),
                    int(_t.time()),
                ),
            )
            await db.commit()
