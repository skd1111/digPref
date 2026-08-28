"""FastAPI entrypoint for the control-layer Agent.

Run with:
    uv run uvicorn agent.main:app --reload --host 127.0.0.1 --port 8765

Lifespan management:
    - On startup: build Runtime(llm, mcp), compile the LangGraph state
      machine, store on `app.state`.
    - On shutdown: cleanly close the MCP client pool.
"""

from __future__ import annotations

# 离线驱动加载 —— 必须在所有业务 import 之前
from agent.driver_bootstrap import load_drivers

load_drivers()

# PPT Master 捆绑运行时（嵌入式 Python + 离线依赖解压）—— best-effort 不阻断启动
from agent.ppt_master_bootstrap import ensure_ppt_master_runtime

ensure_ppt_master_runtime()

# LLM active 后端统一配置 —— 必须在 agent.config 加载前应用
# （router.db llm_kv 为唯一长期事实源；遗留 llm-config.json 启动时迁移）
from agent.llm.active_config import apply_active

apply_active()

import asyncio
import logging
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from agent.api import approval, chat, health, ws
from agent.audit.store import audit
from agent.config import settings
from agent.envconfig import api as envconfig_api
from agent.graph.compile import Runtime, compile_graph
from agent.llm.router import LMRouter
from agent.mcp.registry import ServerRegistry

if TYPE_CHECKING:
    from agent.mcp.client import McpClient

# ---- 独立文件日志（PyInstaller / 子进程被 Rust 拉起时方便排查）---------
# 日志落在工作目录（打包后 = 安装目录）logs/；失败时回退用户目录。
try:
    _AGENT_LOG = Path("logs") / "agent.log"
    _AGENT_LOG.parent.mkdir(parents=True, exist_ok=True)
except OSError:
    _AGENT_LOG = Path.home() / ".eaide" / "agent.log"
    _AGENT_LOG.parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    filename=str(_AGENT_LOG),
    level=logging.DEBUG,
    format="%(asctime)s.%(msecs)03d [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("agent.main")

# ---- CoT 专用日志（logs/cot.log）：意图识别 / 思维链全链路汇聚单文件分析用
from agent.observability.cot_log import get_cot_logger

get_cot_logger()


# ---- Runtime cache ---------------------------------------------------------

_runtime: Runtime | None = None
_compiled_graph = None


def get_runtime() -> Runtime:
    """Return the global Runtime. Lazily built so unit tests can inject mocks."""
    global _runtime
    if _runtime is None:
        _runtime = Runtime(llm=LMRouter(), mcp=_build_mcp())
    return _runtime


