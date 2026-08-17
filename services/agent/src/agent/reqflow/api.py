"""reqflow.api —— FastAPI 路由（需求改造工作流 V1，11 端点）。

端点：
  POST   /reqflow/batches                      — 创建批次
  GET    /reqflow/batches                      — 批次列表 + 每批统计
  POST   /reqflow/cards/generate               — AI 生成卡片草稿（三级降级链）
  GET    /reqflow/cards                        — 卡片列表（batch/status/feature 过滤）
  POST   /reqflow/cards                        — 保存卡片（自动编号）
  PUT    /reqflow/cards/{id}                   — 改字段 / 切状态（流转校验 + 自动记版本）
  DELETE /reqflow/cards/{id}                   — 删除（仅 draft）
  GET    /reqflow/cards/{id}/versions          — 历史版本列表（倒序）
  GET    /reqflow/cards/{id}/versions/{ver}    — 指定版本快照（只读）
  GET    /reqflow/export                       — 批次导出 md/docx

设计对齐 biznav/api.py：sync storage 单例 + audit try/except 不阻塞。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from agent.paths import data_root

from . import generator
from .exporter import export_docx, export_markdown
from .models import ReqCard
from .storage import ReqCardStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/reqflow", tags=["reqflow"])

_DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


# ---------------------------------------------------------------------------
# Pydantic 请求模型
# ---------------------------------------------------------------------------


class CreateBatchRequest(BaseModel):
    project_name: str
    name: str = ""
    created_by: str = ""


class CreateCardRequest(BaseModel):
    batch_id: str
    project_name: str
    system_name: str
    title: str
    feature_ids: list[str] = Field(default_factory=list)
    business_value: str = ""
    change_points: str = ""
    feasibility: str = ""
    feasibility_notes: str = ""
    impact: str = ""
    external_systems: list[str] = Field(default_factory=list)
    priority: str = "P2"
    conversation_summary: str = ""
    session_id: str = ""
    created_by: str = ""


class UpdateCardRequest(BaseModel):
    title: str | None = None
    system_name: str | None = None
    business_value: str | None = None
    change_points: str | None = None
    feasibility: str | None = None
    feasibility_notes: str | None = None
    impact: str | None = None
    external_systems: list[str] | None = None
    priority: str | None = None
    status: str | None = None
    changed_by: str = ""


class GenerateCardRequest(BaseModel):
    feature_ids: list[str] = Field(default_factory=list)
    project_name: str = ""
    system_name: str = ""
    conversation_summary: str = ""
    session_id: str = ""


# ---------------------------------------------------------------------------
# Storage 单例
# ---------------------------------------------------------------------------


def _default_db_path() -> str:
    # BUGFIX #98：统一落数据根（生产=安装目录）
    return str(data_root() / "reqcards.db")


_storage: ReqCardStorage | None = None


def _get_storage(db_path: str | None = None) -> ReqCardStorage:
    global _storage
    if db_path:
        return ReqCardStorage(db_path)
    if _storage is None:
        _storage = ReqCardStorage(os.environ.get("EAIDE_REQCARDS_DB", _default_db_path()))
    return _storage


def _reset_storage_for_tests() -> None:
    global _storage
    _storage = None


# ---------------------------------------------------------------------------
# LLM 降级链（与 biznav 提取同一形态：本地 Ollama → DB 内网 → DB 云端）
# ---------------------------------------------------------------------------


def _make_cardify_llm():
    """构造 async (messages) -> str；三级全失败抛 RuntimeError。"""

    async def _call_extract_chat(backend, messages: list[dict]) -> str:
        return str(await backend.extract_chat(messages) or "")

    async def _client(messages: list[dict]) -> str:
        from agent.llm.router import LMRouter

        router = LMRouter()

        if router._mock_mode:
            return await _call_extract_chat(router.mock, messages)

        failures: list[str] = []

        # 1/3 本地 Ollama
        try:
            text = await _call_extract_chat(router.ollama, messages)
            if text.strip():
                return text
            failures.append("本地 Ollama 返回空")
        except Exception as e:
            failures.append(f"本地 Ollama 不可用: {e}")

        # 2/3 内网（router.db 启用的 private 后端）
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
            else:
                failures.append("内网模型未启用")
        except Exception as e:
            failures.append(f"内网模型查询失败: {e}")

        # 3/3 云端（router.db 启用的 cloud 后端）
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

        raise RuntimeError(
            "所有 LLM 后端均不可用（" + "；".join(failures) + "）。"
            "请启动本地 Ollama，或在「模型管理」中配置可用的云端/内网模型"
        )

    return _client


# ---------------------------------------------------------------------------
# biznav 功能点上下文（生成卡片 / 导出时的名称映射）
# ---------------------------------------------------------------------------


def _load_features(feature_ids: list[str], project_name: str) -> list[dict[str, Any]]:
    """按 id 从 biznav 读功能点上下文；读不到的跳过（卡片引用不阻塞）。"""
    if not feature_ids:
        return []
    try:
        from agent.biznav.api import _get_storage as _biznav_storage

        biz_storage = _biznav_storage()
    except Exception as e:
        logger.warning("[reqflow] biznav storage unavailable: %s", e)
        return []
    out: list[dict[str, Any]] = []
    for fid in feature_ids:
        try:
            f = biz_storage.get(fid, project_name)
        except Exception:
            f = None
        if f is None:
            continue
        out.append(
            {
                "id": f.id,
                "name": f.name,
                "description": f.description,
                "related_apis": [{"method": a.method, "path": a.path} for a in f.related_apis],
                "related_tables": [{"name": t.name} for t in f.related_tables],
                "business_rules": [{"text": r.text} for r in f.business_rules],
            }
        )
    return out


def _feature_names_map(cards: list[ReqCard], project_name: str) -> dict[str, str]:
    ids = sorted({fid for c in cards for fid in c.feature_ids})
    return {f["id"]: f["name"] for f in _load_features(ids, project_name)}


async def _audit(event: str, payload: dict[str, Any]) -> None:
    try:
        from agent.audit.store import audit

        await audit(event, payload)
    except Exception as e:
        logger.warning("[reqflow] audit emit failed: %s", e)


def _format_done_requirements(project_name: str, limit: int = 20) -> str:
    """拉取本工程已完成（done）的需求卡片，渲染成提示词参照文本。

    新需求生成/对齐时必须考虑已完成的改造，避免重复/冲突，
    影响分析才能准确。
    """
    if not project_name:
        return ""
    try:
        storage = _get_storage()
        cards = storage.list_cards(project_name=project_name, status="done")
    except Exception as e:
        logger.warning("[reqflow] load done cards failed: %s", e)
        return ""
    if not cards:
        return ""
    lines: list[str] = []
    for c in cards[-limit:]:
        parts = [f"- {c.id} · {c.title}"]
        if c.change_points:
            parts.append(f"  改造点：{c.change_points[:200]}")
        if c.impact and c.impact != "无":
            parts.append(f"  影响：{c.impact[:200]}")
        if c.external_systems:
            parts.append(f"  外部系统：{'、'.join(c.external_systems[:5])}")
        lines.append("\n".join(parts))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Routes —— 批次
# ---------------------------------------------------------------------------


@router.post("/batches")
async def create_batch(req: CreateBatchRequest) -> dict:
    if not req.project_name:
        raise HTTPException(400, "project_name required")
    storage = _get_storage()
    batch = storage.create_batch(
        project_name=req.project_name, name=req.name, created_by=req.created_by
    )
    return batch.to_dict()


@router.get("/batches")
async def list_batches(project_name: str | None = Query(None)) -> dict:
    storage = _get_storage()
    batches = storage.list_batches(project_name=project_name)
    return {
        "batches": [b.to_dict() for b in batches],
        "stats": {b.id: storage.batch_stats(b.id) for b in batches},
    }


# ---------------------------------------------------------------------------
# Routes —— AI 生成
# ---------------------------------------------------------------------------


@router.post("/cards/generate")
async def generate_card(req: GenerateCardRequest) -> dict:
    features = _load_features(req.feature_ids, req.project_name)
    # 本工程已完成的需求卡片注入提示词：新需求必须对照已完成改造，
    # 避免重复/冲突，影响分析才准确
    done_requirements = _format_done_requirements(req.project_name)
    llm_call = _make_cardify_llm()
    try:
        draft = await generator.generate_card_draft(
            llm_call=llm_call,
            features=features,
            conversation_summary=req.conversation_summary,
            system_name=req.system_name,
            done_requirements=done_requirements,
        )
    except RuntimeError as e:
        raise HTTPException(502, str(e)) from e
    draft["feature_ids"] = req.feature_ids
    draft["session_id"] = req.session_id
    return {"draft": draft}


# ---------------------------------------------------------------------------
# Routes —— 卡片 CRUD + 版本
# ---------------------------------------------------------------------------


@router.get("/cards")
async def list_cards(
    batch_id: str | None = Query(None),
    status: str | None = Query(None),
    feature_id: str | None = Query(None),
    project_name: str | None = Query(None),
) -> dict:
    storage = _get_storage()
    cards = storage.list_cards(
        batch_id=batch_id,
        status=status,
        feature_id=feature_id,
        project_name=project_name,
    )
    return {"cards": [c.to_dict() for c in cards], "total": len(cards)}


@router.post("/cards")
async def create_card(req: CreateCardRequest) -> dict:
    storage = _get_storage()
    if storage.get_batch(req.batch_id) is None:
        raise HTTPException(404, f"batch {req.batch_id} not found")
    card = storage.create_card(
        batch_id=req.batch_id,
        project_name=req.project_name,
        system_name=req.system_name,
        title=req.title,
        feature_ids=req.feature_ids,
        business_value=req.business_value,
        change_points=req.change_points,
        feasibility=req.feasibility,
        feasibility_notes=req.feasibility_notes,
        impact=req.impact,
        external_systems=req.external_systems,
        priority=req.priority,
        conversation_summary=req.conversation_summary,
        session_id=req.session_id,
        created_by=req.created_by,
    )
    await _audit(
        "req_card_create",
        {"card_id": card.id, "batch_id": card.batch_id, "title": card.title},
    )
    return card.to_dict()


@router.put("/cards/{card_id}")
async def update_card(card_id: str, body: UpdateCardRequest) -> dict:
    storage = _get_storage()
    fields = body.model_dump(exclude_none=True, exclude={"changed_by"})
    try:
        card = storage.update_card(card_id, changed_by=body.changed_by, **fields)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    if body.status is not None:
        await _audit(
            "req_card_status",
            {
                "card_id": card_id,
                "status": body.status,
                "changed_by": body.changed_by,
                "version": card.version,
            },
        )
    return card.to_dict()


@router.delete("/cards/{card_id}")
async def delete_card(card_id: str) -> dict:
    storage = _get_storage()
    try:
        storage.delete_card(card_id)
    except ValueError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "card_id": card_id}


@router.get("/cards/{card_id}/versions")
async def list_card_versions(card_id: str) -> dict:
    storage = _get_storage()
    card = storage.get_card(card_id)
    if card is None:
        raise HTTPException(404, f"card {card_id} not found")
    return {
        "card_id": card_id,
        "current_version": card.version,
        "versions": storage.list_versions(card_id),
    }


@router.get("/cards/{card_id}/versions/{version}")
async def get_card_version(card_id: str, version: int) -> dict:
    storage = _get_storage()
    try:
        snapshot = storage.get_version(card_id, version)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    return {"card_id": card_id, "version": version, "snapshot": snapshot}


# ---------------------------------------------------------------------------
# Routes —— 导出
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_batch(
    batch_id: str = Query(...),
    format: str = Query("md"),
) -> Any:
    storage = _get_storage()
    batch = storage.get_batch(batch_id)
    if batch is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    cards = storage.list_cards(batch_id=batch_id)
    feature_names = _feature_names_map(cards, batch.project_name)
    if format == "md":
        return {"markdown": export_markdown(batch, cards, feature_names)}
    if format == "docx":
        data = export_docx(batch, cards, feature_names)
        return Response(
            content=data,
            media_type=_DOCX_MEDIA,
            headers={"Content-Disposition": f'attachment; filename="{batch_id}.docx"'},
        )
    raise HTTPException(400, f"unsupported format: {format} (md|docx)")
