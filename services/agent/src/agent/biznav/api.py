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
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from .audit import (
    EVT_FEATURE_DELETE,
    EVT_FEATURE_IMPORT,
    EVT_FEATURE_UPDATE,
)
from .events import EVT_EXTRACTION_DONE, emit_biznav_event
from .import_export import FeatureImportError, FeatureIO
from .models import Feature
from .storage import FeatureStorage, FeatureVersionConflict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/biznav", tags=["biznav"])

# 后台 extract 任务强引用（防 GC 提前回收 fire-and-forget task）
_background_tasks: set[asyncio.Task] = set()


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
    name: str | None = None
    description: str | None = None
    category: str | None = None
    skill_id: str | None = None
    expert_team_ids: list[str] | None = None
    related_files: list[dict[str, str]] | None = None
    related_apis: list[dict[str, str]] | None = None
    related_tables: list[dict[str, str]] | None = None
    business_rules: list[dict[str, Any]] | None = None
    source: str | None = None


class ImportRequest(BaseModel):
    project_name: str
    yaml_text: str | None = None
    json_text: str | None = None
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


def _get_storage(db_path: str | None = None) -> FeatureStorage:
    global _storage
    if db_path:
        return FeatureStorage(db_path)
    if _storage is None:
        _storage = FeatureStorage(os.environ.get("EAIDE_BIZNAV_DB", _default_db_path()))
    return _storage


# ---------------------------------------------------------------------------
# Inject LLM runtime (for extract)
# ---------------------------------------------------------------------------


def _make_llm_client():
    """构造一个 async callable(kind, messages) -> str。

    V1.2 (2026-07-28)：真接 LMRouter.summarise（V1.5 才会有专用 extract_chat 接口）。

    V1.4 (2026-08-04) 逐级降级链（用户要求）：
        1. 本地 Ollama（本地优先红线：本地可用时绝不出外）；
        2. 本地不可用/返回空 → 内网模型；
        3. 内网不可用/未配置 → 云端模型；
    判定"不可用"：抛异常（连接拒绝/超时）或返回空文本，两种都降级到下一级。

    V1.5 (2026-08-05) 修复两个隐性问题：
        - 改走各后端 extract_chat 原始对话（messages 透传）：summarise 会注入
          无关 system prompt 并把输出包成 {"answer": ...}，提取提示词要求的
          JSON 数组永远解析不出来（即使 LLM 可用也是 0 功能点）；
        - 三级全失败时抛 RuntimeError（不再静默返回空串）：extractor 会把
          失败原因写进 job.error_message 并标 failed，前端可见。

    V1.6 (2026-08-05) 内网层级对齐模型管理（用户要求）：
        - 内网和云端同一层级，都从 router.db.llm_backends 取已启用后端；
          不再读 settings.private_llm_*（环境变量/配置）；
        - 没有启用的内网模型（或内网调用失败）才走云端。
    """

    async def _call_extract_chat(backend, messages: list[dict]) -> str:
        """统一调 extract_chat 原始对话接口，只返回文本（失败抛异常由上层捕获）。"""
        return str(await backend.extract_chat(messages) or "")

    async def _client(kind: str, messages: list[dict]) -> str:
        from agent.llm.router import LMRouter

        # 每次 extract 重新构造一个 LMRouter（__init__ 廉价，仅查 router.db）
        router = LMRouter()

        # mock 模式：走内置 mock 后端（不调任何外部服务）
        if router._mock_mode:
            return await _call_extract_chat(router.mock, messages)

        failures: list[str] = []

        # 1/3 本地 Ollama（本地优先：可用时绝不降级）
        try:
            text = await _call_extract_chat(router.ollama, messages)
            if text.strip():
                return text
            failures.append("本地 Ollama 返回空")
        except Exception as e:
            failures.append(f"本地 Ollama 不可用: {e}")
            logger.warning("[biznav] local ollama unavailable (%s); fallback to private", e)

        # 2/3 内网模型（与云端同层：从 router.db 取已启用的 private 后端，
        # 不看 settings/环境变量；没有启用的内网模型才走云端）
        try:
            private = await router._build_private_client()
            if private is not None:
                try:
                    text = await _call_extract_chat(private, messages)
                    if text.strip():
                        return text
                    failures.append("内网模型返回空")
                except Exception as e:
                    failures.append(f"内网模型不可用: {e}")
                    logger.warning("[biznav] private unavailable (%s); fallback to cloud", e)
            else:
                failures.append("内网模型未启用（模型管理中没有启用的 private 后端）")
        except Exception as e:
            failures.append(f"内网模型查询失败: {e}")
            logger.warning("[biznav] private lookup failed (%s); fallback to cloud", e)

        # 3/3 云端模型（模型管理注册表已启用的 cloud 后端）
        try:
            cloud = await router._build_cloud_client()
            if cloud is not None:
                text = await _call_extract_chat(cloud, messages)
                if text.strip():
                    return text
                failures.append("云端模型返回空")
            else:
                failures.append("云端模型未配置")
        except Exception as e:
            failures.append(f"云端模型不可用: {e}")
            logger.warning("[biznav] cloud unavailable (%s); all backends exhausted", e)

        # 全失败：抛错而不是返回空串——extractor 记录到 job.error_message，
        # 前端能把「为什么没功能点」展示给用户（BUGFIX：静默 0 产出）。
        raise RuntimeError(
            "所有 LLM 后端均不可用（" + "；".join(failures) + "）。"
            "请启动本地 Ollama，或在「模型管理」中配置可用的云端/内网模型"
        )

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
            except Exception as e:
                logger.warning("[biznav] emit biznav_extraction_done 失败: %s", e)

        # 项目画像（init 风格，2026-08-05）：独立于功能点提取成败，best-effort。
        # 后续 chat 发送时前端把画像前置注入 prompt，模型不再反问项目/语言。
        try:
            from .project_profile import generate_profile

            profile_text = await generate_profile(
                req.project_name,
                str(root_path.resolve()),
                _make_llm_client(),
            )
            if profile_text:
                storage.upsert_profile(
                    req.project_name,
                    str(root_path.resolve()),
                    profile_text,
                )
                logger.info(
                    "[biznav] project profile saved: %s (%d chars)",
                    req.project_name,
                    len(profile_text),
                )
        except Exception as e:
            logger.warning("[biznav] project profile generation failed: %s", e)

    task = asyncio.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    return ExtractResponse(job_id=job_id, project_name=req.project_name, status="pending")


