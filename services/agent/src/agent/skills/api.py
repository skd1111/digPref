"""FastAPI /skills/* 路由。

端点：
  GET    /skills/list          — 列出所有 skill
  GET    /skills/{id}          — 获取单个 skill
  PUT    /skills/{id}          — 保存（写入 + 立即 load_one()）
  DELETE /skills/{id}          — 删除
  POST   /skills/import        — 用户从 UI 导入（写文件 + 加载）
  GET    /skills/export/all    — 导出全部（YAML 字典）
  POST   /skills/reload        — 重新扫描整个目录
"""

from __future__ import annotations

import yaml
from fastapi import APIRouter, Body, File, HTTPException, UploadFile
from fastapi.responses import Response

from agent.skills.loader import SKILLS_DIR as _DEFAULT_SKILLS_DIR
from agent.skills.loader import SkillLoader
from agent.skills.schema import validate_no_dsn, validate_skill_yaml
from agent.skills.share import export_zip, import_zip

router = APIRouter(prefix="/skills", tags=["skills"])

# module-level 单例 loader（手动加载模式：save/import 端点更新它）
_loader = SkillLoader()
# SKILLS_DIR 走 module-level 便于测试 monkey-patch
SKILLS_DIR = _DEFAULT_SKILLS_DIR


def get_loader() -> SkillLoader:
    return _loader


def init_loader() -> None:
    """应用启动时调用一次（C5 fix: 不在 router 上挂 startup 事件。

    FastAPI 0.93+ 弃用 @router.on_event("startup")，且 TestClient 不触发
    include_router 的 startup 钩子。V0 显式在 main.py 启动时调 init_loader()，
    测试通过直接替换 api._loader 实现（见 test_skills_api.py）。
    """
    global _loader
    _loader = SkillLoader()
    _loader.load_all()


@router.get("/list")
def list_skills() -> dict:
    return {"skills": [s.to_dict() for s in _loader.list()]}


@router.get("/{skill_id}")
def get_skill(skill_id: str) -> dict:
    s = _loader.get(skill_id)
    if not s:
        raise HTTPException(status_code=404, detail=f"skill {skill_id} not found")
    return s.to_dict()


@router.put("/{skill_id}")
def save_skill(skill_id: str, body: dict = Body(...)) -> dict:
    """保存并立即重载。"""
    errors = validate_skill_yaml(body)
    if errors:
        raise HTTPException(status_code=400, detail={"validation_errors": errors})
    if body.get("id") != skill_id:
        raise HTTPException(status_code=400, detail="body.id must match URL skill_id")
    dsn_errors = validate_no_dsn(body)
    if dsn_errors:
        raise HTTPException(status_code=400, detail={"dsn_errors": dsn_errors})
    # 用 loader._dir 而非 module-level SKILLS_DIR，便于测试隔离
    path = _loader._dir / f"{skill_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(body, f, allow_unicode=True, sort_keys=False)
    skill = _loader.load_one(path)
    if not skill:
        raise HTTPException(status_code=500, detail="load after save failed")
    return {"ok": True, "path": str(path), "skill": skill.to_dict()}


@router.delete("/{skill_id}")
def delete_skill(skill_id: str) -> dict:
    path = _loader._dir / f"{skill_id}.yaml"
    if path.exists():
        path.unlink()
    _loader.remove(skill_id)
    return {"ok": True}


@router.post("/import")
def import_skill(body: dict = Body(...)) -> dict:
    errors = validate_skill_yaml(body)
    if errors:
        raise HTTPException(status_code=400, detail={"validation_errors": errors})
    dsn_errors = validate_no_dsn(body)
    if dsn_errors:
        raise HTTPException(status_code=400, detail={"dsn_errors": dsn_errors})
    skill_id = body["id"]
    path = _loader._dir / f"{skill_id}.yaml"
    if path.exists():
        raise HTTPException(status_code=409, detail=f"skill {skill_id} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(body, f, allow_unicode=True, sort_keys=False)
    skill = _loader.load_one(path)
    if not skill:
        raise HTTPException(status_code=500, detail="import load failed")
    return {"ok": True, "skill_id": skill_id}


@router.get("/export/all")
def export_all() -> dict:
    return {
        "skills": {s.id: yaml.safe_dump(s.to_dict(), allow_unicode=True) for s in _loader.list()}
    }


@router.get("/export/zip")
def export_zip_endpoint() -> Response:
    """V1: 把所有 skill 打成 zip 字节流供前端下载。"""
    data = export_zip(_loader.list())
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": 'attachment; filename="eaide-skills.zip"',
        },
    )


@router.post("/import/zip")
async def import_zip_endpoint(
    file: UploadFile = File(...),
    overwrite: bool = False,
) -> dict:
    """V1: 上传 zip（multipart/form-data）→ 校验 → 写文件 → load_one。

    前端用 `<input type="file">` 选 zip 直接上传。
    overwrite=true 时覆盖同名 skill；false（默认）跳过已存在。
    """
    zip_bytes = await file.read()
    report = import_zip(zip_bytes, _loader, overwrite=overwrite)
    return report.to_dict()


@router.post("/reload")
def reload_all() -> dict:
    _loader.load_all()
    return {"ok": True, "count": len(_loader.list())}
