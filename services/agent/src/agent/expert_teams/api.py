"""FastAPI /expert-teams/* 路由（仿 skills/api.py）。

端点：
  GET    /expert-teams/list        — 列出所有专家团
  GET    /expert-teams/{id}        — 获取单个专家团
  PUT    /expert-teams/{id}        — 保存（写 YAML + 立即 load_one()）
  DELETE /expert-teams/{id}        — 删除（含其报告模板）
  POST   /expert-teams/import      — 导入（dict 或 {content: "yaml 文本"}，兼容旧格式）
  POST   /expert-teams/import-package — 导入专家团资产包 zip（team.yaml 提示词 + templates/ 模板）
  GET    /expert-teams/{id}/package   — 导出专家团资产包 zip（base64）
  GET    /expert-teams/export/all  — 导出全部（YAML 字典）
  POST   /expert-teams/recommend   — 业务 → 专家团推荐（预设→LLM→关键词）
"""

from __future__ import annotations

import base64
import io
import zipfile

import yaml
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from agent.expert_teams.loader import EXPERT_TEAMS_DIR as _DEFAULT_DIR
from agent.expert_teams.loader import ExpertTeamLoader
from agent.expert_teams.recommender import recommend_team
from agent.expert_teams.schema import validate_expert_team_yaml, validate_no_dsn
from agent.expert_teams.templates import (
    delete_template as _delete_template_file,
)
from agent.expert_teams.templates import (
    resolve_template_path,
)
from agent.expert_teams.templates import (
    save_template as _save_template_file,
)

router = APIRouter(prefix="/expert-teams", tags=["expert-teams"])

# module-level 单例 loader（手动加载模式：save/import 端点更新它）
_loader = ExpertTeamLoader()
EXPERT_TEAMS_DIR = _DEFAULT_DIR


def get_loader() -> ExpertTeamLoader:
    return _loader


def init_loader() -> None:
    """应用启动时调用一次（同 skills api 的 C5 fix：main.py 显式调用）。"""
    global _loader
    _loader = ExpertTeamLoader()
    _loader.load_all()


def _validate(body: dict) -> None:
    errors = validate_expert_team_yaml(body)
    if errors:
        raise HTTPException(status_code=400, detail={"validation_errors": errors})
    dsn_errors = validate_no_dsn(body)
    if dsn_errors:
        raise HTTPException(status_code=400, detail={"dsn_errors": dsn_errors})


@router.get("/list")
def list_teams() -> dict:
    return {"teams": [t.to_dict() for t in _loader.list()]}


@router.get("/{team_id}")
def get_team(team_id: str) -> dict:
    t = _loader.get(team_id)
    if not t:
        raise HTTPException(status_code=404, detail=f"expert team {team_id} not found")
    return t.to_dict()


@router.put("/{team_id}")
def save_team(team_id: str, body: dict = Body(...)) -> dict:
    """保存并立即重载（upsert：新团也走这里写盘）。"""
    _validate(body)
    if body.get("id") != team_id:
        raise HTTPException(status_code=400, detail="body.id must match URL team_id")
    path = _loader._dir / f"{team_id}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(body, f, allow_unicode=True, sort_keys=False)
    team = _loader.load_one(path)
    if not team:
        raise HTTPException(status_code=500, detail="load after save failed")
    return {"ok": True, "path": str(path), "team": team.to_dict()}


@router.delete("/{team_id}")
def delete_team(team_id: str) -> dict:
    team = _loader.get(team_id)
    if team is not None:
        # 专家团是系统重要资产：删团时同步清理其交付物报告模板
        _delete_template_file(team_id, team.report_template)
    path = _loader._dir / f"{team_id}.yaml"
    if path.exists():
        path.unlink()
    _loader.remove(team_id)
    return {"ok": True}


