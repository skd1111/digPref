"""biznav.api —— FastAPI 路由（Phase 2G V1.1，10 端点）。

端点：
  POST /biznav/extract                  — 启动后台提取任务，立即返回 job_id
  GET  /biznav/status                  — 项目最新任务状态
  GET  /biznav/features                — 列表（按 project_name 过滤，可选 category）
  GET  /biznav/features/{id}           — 详情
  PUT  /biznav/features/{id}           — 更新（乐观锁 expected_version）
  DELETE /biznav/features/{id}         — 软删除
  POST /biznav/import                  — 导入 YAML/JSON
  GET  /biznav/export                  — 导出 YAML/JSON
  GET  /biznav/affected                — V1.1 stub：返空 list
  POST /biznav/reload                  — V1.1 stub：返 503 "not implemented in V1.1"

设计要点：
- 全部 sync 路由（FeatureStorage 是 sync sqlite3）
- 内部 storage 单例：_get_storage(db_path=None) lazy init
- audit 异步：await audit(...)
- 后台 extract 用 asyncio.create_task 触发，不阻塞响应
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from .audit import (
    EVT_FEATURE_DELETE,
    EVT_FEATURE_EXTRACT,
    EVT_FEATURE_IMPORT,
    EVT_FEATURE_UPDATE,
    EVT_YAML_RELOAD,
)
from .import_export import FeatureIO, FeatureImportError
from .models import Feature
from .storage import FeatureStorage, FeatureVersionConflict, now
from .events import EVT_EXTRACTION_DONE, emit_biznav_event


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/biznav", tags=["biznav"])


# ---------------------------------------------------------------------------
# Pydantic 请求/响应模型
# ---------------------------------------------------------------------------


class ExtractRequest(BaseModel):
    project_name: str
    project_root: str


class ExtractResponse(BaseModel):
    job_id: int
    project_name: str
    status: str = "pending"


class UpdateFeatureRequest(BaseModel):
    project_name: str
    expected_version: int
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    related_files: Optional[list[dict[str, str]]] = None
    related_apis: Optional[list[dict[str, str]]] = None
    related_tables: Optional[list[dict[str, str]]] = None
    business_rules: Optional[list[dict[str, Any]]] = None
    source: Optional[str] = None


class ImportRequest(BaseModel):
    project_name: str
    yaml_text: Optional[str] = None
    json_text: Optional[str] = None
    merge: bool = True  # True → sync_yaml_to_db；False → 单纯解析不写库


# ---------------------------------------------------------------------------
# Storage 单例
# ---------------------------------------------------------------------------


def _default_db_path() -> str:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "eaide", "biznav.db")
    return os.path.expanduser("~/.eaide/biznav.db")


_storage: FeatureStorage | None = None


def _get_storage(db_path: Optional[str] = None) -> FeatureStorage:
    global _storage
    if db_path:
        return FeatureStorage(db_path)
    if _storage is None:
        _storage = FeatureStorage(
            os.environ.get("EAIDE_BIZNAV_DB", _default_db_path())
        )
    return _storage


# ---------------------------------------------------------------------------
# Inject LLM runtime (for extract)
# ---------------------------------------------------------------------------


def _make_llm_client():
    """构造一个 async callable(kind, messages) -> str。

    V1.2 (2026-07-28)：真接 LMRouter.summarise（V1.5 才会有专用 extract_chat 接口）。
    - `biznav_extract` → `_LOCAL_ONLY_TASKS` 已锁死 → LMRouter.pick 自动走本地 Ollama；
      后端不可达时降级 mock（保持现有测试通过）。
    - V1.1 时期返回空串的兜底保留：若 LMRouter 不可用 / 抛错，client 返回空串，
      extractor 走"该组不生成 feature"的兜底逻辑。
    """
    async def _client(kind: str, messages: list[dict]) -> str:
        try:
            from agent.llm.router import LMRouter

            # 每次 extract 重新构造一个 LMRouter（__init__ 廉价，仅查 router.db）
            router = LMRouter()
            backend = router.pick(kind)  # 自动按 _LOCAL_ONLY_TASKS 走 Ollama
            # summarise 期望 intent/user_prompt/plan/results，把最后一条 user 消息
            # 当作 user_prompt；plan/results 用空列表（extractor 不需要 plan）。
            user_prompt = ""
            for m in reversed(messages):
                if isinstance(m, dict) and m.get("role") == "user":
                    user_prompt = str(m.get("content", ""))
                    break
            # Intent 是 Literal（query/mutate/orchestrate/chitchat）；summarise
            # 实际只读 user_prompt，此处 "query" 是占位。
            text, _sources = await backend.summarise(
                intent="query",
                user_prompt=user_prompt,
                plan=[],
                results=[],
            )
            return text
        except Exception as e:  # noqa: BLE001 —— LLM 失败不阻塞 extract 流程
            logger.warning(
                "[biznav] _make_llm_client kind=%s failed: %s; extractor will skip this group",
                kind,
                e,
            )
            return ""

    return _client


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/extract", response_model=ExtractResponse)
async def biznav_extract(req: ExtractRequest) -> ExtractResponse:
    if not req.project_name or not req.project_root:
        raise HTTPException(400, "project_name and project_root required")
    root_path = Path(req.project_root)
    if not root_path.exists():
        raise HTTPException(404, f"project_root not found: {req.project_root}")

    storage = _get_storage()
    job_id = storage.create_job(req.project_name, str(root_path.resolve()))

    # 异步后台任务（不阻塞响应）
    async def _run():
        from .extractor import FeatureExtractor

        extractor = FeatureExtractor(
            storage=storage,
            llm_client=_make_llm_client(),
            project_root=str(root_path.resolve()),
            project_name=req.project_name,
            job_id=job_id,
        )
        success = False
        error_message: str | None = None
        features_generated = 0
        try:
            result = await extractor.extract_all()
            success = True
            # extractor 返回 ExtractionResult 时带 features 数量
            features_generated = getattr(result, "features_generated", 0)
        except Exception as e:  # 兜底
            logger.exception("[biznav] extract_all crashed: %s", e)
            error_message = str(e)
            storage.update_job(job_id, status="failed", error_message=error_message, finished=True)
        finally:
            # V1.3 SSE 推送：业务功能点提取任务完成（成功 / 失败 都推）
            try:
                emit_biznav_event(
                    EVT_EXTRACTION_DONE,
                    {
                        "job_id": job_id,
                        "project_name": req.project_name,
                        "project_root": str(root_path.resolve()),
                        "success": success,
                        "features_generated": features_generated,
                        "error": error_message,
                        "ts": int(time.time() * 1000),
                    },
                )
            except Exception as e:  # noqa: BLE001 —— SSE 不阻塞
                logger.warning("[biznav] emit biznav_extraction_done 失败: %s", e)

    asyncio.create_task(_run())

    return ExtractResponse(job_id=job_id, project_name=req.project_name, status="pending")


@router.get("/status")
async def biznav_status(project_name: str = Query(...)) -> dict:
    storage = _get_storage()
    job = storage.latest_job(project_name)
    if not job:
        return {"project_name": project_name, "has_job": False}
    return {"project_name": project_name, "has_job": True, "job": job}


@router.get("/features")
async def biznav_list_features(
    project_name: str = Query(...),
    category: Optional[str] = Query(None),
    include_deleted: bool = Query(False),
) -> dict:
    storage = _get_storage()
    features = storage.list_by_project(project_name, include_deleted=include_deleted)
    if category:
        features = [f for f in features if f.category == category]
    return {
        "project_name": project_name,
        "features": [f.to_dict() for f in features],
        "total": len(features),
    }


@router.get("/features/{feature_id}")
async def biznav_get_feature(
    feature_id: str,
    project_name: str = Query(...),
) -> dict:
    storage = _get_storage()
    f = storage.get(feature_id, project_name)
    if not f:
        raise HTTPException(404, f"feature {feature_id} not found")
    return f.to_dict()


@router.put("/features/{feature_id}")
async def biznav_update_feature(
    feature_id: str,
    body: UpdateFeatureRequest = Body(...),
) -> dict:
    storage = _get_storage()
    existing = storage.get(feature_id, body.project_name)
    if not existing:
        raise HTTPException(404, f"feature {feature_id} not found")

    # 构造新版本（保留关联字段未传值）
    new = Feature.from_dict(existing.to_dict())
    new.version = body.expected_version
    if body.name is not None:
        new.name = body.name
    if body.description is not None:
        new.description = body.description
    if body.category is not None:
        new.category = body.category
    if body.related_files is not None:
        from .models import RelatedFile

        new.related_files = [
            RelatedFile(path=str(rf.get("path", "")), role=str(rf.get("role", "")))
            for rf in body.related_files
        ]
    if body.related_apis is not None:
        from .models import RelatedApi

        new.related_apis = [
            RelatedApi(
                method=str(a.get("method", "")),
                path=str(a.get("path", "")),
                description=str(a.get("description", "")),
            )
            for a in body.related_apis
        ]
    if body.related_tables is not None:
        from .models import RelatedTable

        new.related_tables = [
            RelatedTable(name=str(t.get("name", "")), description=str(t.get("description", "")))
            for t in body.related_tables
        ]
    if body.business_rules is not None:
        from .models import BusinessRule

        new.business_rules = [
            BusinessRule.from_dict(r) for r in body.business_rules
        ]
    # source 转换：UI 编辑（PUT）→ 默认 'manual'（设计文档 §11.1）；除非显式指定
    new.source = body.source or "manual"

    try:
        storage.upsert(new)
    except FeatureVersionConflict as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, f"validation failed: {e}")

    # audit
    try:
        from agent.audit.store import audit

        await audit(
            EVT_FEATURE_UPDATE,
            {
                "feature_id": feature_id,
                "project_name": body.project_name,
                "expected_version": body.expected_version,
                "new_version": new.version,
            },
        )
    except Exception as e:
        logger.warning("[biznav] audit update failed: %s", e)

    return new.to_dict()


@router.delete("/features/{feature_id}")
async def biznav_delete_feature(
    feature_id: str,
    project_name: str = Query(...),
    hard: bool = Query(False),
) -> dict:
    storage = _get_storage()
    existing = storage.get(feature_id, project_name)
    if not existing:
        raise HTTPException(404, f"feature {feature_id} not found")
    if hard:
        storage.delete(feature_id, project_name)
    else:
        storage.soft_delete(feature_id, project_name)

    try:
        from agent.audit.store import audit

        await audit(
            EVT_FEATURE_DELETE,
            {
                "feature_id": feature_id,
                "project_name": project_name,
                "hard": hard,
            },
        )
    except Exception as e:
        logger.warning("[biznav] audit delete failed: %s", e)

    return {"ok": True, "feature_id": feature_id, "hard": hard}


@router.post("/import")
async def biznav_import(req: ImportRequest) -> dict:
    storage = _get_storage()
    if not req.yaml_text and not req.json_text:
        raise HTTPException(400, "yaml_text or json_text required")
    try:
        if req.yaml_text:
            features = FeatureIO.from_yaml(req.yaml_text)
        else:
            features = FeatureIO.from_json(req.json_text)
    except FeatureImportError as e:
        raise HTTPException(400, f"import failed: {e}")

    if not req.merge:
        # 只解析不写库
        return {
            "parsed": len(features),
            "merged": 0,
            "features": [f.to_dict() for f in features],
        }

    # 走 sync_yaml_to_db 路径（即使 JSON 来源也用相同合并策略）
    report = {"inserted": 0, "updated": 0, "skipped": 0, "conflicts": []}
    if req.yaml_text:
        sync = FeatureIO.sync_yaml_to_db(req.yaml_text, req.project_name, storage)
        report = {
            "inserted": sync.inserted,
            "updated": sync.updated,
            "skipped": sync.skipped,
            "conflicts": sync.conflicts,
        }
    else:
        for f in features:
            f.project_name = req.project_name
            existing = storage.get(f.id, req.project_name)
            if existing is None:
                f.source = "merged"
                storage.upsert(f)
                report["inserted"] += 1
            elif existing.source == "manual":
                report["skipped"] += 1
                report["conflicts"].append(
                    {"feature_id": f.id, "reason": "manual source preserved"}
                )
            else:
                f.source = "ai"
                f.created_at = existing.created_at
                storage.upsert(f)
                report["updated"] += 1

    try:
        from agent.audit.store import audit

        await audit(
            EVT_FEATURE_IMPORT,
            {
                "project_name": req.project_name,
                "merge": req.merge,
                **report,
            },
        )
    except Exception as e:
        logger.warning("[biznav] audit import failed: %s", e)

    return {"ok": True, **report}


@router.get("/export")
async def biznav_export(
    project_name: str = Query(...),
    project_root: str = Query(""),
    format: str = Query("yaml"),
) -> dict:
    storage = _get_storage()
    if format == "json":
        features = storage.list_by_project(project_name, include_deleted=False)
        body = FeatureIO.to_json(features)
    else:
        body = FeatureIO.sync_db_to_yaml(
            storage,
            project_name=project_name,
            project_root=project_root,
        )
    return {"format": format, "project_name": project_name, "body": body}


@router.get("/affected")
async def biznav_affected(
    project_name: str = Query(...),
    file_path: str = Query(""),
) -> dict:
    """V1.1 stub —— V1.3 incremental 实现：返回 file_path → features 映射。"""
    return {"project_name": project_name, "file_path": file_path, "affected": []}


@router.post("/reload")
async def biznav_reload(project_name: str = Query(...)) -> dict:
    """V1.1 stub —— V1.3 hot_reload 实现：监听 .eaide/features/*.yaml 变更。"""
    raise HTTPException(
        status_code=503,
        detail="biznav reload not implemented in V1.1 (planned for V1.3)",
    )


# ---------------------------------------------------------------------------
# internal helpers (test access)
# ---------------------------------------------------------------------------


def _reset_storage_for_tests() -> None:
    """测试用：清空单例。"""
    global _storage
    _storage = None