def get_compiled_graph():
    """Return the compiled LangGraph. Built once at startup."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = compile_graph(get_runtime())
    return _compiled_graph


def set_runtime_for_testing(runtime: Runtime, compiled=None) -> None:
    """Test hook — inject a mock runtime and pre-compiled graph."""
    global _runtime, _compiled_graph
    _runtime = runtime
    _compiled_graph = compiled or compile_graph(runtime)


def _build_mcp():
    """Construct an MCP client from the registry (returns None if no servers)."""
    try:
        registry = ServerRegistry.from_yaml(settings.mcp_config_path)
        if not registry.servers:
            return None

        # We can't await here (sync __init__). Caller must wrap in async context.
        # We return a factory that chat.py can call inside its endpoint.
        return _LazyMcp(registry)
    except FileNotFoundError:
        return None


class _LazyMcp:
    """MCP 客户端代理 —— 延迟初始化，复用长生命周期连接。

    借鉴 VSCode 语言服务器协议 (LSP) 的设计：
        - MCP 子进程在首次使用时启动，进程生命周期内保持存活
        - 所有工具调用共享同一组 stdio 连接，避免反复创建/销毁
        - Agent 关闭时统一清理（通过 lifespan shutdown 触发）
    """

    def __init__(self, registry: ServerRegistry) -> None:
        self._registry = registry
        self._client: McpClient | None = None
        self._lock = asyncio.Lock()

    async def _ensure_open(self) -> McpClient:
        """获取或创建共享的 MCP 客户端（懒加载 + 双重检查锁）。"""
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is not None:
                return self._client
            from agent.mcp.client import McpClient

            self._client = McpClient(self._registry)
            await self._client.__aenter__()
            return self._client

    async def list_tools(self):
        c = await self._ensure_open()
        return await c.list_tools()

    async def invoke(self, call, *, timeout_sec, row_limit):
        c = await self._ensure_open()
        return await c.invoke(call, timeout_sec=timeout_sec, row_limit=row_limit)

    async def close(self):
        """关闭所有 MCP 连接。由 lifespan shutdown 调用。"""
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None


async def reload_mcp_clients() -> list[str]:
    """重读 mcp.yaml 并重建 MCP 客户端（设置页「MCP」热重载入口）。

    关闭既有连接后整体重建；文件缺失 / 空表 → mcp=None（与启动时语义一致）。
    Runtime.mcp 是动态引用（tool catalog / chat 每轮取），替换后即刻生效。
    """
    runtime = get_runtime()
    old = runtime.mcp
    if isinstance(old, _LazyMcp):
        await old.close()
    runtime.mcp = _build_mcp()
    servers: list[str] = []
    registry = getattr(runtime.mcp, "_registry", None)
    if registry is not None and hasattr(registry, "servers"):
        servers = list(registry.servers.keys())
    log.info("MCP clients reloaded: %s", servers)
    return servers


# ---- Lifespan --------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: pre-build the runtime + compiled graph so the first request
    # doesn't pay the latency tax.
    runtime = get_runtime()
    graph = get_compiled_graph()
    app.state.runtime = runtime
    app.state.graph = graph
    # Phase 1B V2: 清理 Tauri 运行时注入（幂等）。
    # 桌面壳（Tauri）集成方在 Agent 就绪后通过
    # `agent.builtin._tauri_runtime.set_tauri_runtime(client)` 注入运行时客户端，
    # 使 dispatcher 的 Rust 工具走 Tauri Command；独立运行（uvicorn / exe）无注入 →
    # 3 高危工具走 Python 原生兜底。
    try:
        from agent.builtin._tauri_runtime import clear_tauri_runtime

        clear_tauri_runtime()
    except Exception:
        pass
    # 执行过程可视化（阶段二）：独立部署形态（无桌面壳注入）拉起
    # eaide-executor 子进程并注入 —— 9 个 Rust 工具统一走 Rust 沙箱实现；
    # 二进制缺失 / 启动失败时返 None，保持「Python 原生兜底」既有降级。
    executor_client = None
    try:
        from agent.builtin._tauri_runtime import set_tauri_runtime
        from agent.builtin.jsonrpc_stdio import try_start_executor_client

        executor_client = await try_start_executor_client()
        if executor_client is not None:
            set_tauri_runtime(executor_client)
    except Exception:
        log.exception("eaide-executor injection failed; keep python-native fallback")
    # Best-effort: discover which MCP servers are configured (only works for
    # the lazy wrapper, not for test-injected mocks — silently skip).
    servers: list[str] = []
    mcp = runtime.mcp
    registry = getattr(mcp, "_registry", None)
    if registry is not None and hasattr(registry, "servers"):
        servers = list(registry.servers.keys())
    await audit("agent.startup", {"mcp_servers": servers})
    # Phase 7 补齐：定时报表调度器启动（缺口 7；失败不阻断 Agent 启动）
    data_scheduler = None
    try:
        from agent.dataexpert.scheduler import ReportScheduler
        from agent.dataexpert.storage import get_default_storage

        data_scheduler = ReportScheduler(get_default_storage())
        await data_scheduler.start()
    except Exception:
        log.exception("data scheduler startup failed")
    app.state.data_scheduler = data_scheduler
    try:
        yield
    finally:
        # 执行过程可视化（阶段二）：关闭 eaide-executor 子进程（幂等）
        if executor_client is not None:
            try:
                from agent.builtin._tauri_runtime import clear_tauri_runtime

                clear_tauri_runtime()
                await executor_client.stop()
            except Exception:
                log.exception("eaide-executor shutdown failed")
        # Phase 7 补齐：停止定时报表调度器（幂等）
        if data_scheduler is not None:
            try:
                await data_scheduler.stop()
            except Exception:
                log.exception("data scheduler shutdown failed")
        # Shutdown: 关闭 MCP 客户端连接池
        if isinstance(mcp, _LazyMcp):
            await mcp.close()
        # Phase 15 V0: 关闭预览 Vite 子进程（幂等）
        try:
            from agent.preview.session_manager import get_default_manager

            await get_default_manager().shutdown()
        except Exception:
            log.exception("preview shutdown failed")
        await audit("agent.shutdown", {})


# ---- App factory -----------------------------------------------------------


def create_app() -> FastAPI:
    app = FastAPI(title="EAIDE Agent", version="0.1.0", lifespan=lifespan)
    app.include_router(chat.router)
    app.include_router(approval.router)
    app.include_router(health.router)
    app.include_router(ws.router)
    app.include_router(envconfig_api.router)
    # MCP 服务器配置管理（设置页「MCP」面板：读 / 写 / 连通性测试 / 热重载）
    from agent.api import mcp_config as mcp_config_api

    app.include_router(mcp_config_api.router)
    # Phase 18: 自动模式授权审计（会话级 autonomy 开启确认）
    from agent.api import autonomy as autonomy_api

    app.include_router(autonomy_api.router)

    # Phase 18: 工具链路径配置（设置页面板）
    from agent.api import toolchain as toolchain_api

    app.include_router(toolchain_api.router)
    # 工作空间路径配置（设置页面板；底层规则：创建类文件默认落工作空间）
    from agent.api import workspace as workspace_api

    app.include_router(workspace_api.router)
    # Phase 2C V2.5: LLM 路由（4 端点 + 后端 CRUD）
    from agent.llm import engine_api

    app.include_router(engine_api.router)

    # Token 用量（状态栏实时速率 + 当日总量；前端 2s 轮询）
    from agent.llm import usage_api

    app.include_router(usage_api.router)

    # Phase 2F: 代码导航（jump / index / status / symbols）
    from agent.codenav import api as codenav_api

    app.include_router(codenav_api.router)

    # Phase 2G V1.1 (2026-07-28): 业务功能点导航（10 路由）
    from agent.biznav import api as biznav_api

    app.include_router(biznav_api.router)

    # reqflow V1 (2026-08-05): 运营专家需求改造工作流（需求卡片，11 路由）
    from agent.reqflow import api as reqflow_api

    app.include_router(reqflow_api.router)

    # Phase 2H (2026-08-07): 运营工作台业务记录（业务小结卡片，可审计）
    from agent.ops import api as ops_api

    app.include_router(ops_api.router)

    # Phase 2H (2026-08-07): 数据字典（公共参数独立维护，Skill 按 key 引用）
    from agent.datadict import api as dict_api

    app.include_router(dict_api.router)

    from agent.sessions import api as sessions_api

    app.include_router(sessions_api.router)

    # Phase 4 V0: 本地端侧模型
    from agent.localai import api as localai_api

    app.include_router(localai_api.router)

    # Phase 4 V0: 外部知识库适配器
    from agent.knowledge import api as knowledge_api

    app.include_router(knowledge_api.router)

    # Phase 2D: Skill/MCP 生态（list/get/save/delete/import/export/reload）
    from agent.skills import api as skills_api

    app.include_router(skills_api.router)
    skills_api.init_loader()

    # 专家团资产（list/get/save/delete/import/export/recommend）
    from agent.expert_teams import api as expert_teams_api

    app.include_router(expert_teams_api.router)
    expert_teams_api.init_loader()

    # Phase 2F+ V1: 日志分析（extract / root-cause / log-level-classify / cache）
    from agent.loganalysis import api as loganalysis_api

    app.include_router(loganalysis_api.router)

    # Phase 14 V0: 图像处理（enhance / correct / ocr + tasks + stats）
    from agent.image_processing import image_api_router

    app.include_router(image_api_router)

    # Phase 15 V0: 前端实时预览引擎（start / stop / sessions / info / reload / install / stream）
    from agent.preview import preview_api_router

    app.include_router(preview_api_router)

    # V9 Office 预览（2026-08-25：OfficeCLI 渲染 docx/xlsx/pptx → HTML/PNG）
    from agent.office_preview import office_preview_router

    app.include_router(office_preview_router)

    # Phase 2B V0: SSH PTY PoC（connect / disconnect / exec / sftp / sessions / stats）
    from agent.ssh import ssh_api_router

    app.include_router(ssh_api_router)

    # Phase 5 V0: 审核专家（tasks + evidence + compliance + decide + verify + stats）
    from agent.audit_expert import audit_api_router

    app.include_router(audit_api_router)

    # 文档风险合规审核（审核专家 · 文档审核）
    from agent.doc_review import doc_review_api_router

    app.include_router(doc_review_api_router)

    # Phase 7 V0: 数据专家（sources + nl2sql + sql/run + python/run + chart + export + templates）
    from agent.dataexpert import data_api_router

    app.include_router(data_api_router)

    # Phase 16: 思维链可视化与文件操作追踪（/trace/session / step / file-diff）
    from agent.trace import api as trace_api

    app.include_router(trace_api.router)

    # Phase 12 V0: 多智能体调度（最小骨架 —— spec / spawn / list / events）
    from agent.orchestrator import api as orch_api

    app.include_router(orch_api.router)

    # Phase 12 V0: 启动时把 LMRouter 注入 Orchestrator（V1：lifespan 内就绪后注入）
    try:
        from agent.main import get_runtime
        from agent.orchestrator.orchestrator import reset_orchestrator

        reset_orchestrator(router=get_runtime().llm)
    except Exception as e:
        # runtime 还没就绪 → V0 走 fallback（直接 await llm_router.route）
        log.warning("[orchestrator] reset skipped (runtime not ready): %s", e)

    # Phase 13 DSpark V0: 推测解码决策层（4 端点 + 启动时初始化 runtime）
    try:
        from agent.llm.dspark.api import _load_dspark_config, init_dspark_runtime
        from agent.llm.dspark.config import DSparkConfig
        from agent.llm.router import _LOCAL_ONLY_TASKS

        # 持久化配置优先于 env var（用户在 UI 保存过就以 UI 为准）
        persisted = _load_dspark_config() or {}
        dspark_cfg = DSparkConfig(
            draft_model_path=persisted.get("draft_model_path") or settings.dspark_draft_model_path,
            short_output_threshold=persisted.get(
                "short_output_threshold", settings.dspark_short_output_threshold
            ),
            enable_global=persisted.get("enable_global", settings.dspark_enable_global),
            context_size=persisted.get("context_size", settings.dspark_context_size),
            gpu_layers=persisted.get("gpu_layers", settings.dspark_gpu_layers),
        )
        init_dspark_runtime(
            config=dspark_cfg,
            yaml_path=settings.dspark_yaml_path,
            local_only_tasks=list(_LOCAL_ONLY_TASKS),
        )
        from agent.llm.dspark import api as dspark_api

        app.include_router(dspark_api.router)
        log.info(
            "[dspark] runtime initialized, yaml=%s, draft=%s",
            settings.dspark_yaml_path,
            dspark_cfg.draft_model_path,
        )
    except Exception as e:
        # DSpark 挂载失败不能阻塞 Agent 启动
        log.warning("[dspark] failed to init runtime: %s", e)

    # 全局异常处理 —— 把 traceback 写文件 + 返 500 JSON
    @app.exception_handler(Exception)
    async def _global_exc(request: Request, exc: Exception):
        tb = traceback.format_exc()
        log.error("unhandled exception on %s %s: %s", request.method, request.url.path, exc)
        log.error("%s", tb)
        return JSONResponse(
            status_code=500,
            content={"detail": f"{type(exc).__name__}: {exc}"},
        )

    return app


app = create_app()


def run() -> None:
    """Console-script entry: `eaide-agent`.

    PyInstaller 冻结后 uvicorn 字符串导入 ("agent.main:app") 拿不到 module
    —— 这里直接传 app 对象。开发模式（uv run）下也工作。
    """
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        reload=settings.dev,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    run()