@router.post("/import")
def import_team(body: dict = Body(...)) -> dict:
    """导入：body 为完整对象，或 {content: "...YAML/JSON 文本..."}（后端解析）。"""
    if isinstance(body.get("content"), str):
        try:
            body = yaml.safe_load(body["content"])
        except yaml.YAMLError as e:
            raise HTTPException(status_code=400, detail=f"YAML parse failed: {e}") from e
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="import body must be a mapping")
    _validate(body)
    team_id = body["id"]
    path = _loader._dir / f"{team_id}.yaml"
    if path.exists():
        raise HTTPException(status_code=409, detail=f"expert team {team_id} already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(body, f, allow_unicode=True, sort_keys=False)
    team = _loader.load_one(path)
    if not team:
        raise HTTPException(status_code=500, detail="import load failed")
    return {"ok": True, "team_id": team_id}


# ---------------------------------------------------------------------------
# 专家团资产包（2026-08-10）：压缩文件 = team.yaml 提示词 + templates/ 交付物模板
# ---------------------------------------------------------------------------


class PackageImportRequest(BaseModel):
    file_name: str = ""
    content_base64: str = Field(min_length=1)


_PACKAGE_TEAM_YAML_NAMES = frozenset({"team.yaml", "team.yml"})


def _extract_package_templates(zf: zipfile.ZipFile, team_id: str) -> list[str]:
    """从包内提取 templates/ 下的模板（白名单后缀 + basename 防 zip-slip）。

    返回已落盘的模板文件名列表；非法条目直接拒绝（资产包必须干净）。
    """
    saved: list[str] = []
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        if not name.startswith("templates/"):
            continue  # team.yaml 单独处理；其余根目录文件忽略
        raw = zf.read(info)
        path = _save_template_file(team_id, name.split("/")[-1], base64.b64encode(raw).decode())
        saved.append(path.name)
    return saved


@router.post("/import-package")
def import_package(req: PackageImportRequest) -> dict:
    """导入专家团资产包 zip：根目录 team.yaml（提示词）+ templates/（交付物模板）。

    同名专家团已存在返 409；包内无 team.yaml / 校验不过返 400。
    """
    try:
        raw = base64.b64decode(req.content_base64)
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"压缩包解析失败: {e}") from e

    # 1) 定位 team.yaml（兼容根目录单 yaml 的包）
    team_yaml_name = ""
    root_yamls = [
        n.filename
        for n in zf.infolist()
        if not n.is_dir() and "/" not in n.filename and n.filename.endswith((".yaml", ".yml"))
    ]
    for cand in root_yamls:
        if cand in _PACKAGE_TEAM_YAML_NAMES:
            team_yaml_name = cand
            break
    if not team_yaml_name and len(root_yamls) == 1:
        team_yaml_name = root_yamls[0]
    if not team_yaml_name:
        raise HTTPException(status_code=400, detail="资产包根目录缺少 team.yaml（专家团定义）")

    try:
        body = yaml.safe_load(zf.read(team_yaml_name).decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise HTTPException(status_code=400, detail=f"team.yaml 解析失败: {e}") from e
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="team.yaml 必须是映射结构")
    _validate(body)
    team_id = body["id"]

    path = _loader._dir / f"{team_id}.yaml"
    if path.exists():
        raise HTTPException(status_code=409, detail=f"expert team {team_id} already exists")

    # 2) 模板落盘（白名单校验在 save_template 内）
    try:
        saved = _extract_package_templates(zf, team_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"模板不合法: {e}") from e

    # 3) 未显式指定 report_template 且包内恰好一个模板 → 自动挂上
    if not str(body.get("report_template", "") or "") and len(saved) == 1:
        body["report_template"] = saved[0]

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(body, f, allow_unicode=True, sort_keys=False)
    team = _loader.load_one(path)
    if not team:
        raise HTTPException(status_code=500, detail="import load failed")
    return {"ok": True, "team_id": team_id, "templates": saved}


@router.get("/{team_id}/package")
def export_package(team_id: str) -> dict:
    """导出专家团资产包 zip（base64）：team.yaml + templates/ 下当前生效模板。"""
    team = _loader.get(team_id)
    if team is None:
        raise HTTPException(status_code=404, detail=f"expert team {team_id} not found")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "team.yaml",
            yaml.safe_dump(team.to_dict(), allow_unicode=True, sort_keys=False),
        )
        tpl = resolve_template_path(team_id, team.report_template)
        if tpl is not None:
            zf.write(tpl, f"templates/{tpl.name}")
    return {
        "file_name": f"{team_id}.zip",
        "content_base64": base64.b64encode(buf.getvalue()).decode(),
    }


@router.get("/export/all")
def export_all() -> dict:
    return {
        "teams": {t.id: yaml.safe_dump(t.to_dict(), allow_unicode=True) for t in _loader.list()}
    }


@router.post("/recommend")
async def recommend(body: dict = Body(...)) -> dict:
    """业务 → 专家团推荐。入参：feature_name/feature_description/materials/
    deliverables/preset_team_ids。永不抛错（失败返回 source='none'）。"""
    return await recommend_team(
        _loader.list(),
        preset_ids=list(body.get("preset_team_ids", [])),
        feature_name=str(body.get("feature_name", "")),
        feature_description=str(body.get("feature_description", "")),
        materials=list(body.get("materials", [])),
        deliverables=list(body.get("deliverables", [])),
    )
