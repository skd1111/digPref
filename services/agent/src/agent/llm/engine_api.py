"""Phase 2C V2.5 路由 API —— FastAPI 端点 + LMRouter 委托。

新增 4 个端点：
  GET  /router/metrics          — circuit 状态 + budget
  GET  /router/decisions        — 最近 routing_decisions
  GET  /router/backends         — 列出所有后端（带 role）
  GET  /router/weights          — 当前评分权重（V0 hardcode）
  POST /router/breakers/{name}/reset
  POST /router/backends/test-connection — 真实探测后端连通性（不在 storage 落库）

LMRouter 委托：4 个公开 API（classify_intent / plan / repair_call / summarise）
内部调 RouterEngine.route_request(role=...) 选后端。
"""

from __future__ import annotations

import logging
import time

import httpx
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from agent.codenav.llm_client import reset_default_client as _reset_codenav_client
from agent.llm.budget import BudgetController
from agent.llm.circuit_breaker import CircuitBreakerRegistry
from agent.llm.engine import RouterEngine
from agent.llm.metrics import MetricsRecorder
from agent.llm.models import LLMBackend, RoutingDecision, Sensitivity, TaskCategory
from agent.llm.router import LMRouter
from agent.llm.storage import (
    delete_backend,
    get_backend,
    get_router_weights,
    list_backends,
    recent_decisions,
    set_router_weights,
    upsert_backend,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/router", tags=["router"])


# 进程级单例（V0 mock 引擎 + 真实存储）。V1 改成从 storage 加载 backends
_ENGINE: RouterEngine | None = None


def _get_engine() -> RouterEngine:
    """V0 简化：内存默认 backends + 真实 storage metrics 写入。

    V2 增量：启动时从 router.db 读 router_weights（持久化的评分权重）；
    若不存在则用 DEFAULT_WEIGHTS（capability 0.35 / cost 0.25 / ...）。

    注意：用 sync sqlite3 读（避免 asyncio.run 在 FastAPI lifespan 中冲突）。
    """
    global _ENGINE
    if _ENGINE is None:
        from agent.config import settings

        # V0 默认 3 个后端（V1 改从 storage 加载）
        default_backends = [
            LLMBackend(
                name="ollama-utility",
                type="local",
                base_url=settings.ollama_base_url,
                model_name="qwen2.5:0.5b",
                cost_per_1k_tokens=0.0,
                timeout_seconds=5,
                data_residency="local",
                role="utility",
                enabled=True,
            ),
            LLMBackend(
                name="deepseek-reasoning",
                type="private",
                base_url="http://internal-deepseek.lan/v1",
                model_name="deepseek-r1",
                cost_per_1k_tokens=0.001,
                timeout_seconds=20,
                data_residency="private",
                role="reasoning",
                enabled=True,
            ),
            LLMBackend(
                name="gpt4o-execution",
                type="cloud",
                base_url="https://api.openai.com/v1",
                model_name="gpt-4o",
                cost_per_1k_tokens=0.03,
                timeout_seconds=60,
                data_residency="cloud",
                api_key_ref="llm.openai.api_key",
                role="execution",
                enabled=True,
            ),
        ]
        # V2 增量：sync 读持久化权重（best-effort，失败用默认）
        persisted_weights = None
        try:
            import sqlite3 as _sq
            from pathlib import Path

            db_path = settings.llm_router_db_path
            if Path(db_path).exists():
                conn = _sq.connect(db_path, timeout=5)
                try:
                    cur = conn.execute(
                        "SELECT capability, cost, latency, compliance, availability FROM router_weights WHERE id=1"
                    )
                    row = cur.fetchone()
                    if row:
                        persisted_weights = {
                            "capability": row[0],
                            "cost": row[1],
                            "latency": row[2],
                            "compliance": row[3],
                            "availability": row[4],
                        }
                finally:
                    conn.close()
        except Exception as e:
            logger.warning("engine_init_load_weights_failed err=%s", e)
        _ENGINE = RouterEngine(
            backends=default_backends,
            budget=BudgetController(),
            breakers=CircuitBreakerRegistry(),
            metrics=MetricsRecorder(),
            weights=persisted_weights,
        )
    return _ENGINE


# ---- API 端点 ----


class ActiveBackendBody(BaseModel):
    """active 后端统一配置（双轨制统一后唯一入口）。"""

    active: str = Field(pattern="^(mock|ollama|private|custom)$")
    ollama: dict = Field(default_factory=dict)
    private: dict = Field(default_factory=dict)
    custom: dict = Field(default_factory=dict)


@router.get("/active")
async def get_active_backend() -> dict:
    """回显已持久化的 active 配置（供前端编辑；不含 env 覆盖态）。"""
    from agent.llm.active_config import load_saved_active

    return load_saved_active()


@router.put("/active")
async def set_active_backend(body: ActiveBackendBody) -> dict:
    """保存 active 配置到 router.db + 热应用（env + settings + 主对话 router 重建）。"""
    from agent.llm.active_config import apply_active, save_active

    cfg = body.model_dump()
    save_active(cfg)
    applied = apply_active(cfg)
    # 主对话 runtime 缓存的是旧 LMRouter（_mock_mode/连接参数已固化）→ 重建
    try:
        from agent import main as agent_main

        if agent_main._runtime is not None:
            agent_main._runtime.llm = LMRouter()
    except Exception as exc:
        logger.warning("active backend switch: runtime router refresh skipped: %s", exc)
    logger.info("active backend switched to %s", applied.get("active"))
    return {"ok": True, "active": applied.get("active")}


@router.get("/metrics")
async def get_metrics() -> dict:
    """实时指标：circuit 状态 + budget。"""
    eng = _get_engine()
    return {
        "circuits": eng.circuit_states(),
        "budget": eng.budget_status(),
        "backends": [
            {"name": b.name, "type": b.type, "role": b.role, "enabled": b.enabled}
            for b in eng.backends
        ],
    }


@router.get("/decisions")
async def get_decisions(limit: int = 50) -> dict:
    """最近 routing_decisions（真实表读取）。"""
    rows = await recent_decisions(limit=limit)
    return {"decisions": rows}


@router.get("/cache-stats")
async def get_cache_stats_endpoint() -> dict:
    """Phase 17 V0：分层缓存命中率统计（L1 实时计数 + routing_decisions 历史口径）。"""
    from agent.llm.cache_stats import get_cache_stats

    return get_cache_stats()


class CacheToggleBody(BaseModel):
    enabled: bool = Field(..., description="L1 精确响应缓存开关（一键回滚）")


@router.post("/cache-toggle")
async def set_cache_enabled(body: CacheToggleBody) -> dict:
    """Phase 17 V0：L1 缓存一键开关（关闭后链路等价无缓存现状）。"""
    from agent.llm.router import set_l1_cache_enabled

    set_l1_cache_enabled(body.enabled)
    return {"ok": True, "l1_enabled": body.enabled}


@router.get("/backends")
async def get_backends() -> dict:
    """列出所有后端（带 role）。"""
    backends = await list_backends()
    return {"backends": [b.to_row() for b in backends]}


@router.post("/backends")
async def create_backend(body: dict = Body(...)) -> dict:
    """添加后端（V2.5）。先校验协议 + 写存储。"""
    try:
        backend = LLMBackend(**{k: v for k, v in body.items() if k in _LLM_BACKEND_FIELDS})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"parse error: {e}")
    err = backend.validate_protocol()
    if err:
        raise HTTPException(status_code=400, detail=err)
    disabled = await upsert_backend(backend)
    _reset_codenav_client()  # backend 变动 → 让 codenav 重新解析
    return {"ok": True, "backend": backend.to_row(), "disabled": disabled}


