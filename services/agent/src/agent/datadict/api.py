"""dict.api —— FastAPI 路由（数据字典，Phase 2H）。

端点：
  GET    /dict/items        — 列表（category 过滤）
  POST   /dict/items        — 新建
  PUT    /dict/items/{key}  — 更新（seed 条目显式覆盖）
  DELETE /dict/items/{key}  — 删除
  GET    /dict/search       — 模糊搜索
  GET    /dict/categories   — 分类列表
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from agent.paths import data_root

from .models import DictItem
from .storage import DictStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dict", tags=["dict"])


def _default_db_path() -> str:
    # BUGFIX #98：统一落数据根（生产=安装目录）
    return str(data_root() / "dict.db")


_storage: DictStorage | None = None


def _get_storage(db_path: str | None = None) -> DictStorage:
    global _storage
    if db_path:
        return DictStorage(db_path)
    if _storage is None:
        _storage = DictStorage(os.environ.get("EAIDE_DICT_DB", _default_db_path()))
    return _storage


def _reset_storage_for_tests() -> None:
    global _storage
    _storage = None


class DictItemRequest(BaseModel):
    key: str
    category: str = "通用"
    label: str = ""
    value: str = ""
    description: str = ""
    updated_by: str = ""


class DictItemUpdateRequest(BaseModel):
    category: str | None = None
    label: str | None = None
    value: str | None = None
    description: str | None = None
    updated_by: str = ""


async def _audit(event: str, payload: dict) -> None:
    try:
        from agent.audit.store import audit

        await audit(event, payload)
    except Exception as e:
        logger.warning("[dict] audit emit failed: %s", e)


@router.get("/items")
async def list_items(category: str | None = Query(None)) -> dict:
    storage = _get_storage()
    items = storage.list(category=category)
    return {"items": [i.to_dict() for i in items], "total": len(items)}


@router.get("/search")
async def search_items(
    q: str = Query(..., min_length=1),
    limit: int = Query(50),
) -> dict:
    storage = _get_storage()
    items = storage.search(q, limit=limit)
    return {"query": q, "items": [i.to_dict() for i in items], "total": len(items)}


@router.get("/categories")
async def list_categories() -> dict:
    storage = _get_storage()
    return {"categories": storage.categories()}


@router.post("/items")
async def create_item(req: DictItemRequest) -> dict:
    storage = _get_storage()
    if not req.key.strip():
        raise HTTPException(400, "key required")
    if storage.get(req.key) is not None:
        raise HTTPException(409, f"dictionary key {req.key} already exists")
    item = DictItem(
        key=req.key.strip(),
        category=req.category or "通用",
        label=req.label,
        value=req.value,
        description=req.description,
        source="manual",
        updated_by=req.updated_by,
    )
    saved = storage.upsert(item, replace_seed=True)
    await _audit("dict_item_create", {"key": saved.key})
    return saved.to_dict()


@router.put("/items/{key}")
async def update_item(key: str, body: DictItemUpdateRequest) -> dict:
    storage = _get_storage()
    existing = storage.get(key)
    if existing is None:
        raise HTTPException(404, f"dictionary key {key} not found")
    patch = body.model_dump(exclude_none=True, exclude={"updated_by"})
    updated = DictItem(
        key=existing.key,
        category=str(patch.get("category", existing.category)),
        label=str(patch.get("label", existing.label)),
        value=str(patch.get("value", existing.value)),
        description=str(patch.get("description", existing.description)),
        source=existing.source,
        updated_by=body.updated_by or existing.updated_by,
        created_at=existing.created_at,
        updated_at=existing.updated_at,
    )
    # seed 条目显式编辑后转 manual（replace_seed=True 允许覆盖）
    saved = storage.upsert(updated, replace_seed=True)
    await _audit("dict_item_update", {"key": key})
    return saved.to_dict()


@router.delete("/items/{key}")
async def delete_item(key: str) -> dict:
    storage = _get_storage()
    if storage.get(key) is None:
        raise HTTPException(404, f"dictionary key {key} not found")
    storage.delete(key)
    await _audit("dict_item_delete", {"key": key})
    return {"ok": True, "key": key}