@router.get("/status")
async def biznav_status(project_name: str = Query(...)) -> dict:
    storage = _get_storage()
    job = storage.latest_job(project_name)
    if not job:
        return {"project_name": project_name, "has_job": False}
    return {"project_name": project_name, "has_job": True, "job": job}


@router.get("/profile")
async def biznav_profile(project_name: str = Query(...)) -> dict:
    """项目画像（init 风格）：导入工程时生成，chat 发送时前置注入提示词。"""
    storage = _get_storage()
    row = storage.get_profile(project_name)
    if not row:
        return {"project_name": project_name, "has_profile": False, "profile": ""}
    return {
        "project_name": project_name,
        "has_profile": True,
        "profile": row["profile_text"],
        "project_root": row.get("project_root") or "",
        "updated_at": row.get("updated_at"),
    }


@router.get("/features")
async def biznav_list_features(
    project_name: str | None = Query(None),
    category: str | None = Query(None),
    include_deleted: bool = Query(False),
) -> dict:
    storage = _get_storage()
    # project_name 可选：不传时跨项目列出全部（前端 open 工程后无法确定提取时的 project_name）
    if project_name:
        features = storage.list_by_project(project_name, include_deleted=include_deleted)
    else:
        features = storage.list_all(include_deleted=include_deleted)
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
    if body.skill_id is not None:
        new.skill_id = body.skill_id
    if body.expert_team_ids is not None:
        new.expert_team_ids = [str(x) for x in body.expert_team_ids]
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

        new.business_rules = [BusinessRule.from_dict(r) for r in body.business_rules]
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