@router.put("/backends/{name}")
async def update_backend(name: str, body: dict = Body(...)) -> dict:
    """更新后端。"""
    existing = await get_backend(name)
    if not existing:
        raise HTTPException(status_code=404)
    body["name"] = name  # URL 是真源
    try:
        backend = LLMBackend(**{k: v for k, v in body.items() if k in _LLM_BACKEND_FIELDS})
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"parse error: {e}")
    err = backend.validate_protocol()
    if err:
        raise HTTPException(status_code=400, detail=err)
    disabled = await upsert_backend(backend)
    _reset_codenav_client()  # backend 变动 → 让 codenav 重新解析
    return {"ok": True, "backend": backend.to_row(), "disabled": disabled}


# LLMBackend 允许的字段（避免 Pydantic / dataclass 报错）
_LLM_BACKEND_FIELDS = {
    "name",
    "type",
    "base_url",
    "api_key_ref",
    "model_name",
    "capabilities",
    "max_context",
    "cost_per_1k_tokens",
    "timeout_seconds",
    "data_residency",
    "enabled",
    "role",
}


@router.delete("/backends/{name}")
async def delete_backend_endpoint(name: str) -> dict:
    """删除后端。"""
    ok = await delete_backend(name)
    _reset_codenav_client()  # backend 变动 → 让 codenav 重新解析
    return {"ok": ok}


