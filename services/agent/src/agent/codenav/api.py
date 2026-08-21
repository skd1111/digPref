"""FastAPI /codenav/* 路由。

端点：
  POST /codenav/jump     — 符号跳转（SQLite 命中 + AI 兜底）
  POST /codenav/explain  — 解释符号语义（LLM）
  POST /codenav/index    — 手动触发全量索引
  POST /codenav/check    — 语法错误检查（tree-sitter，2026-08-19）
  GET  /codenav/status   — 索引状态
  GET  /codenav/symbols  — 搜索符号
  GET  /codenav/llm-config — 当前 LLM 配置状态（不泄露 key）
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.codenav.indexer import WorkspaceIndexer
from agent.codenav.llm_client import (
    get_default_client,
    reset_default_client,
    resolve_codenav_backend,
)
from agent.codenav.mcp_tools import explain_symbol, resolve_jump
from agent.codenav.query import SymbolQuery
from agent.codenav.syntax_check import check_syntax
from agent.paths import data_root

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/codenav", tags=["codenav"])


# ---------------------------------------------------------------------------
# 单例 indexer + query（启动时初始化一次）
# ---------------------------------------------------------------------------


def _default_db_path() -> str:
    # BUGFIX #98：统一落数据根（生产=安装目录）
    return str(data_root() / "workspace_index.db")


def _default_root_paths() -> list[str]:
    env = os.environ.get("EAIDE_CODE_NAV_ROOTS")
    if env:
        return [p for p in env.split(os.pathsep) if p]
    # 默认：当前进程 cwd
    return [os.getcwd()]


_indexer: WorkspaceIndexer | None = None
_query: SymbolQuery | None = None


def _get_indexer() -> WorkspaceIndexer:
    global _indexer
    if _indexer is None:
        _indexer = WorkspaceIndexer(
            db_path=os.environ.get("EAIDE_WORKSPACE_INDEX_DB", _default_db_path()),
            root_paths=os.environ.get("EAIDE_CODE_NAV_ROOTS", "").split(os.pathsep)
            if os.environ.get("EAIDE_CODE_NAV_ROOTS")
            else _default_root_paths(),
        )
    return _indexer


def _get_query() -> SymbolQuery:
    global _query
    if _query is None:
        _query = SymbolQuery(_get_indexer()._db_path)
    return _query


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class JumpRequest(BaseModel):
    symbol: str
    current_file: str
    context: str = ""
    line: int = 0


class ExplainRequest(BaseModel):
    symbol: str
    current_file: str
    line: int = 0
    context: str = ""
    # V1 Phase 12：用户从编辑器选中的范围（start_line/end_line + 选中文本）
    # 后端会用它改写 system prompt —— 「你正在解释用户选中的代码」
    selection_start_line: int | None = None
    selection_end_line: int | None = None
    selection_text: str | None = None


class IndexRequest(BaseModel):
    root_paths: list[str] | None = None
    add_roots: list[str] | None = None  # 用户从 UI 追加的额外目录（白名单内）
    files: list[str] | None = None  # 用户从 UI 指定的单文件列表


# 语法检查（2026-08-19）：file_path 只用于按后缀选语法，不落盘不读盘；
# content 上限 2MB（防异常大文件把解析卡死）
_CHECK_MAX_CONTENT_CHARS = 2_000_000


class CheckRequest(BaseModel):
    file_path: str
    content: str


# 允许的根路径白名单：用户能在 UI 里选的最高边界
# - 用户家目录（Documents / Desktop / Projects）
# - 当前工作目录
# - 通过环境变量 EAIDE_CODENAV_EXTRA_ROOTS 追加（分号分隔）
def _allowed_roots() -> list[Path]:
    roots: list[Path] = []
    home = Path(os.path.expanduser("~"))
    for sub in ("Documents", "Desktop", "Projects", "code", "workspace"):
        p = home / sub
        if p.exists():
            roots.append(p.resolve())
    cwd = Path(os.getcwd()).resolve()
    if cwd not in roots:
        roots.append(cwd)
    extra = os.environ.get("EAIDE_CODENAV_EXTRA_ROOTS", "")
    for token in extra.split(os.pathsep):
        if token.strip():
            p = Path(token).resolve()
            if p.exists():
                roots.append(p)
    return roots


def _validate_user_paths(paths: list[str]) -> list[str]:
    """V2 简化：只校验路径存在 + 类型；不做白名单校验。

    用户已经通过 Tauri 对话框选了路径，再加白名单反而挡路（用户选了一个
    D:\\code\\myproject 就因为不在 Documents/Desktop/Projects/cwd 白名单被拒，
    体验差）。安全靠 Tauri 对话框的目录选择器天然防御。
    """
    out: list[str] = []
    for p in paths:
        if not p or not isinstance(p, str):
            raise HTTPException(status_code=400, detail=f"invalid path: {p!r}")
        path = Path(p)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"path not found: {p}")
        out.append(str(path.resolve()))
    return out


def _is_within_allowed(path: Path) -> bool:
    """兼容旧接口：V2 总返回 True。"""
    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/jump")
async def code_nav_jump(req: JumpRequest) -> dict:
    """符号跳转：先查 SQLite，未命中走 LLM。"""
    if not req.symbol:
        raise HTTPException(status_code=400, detail="symbol required")
    result = await resolve_jump(
        symbol=req.symbol,
        current_file=req.current_file,
        context=req.context,
        llm_client=get_default_client(),
    )
    return {
        "file_path": result.file_path,
        "line": result.line,
        "confidence": result.confidence,
        "source": result.source,
        "note": result.note,
    }


@router.post("/check")
async def code_nav_check(req: CheckRequest) -> dict:
    """语法错误检查（tree-sitter）。不落盘、不读盘、不依赖索引：

    前端把编辑器当前内容（可能未保存）直接传进来，按 file_path 后缀选语法
    解析，返语法级诊断列表（行列 1-based）。不支持的后缀 supported=False。
    """
    if not req.file_path:
        raise HTTPException(status_code=400, detail="file_path required")
    if len(req.content) > _CHECK_MAX_CONTENT_CHARS:
        raise HTTPException(status_code=413, detail="content too large (max 2MB)")

    lang_id, diagnostics = check_syntax(req.file_path, req.content)
    return {
        "ok": True,
        "supported": bool(lang_id),
        "language": lang_id,
        "diagnostics": [d.to_dict() for d in diagnostics],
    }


@router.post("/explain")
async def code_nav_explain(req: ExplainRequest) -> dict:
    """解释符号语义。配置 LLM 时返回真实解释，否则返回占位文本。

    V1 Phase 12：用户可在编辑器选中一段代码后右键触发；后端会把「你正在解释用户
    选中的代码（X-Y 行）」写入 system prompt，选中文本作为 user message 主体。
    """
    if not req.symbol:
        raise HTTPException(status_code=400, detail="symbol required")
    result = await explain_symbol(
        symbol=req.symbol,
        current_file=req.current_file,
        line=req.line,
        context=req.context,
        selection=(
            (req.selection_start_line, req.selection_end_line, req.selection_text)
            if (req.selection_start_line is not None and req.selection_text)
            else None
        ),
        llm_client=get_default_client(),
    )
    return {
        "symbol": req.symbol,
        "text": result["text"],
        "source": result["source"],
        "confidence": result["confidence"],
        "backend": result.get("backend"),
    }


@router.post("/explain/stream")
async def code_nav_explain_stream(req: ExplainRequest) -> StreamingResponse:
    """流式解释：NDJSON 增量输出，正文自动剥离 think 推理内容。

    每行一个 JSON：
      {"delta": "..."}                  —— 正文增量（不含 think）
      {"done": true, "text": "...", "source": "llm"|"mock", ...}  —— 结束帧
      {"error": "..."}                 —— 出错帧（随后无 done）
    """

    async def gen():
        client = get_default_client()
        selection = (
            (req.selection_start_line, req.selection_end_line, req.selection_text)
            if (req.selection_start_line is not None and req.selection_text)
            else None
        )
        if not client.configured:
            mock_text = "（mock）语义解释占位 —— 启用真 LLM 后将生成完整说明。"
            yield json.dumps({"delta": mock_text}, ensure_ascii=False) + "\n"
            yield (
                json.dumps(
                    {
                        "done": True,
                        "text": mock_text,
                        "source": "mock",
                        "confidence": 0.0,
                        "backend": None,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            return
        chunks: list[str] = []
        try:
            async for piece in client.explain_symbol_stream(
                symbol=req.symbol,
                current_file=req.current_file,
                line=req.line,
                context=req.context,
                selection=selection,
            ):
                chunks.append(piece)
                yield json.dumps({"delta": piece}, ensure_ascii=False) + "\n"
            text = "".join(chunks).strip()
            # 流式空结果兜底：推理型模型的 think 段被截断未闭合时，正文剥离后
            # 为空 —— 改用非流式重试（更大 token 预算，让模型完成推理）。
            if not text:
                logger.info("codenav explain stream empty, retrying non-stream")
                fallback = await client.explain_symbol(
                    symbol=req.symbol,
                    current_file=req.current_file,
                    line=req.line,
                    context=req.context,
                    selection=selection,
                    max_tokens=2048,
                )
                text = (fallback or "").strip()
                if text:
                    yield json.dumps({"delta": text}, ensure_ascii=False) + "\n"
            if not text:
                yield (
                    json.dumps(
                        {
                            "error": "模型未返回解释内容（推理可能被截断），请重试或更换 backend",
                            "source": "llm",
                            "backend": client.model,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
                return
            yield (
                json.dumps(
                    {
                        "done": True,
                        "text": text,
                        "source": "llm",
                        "confidence": 0.85,
                        "backend": client.model,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
        except Exception as e:
            logger.warning("codenav explain stream failed: %s", e)
            yield json.dumps({"error": str(e)}, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@router.get("/llm-config")
async def code_nav_llm_config() -> dict:
    """返回当前 LLM 配置状态（不泄露 key）。用于前端 Settings 展示。"""
    client = get_default_client()
    return {
        "configured": client.configured,
        "base_url": client.base_url or None,
        "model": client.model or None,
        "has_api_key": bool(client.api_key),
        "timeout_s": client.timeout_s,
        "max_context": client.max_context,
    }


@router.post("/llm-config/reload")
async def code_nav_llm_config_reload() -> dict:
    """强制重读环境变量（用户在 Settings 改完配置后调一次）。"""
    reset_default_client()
    return await code_nav_llm_config()


@router.get("/llm-backend")
async def code_nav_llm_backend() -> dict:
    """代码导航当前绑定的 backend（来自 router.db.feature_backend）。

    返回：
      bound: 当前 feature_backend 表里的 backend_name（可能为 null）
      resolved: 实际生效的 backend 信息（base_url / model / has_api_key / source）
      candidates: 所有可用 backends 列表（供前端下拉框）
    """
    from agent.llm.storage import get_feature_backend, list_backends

    bound = await get_feature_backend("codenav")
    cfg = await resolve_codenav_backend()
    backends = await list_backends()
    resolved = None
    if cfg:
        resolved = {
            "name": cfg.get("name"),
            "type": cfg.get("type"),
            "base_url": cfg.get("base_url"),
            "model": cfg.get("model"),
            "has_api_key": bool(cfg.get("api_key")),
            "source": (
                "router_db_bound"
                if (bound and cfg.get("name") == bound)
                else "router_db_default"
                if cfg.get("name") and cfg.get("name") != "env"
                else "env"
            ),
        }
    return {
        "bound": bound,
        "resolved": resolved,
        "candidates": [
            {
                "name": b.name,
                "type": b.type,
                "base_url": b.base_url,
                "model": b.model_name,
                "enabled": b.enabled,
            }
            for b in backends
        ],
    }


class BindBackendRequest(BaseModel):
    backend_name: str | None = None  # null/空 = 解绑


@router.post("/llm-backend/bind")
async def code_nav_llm_backend_bind(req: BindBackendRequest) -> dict:
    """绑定 / 解绑代码导航用的 backend。

    backend_name=null → 解绑（代码导航走环境变量或 mock）。
    """
    from agent.llm.storage import set_feature_backend

    name = (req.backend_name or "").strip() or None
    await set_feature_backend("codenav", name)
    reset_default_client()  # 让下次请求重新解析
    return await code_nav_llm_backend()


@router.post("/index")
async def trigger_index(req: IndexRequest | None = None) -> dict:
    """手动触发全量索引。

    三种用法（可叠加）：
      1. root_paths: 临时换 root（不修改单例）—— 主要给 V0/开发用
      2. add_roots:  把这些目录追加到单例 root，再跑 full_scan（白名单校验）
      3. files:      单文件列表走 incremental_update（白名单校验）
    """
    indexer = _get_indexer()
    extra_dirs: list[str] = []
    extra_files: list[str] = []

    if req and req.add_roots:
        validated = _validate_user_paths(req.add_roots)
        extra_dirs.extend(validated)

    if req and req.files:
        validated = _validate_user_paths(req.files)
        extra_files.extend(validated)

    # root_paths 临时 indexer
    if req and req.root_paths:
        tmp = WorkspaceIndexer(db_path=indexer._db_path, root_paths=req.root_paths)
        status = await tmp.full_scan()
    elif extra_dirs:
        # 把 add_roots 加到单例 root，跑全量扫描
        merged_roots = list(indexer._root_paths) + [Path(p) for p in extra_dirs]
        tmp = WorkspaceIndexer(db_path=indexer._db_path, root_paths=merged_roots)
        status = await tmp.full_scan()
        # 同步更新单例（之后查询看到新 root）
        indexer._root_paths = merged_roots
    else:
        status = await indexer.full_scan()

    # 单文件 incremental
    if extra_files:
        await indexer.incremental_update(extra_files)

    final_status = indexer.get_status()
    final_status.last_full_scan = final_status.last_full_scan or status.last_full_scan
    return final_status.to_dict()


@router.get("/allowed-roots")
async def allowed_roots() -> dict:
    """返回当前白名单路径（前端 Settings 提示用户可选范围）。"""
    return {
        "roots": [str(r) for r in _allowed_roots()],
        "extra_env": "EAIDE_CODENAV_EXTRA_ROOTS",
    }


# ---------------------------------------------------------------------------
# V3 路径护栏（path_guard）端点
# ---------------------------------------------------------------------------
# 用户语义：Agent 只能读写 opened_projects 内的路径；访问其他路径要弹确认框。
# 前端 File → Open Folder 后调用 sync-opened-projects 同步；Agent 任何
# tool_runner / hitl_gate 内部用 path_guard.check(path) 校验。


class OpenedProjectsRequest(BaseModel):
    folders: list[str]


class FolderRequest(BaseModel):
    folder: str


@router.post("/opened-projects/sync")
async def sync_opened_projects(req: OpenedProjectsRequest) -> dict:
    """用前端给的列表**整体替换** opened_projects（去重 + resolve）。

    启动时 + 每次用户 File → Open / Close Folder 时调用。
    """
    from agent.codenav import path_guard

    path_guard.init_opened_projects(req.folders)
    return {"opened_projects": path_guard.get_opened_projects()}


@router.get("/opened-projects")
async def list_opened_projects() -> dict:
    """返回当前 opened_projects 列表。"""
    from agent.codenav import path_guard

    return {"opened_projects": path_guard.get_opened_projects()}


@router.post("/opened-projects/add")
async def add_opened_project(req: FolderRequest) -> dict:
    """运行时追加一个项目（用户批准 Agent 访问新路径后调）。"""
    from agent.codenav import path_guard

    path_guard.add_opened_project(req.folder)
    return {"opened_projects": path_guard.get_opened_projects()}


@router.post("/opened-projects/remove")
async def remove_opened_project(req: FolderRequest) -> dict:
    """运行时移除一个项目。"""
    from agent.codenav import path_guard

    path_guard.remove_opened_project(req.folder)
    return {"opened_projects": path_guard.get_opened_projects()}


@router.get("/status")
async def index_status() -> dict:
    """索引状态。"""
    return _get_query().get_status().to_dict()


@router.get("/symbols")
async def list_symbols(name: str, kind: str | None = None, limit: int = 10) -> list[dict]:
    """搜索符号（GET 形式，方便 curl 调试）。"""
    q = _get_query()
    return [s.__dict__ for s in q.search(name, kind=kind, limit=limit)]