@router.get("/weights")
async def get_weights() -> dict:
    """读 router_weights 单行；不存在则返回默认（与 Engine 内存一致）。"""
    try:
        w = await get_router_weights()
    except Exception as e:
        logger.warning("get_weights_failed err=%s", e)
        w = {
            "capability": 0.35,
            "cost": 0.25,
            "latency": 0.20,
            "compliance": 0.15,
            "availability": 0.05,
        }
    return {"weights": w}


class WeightsBody(BaseModel):
    """PUT /router/weights body —— 5 维评分权重。"""

    capability: float = Field(ge=0.0, le=1.0)
    cost: float = Field(ge=0.0, le=1.0)
    latency: float = Field(ge=0.0, le=1.0)
    compliance: float = Field(ge=0.0, le=1.0)
    availability: float = Field(ge=0.0, le=1.0)


@router.put("/weights")
async def set_weights(body: WeightsBody) -> dict:
    """V2 增量：更新评分权重（持久化 + 热生效到 Engine 内存）。

    校验：
        1. 5 个值 ∈ [0, 1]（Pydantic Field）
        2. 5 个和 ∈ [0.99, 1.01]（防止前端编辑后漂移）

    Tauri 命令 `router_set_weights` 已注册（commands/router.rs:189），本端点是其后端。
    """
    s = body.capability + body.cost + body.latency + body.compliance + body.availability
    if not (0.99 <= s <= 1.01):
        raise HTTPException(
            status_code=400,
            detail=f"weights must sum to 1.0 (got {s:.4f}); adjust sliders so Σ=1",
        )
    weights_dict = {
        "capability": body.capability,
        "cost": body.cost,
        "latency": body.latency,
        "compliance": body.compliance,
        "availability": body.availability,
    }
    # 1) 落库（router.db.router_weights id=1）
    await set_router_weights(weights_dict)
    # 2) 热生效到 Engine 内存
    eng = _get_engine()
    eng.set_weights(weights_dict)
    logger.info("router_weights_updated %s", weights_dict)
    return {"ok": True, "weights": weights_dict}


@router.post("/breakers/{name}/reset")
async def reset_breaker(name: str) -> dict:
    """手动重置熔断器。"""
    eng = _get_engine()
    cb = eng._breakers.get_or_create(name)
    cb.reset()
    return {"ok": True, "state": cb.state.value}


class SparkModeBody(BaseModel):
    """POST /router/spark-mode body"""

    enabled: bool


@router.post("/spark-mode")
async def set_spark_mode(body: SparkModeBody) -> dict:
    """V2 增量：Spark 模式 toggle（前端 RouterDashboard 直连）。

    通过全局 LMRouter 实例调 set_spark_mode()。Engine 同时更新自身 spark_enabled，
    影响后续 route_request() 的 DSpark 决策。
    """
    try:
        from agent.main import get_runtime

        runtime = get_runtime()
        runtime.llm.set_spark_mode(body.enabled)
    except Exception as e:
        # runtime 未就绪时回退：仅 Engine set（前端重启后生效）
        logger.warning("router_set_spark_mode_runtime_unavailable err=%s", e)
        eng = _get_engine()
        eng.set_spark_enabled(body.enabled)
    return {"ok": True, "spark_enabled": body.enabled}


@router.post("/reload-context")
async def reload_max_context_endpoint() -> dict:
    """热重载 LMRouter 的 max_context（用户在模型管理面板改完保存后调一次）。

    读 router.db → 更新 ollama / private client 的 max_context。
    """
    try:
        from agent.main import get_runtime

        runtime = get_runtime()
        runtime.llm.reload_max_context()
        return {
            "ok": True,
            "ollama_max_ctx": runtime.llm.ollama.max_context,
            "private_max_ctx": (runtime.llm.private.max_context if runtime.llm.private else None),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"reload failed: {e}")


# ---- 测试连接（不落库，真实探测） ----------------------------------------


def _is_anthropic_endpoint(base_url: str) -> bool:
    """启发式判断 base_url 是否为 Anthropic 兼容端点（仅用于探测提示，不做协议支持）。"""
    path = (base_url or "").split("?", 1)[0].rstrip("/").lower()
    return "/anthropic" in path or path.endswith("/messages")


async def _probe_ollama(base_url: str, timeout_s: float = 5.0) -> dict:
    """Ollama 真实探测：GET /api/tags 列已下载模型。"""
    url = f"{base_url.rstrip('/')}/api/tags"
    t0 = time.time()
    async with httpx.AsyncClient(timeout=timeout_s) as c:
        r = await c.get(url)
        r.raise_for_status()
        data = r.json()
    latency_ms = int((time.time() - t0) * 1000)
    models = [m.get("name") for m in (data.get("models") or []) if m.get("name")]
    return {
        "ok": True,
        "latency_ms": latency_ms,
        "models": models[:20],
        "info": f"Ollama · {len(models)} 个模型已下载",
    }


async def _probe_openai_chat(
    base_url: str, model: str, api_key: str, timeout_s: float = 8.0
) -> dict:
    """OpenAI 兼容（private / cloud）真实探测：POST /chat/completions 单 token 调用。

    max_tokens=1 + 极短 prompt 是行业标准（不消耗配额 / 不计入账单）。
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 1,
        "temperature": 0.0,
    }
    t0 = time.time()
    async with httpx.AsyncClient(timeout=timeout_s) as c:
        r = await c.post(url, json=payload, headers=headers)
        r.raise_for_status()
        body = r.json()
    latency_ms = int((time.time() - t0) * 1000)
    # 取返回的 model 字段（云端有时会回真实路由的 model）
    actual_model = body.get("model") or model
    return {
        "ok": True,
        "latency_ms": latency_ms,
        "actual_model": actual_model,
        "info": f"OpenAI 兼容 · {actual_model}",
    }


@router.post("/backends/test-connection")
async def test_backend_connection(body: dict = Body(...)) -> dict:
    """真实测试后端连通性（不在 storage 落库，仅探测）。

    请求体（任一字段缺省时按 type 选默认探测策略）：
      - type: 'local' (Ollama) / 'private' / 'cloud'
      - base_url: 必填
      - model: 必填
      - api_key: cloud 必填（走 Keyring 解析），private 可选
      - timeout_s: 可选，默认 local=5 / 其它=8

    返回：
      ok: bool
      latency_ms: 探测耗时
      info: 人类可读一行（前端 toast 直接展示）
      detail: 详细字段（models 列表 / actual_model）
      error: 失败时的错误信息（HTTPStatus / Connect / Timeout 等）
    """
    btype = (body.get("type") or "").strip().lower()
    base_url = (body.get("base_url") or "").strip()
    model = (body.get("model") or "").strip()
    api_key = body.get("api_key") or ""
    timeout_s = float(body.get("timeout_s") or (5.0 if btype == "local" else 8.0))

    if not base_url:
        raise HTTPException(status_code=400, detail="base_url 必填")
    if not model:
        raise HTTPException(status_code=400, detail="model 必填")

    # 云端/内网只支持 OpenAI 兼容端点；Anthropic 风格路径（如 MiniMax /anthropic）
    # 会被 OpenAI 探测打出裸 404，提前给可操作提示（真实推理链路同样只支持 OpenAI 协议）。
    if btype != "local" and _is_anthropic_endpoint(base_url):
        return {
            "ok": False,
            "error": (
                "检测到 Anthropic 兼容端点（路径含 /anthropic 或 /messages）：当前模型探测"
                "仅支持 OpenAI 兼容端点（POST {base}/chat/completions）。"
                "请改用该服务商的 OpenAI 兼容 base_url，"
                "MiniMax 示例：https://api.minimaxi.com/v1"
            ),
        }

    try:
        if btype == "local":
            res = await _probe_ollama(base_url, timeout_s=timeout_s)
        else:
            # private / cloud 都走 OpenAI 兼容协议（max_tokens=1 探测）
            res = await _probe_openai_chat(base_url, model, api_key, timeout_s=timeout_s)
        return res
    except httpx.HTTPStatusError as e:
        # 404 通常不是模型不存在，而是 base_url 协议/路径不对（缺 /v1 等）
        hint = (
            "（请确认 base_url 为 OpenAI 兼容端点，路径含 /v1，如 https://api.minimaxi.com/v1）"
            if e.response.status_code == 404
            else ""
        )
        return {
            "ok": False,
            "latency_ms": int((time.time() - time.time()) * 0) + 0,
            "error": f"HTTP {e.response.status_code}: {e.response.text[:200]}{hint}",
        }
    except httpx.ConnectError as e:
        return {
            "ok": False,
            "error": f"连接失败：{e.__class__.__name__} · {str(e)[:150]}",
        }
    except httpx.TimeoutException as e:
        return {
            "ok": False,
            "error": f"超时（{timeout_s}s）：{str(e)[:150]}",
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{e.__class__.__name__}: {str(e)[:200]}",
        }


# ---- 委托接口（供 LMRouter 内部调用） ----


def route_via_engine(
    task_kind: str,
    user_prompt: str,
    sensitivity: str = "internal",
    request_id: str = "",
    estimated_tokens: int = 1000,
) -> RoutingDecision:
    """LMRouter 4 个公开 API 内部调这个，按 task_kind 推断 role + sensitivity。"""
    eng = _get_engine()
    role_map = {
        "intent": "utility",  # 端侧小模型
        "plan": "reasoning",  # 推理模型
        "repair": "reasoning",  # 推理模型
        "summarise": "execution",  # 复杂模型
    }
    role_map.get(task_kind, "execution")
    cat_map = {
        "intent": TaskCategory.SIMPLE,
        "plan": TaskCategory.COMPLEX,
        "repair": TaskCategory.MEDIUM,
        "summarise": TaskCategory.MEDIUM,
    }
    category = cat_map.get(task_kind, TaskCategory.MEDIUM)
    sens_map = {
        "public": Sensitivity.PUBLIC,
        "internal": Sensitivity.INTERNAL,
        "pii": Sensitivity.PII,
        "production": Sensitivity.PRODUCTION,
    }
    sens = sens_map.get(sensitivity, Sensitivity.INTERNAL)
    return eng.route_request(
        task_kind=task_kind,
        category=category,
        sensitivity=sens,
        request_id=request_id,
        estimated_tokens=estimated_tokens,
    )
