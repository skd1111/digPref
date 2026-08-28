"""Phase 1B V9.5 · Skill 播种 —— 内置 Office 生成规范种子（2026-08-25）。

把随包分发的种子（skills/seeds/*.yaml，蒸馏自 MiniMax-AI/skills 等
MIT / Apache 2.0 开源 skill 的生产级生成规范）在首次启动时播种到数据根
``<data_root>/skills/``：

    - 目标已存在（用户改过）→ 不覆盖，用户天然可覆盖内置规范
    - manifest（.seeded-manifest.json）记账已播种 id：用户删除后不复活
      （删除即永久移除；想恢复可手动从仓库复制或在设置页重建）
    - 每个种子先过 validate_skill_yaml + validate_no_dsn，坏种子记日志跳过不抛

种子源定位（三级回退，与 config/biz_dict 同策略）：
    1. cwd 相对路径 ``services/agent/src/agent/skills/seeds``
    2. PyInstaller ``_MEIPASS`` 内置副本（spec datas → ``agent/skills/seeds``）
    3. 模块位置推导仓库根（开发态兜底）
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import yaml

from agent.skills.schema import validate_no_dsn, validate_skill_yaml

logger = logging.getLogger(__name__)

# 种子源相对路径（1 = cwd / 仓库根；2 = _MEIPASS 内打包落点）
_SEEDS_REL_REPO = "services/agent/src/agent/skills/seeds"
_SEEDS_REL_MEIPASS = "agent/skills/seeds"

_MANIFEST_NAME = ".seeded-manifest.json"


def resolve_seeds_dir() -> Path | None:
    """三级回退定位种子源目录；全部不存在返 None。"""
    cwd_candidate = Path.cwd() / _SEEDS_REL_REPO
    if cwd_candidate.is_dir():
        return cwd_candidate
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundled = Path(meipass) / _SEEDS_REL_MEIPASS
        if bundled.is_dir():
            return bundled
    try:
        # seed.py → skills → agent → src → agent → services → 仓库根（parents[5]）
        repo_root = Path(__file__).resolve().parents[5]
        candidate = repo_root / _SEEDS_REL_REPO
        if candidate.is_dir():
            return candidate
    except IndexError:
        pass
    return None


def _load_manifest(skills_dir: Path) -> set[str]:
    path = skills_dir / _MANIFEST_NAME
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        ids = data.get("seeded", [])
        return {str(i) for i in ids if isinstance(i, str)}
    except (json.JSONDecodeError, OSError):
        return set()


def _save_manifest(skills_dir: Path, seeded_ids: set[str]) -> None:
    path = skills_dir / _MANIFEST_NAME
    path.write_text(
        json.dumps({"seeded": sorted(seeded_ids)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def seed_builtin_skills(skills_dir: Path) -> list[str]:
    """把内置种子播种到 skills_dir；返回本次新播种的 skill id 列表。

    幂等且保守：已有文件一律不动；已播过的 id 记入 manifest 后不再复活。
    """
    src_dir = resolve_seeds_dir()
    if src_dir is None or not src_dir.is_dir():
        return []
    skills_dir.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(skills_dir)
    seeded: list[str] = []

    for path in sorted(src_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("seed skip (unreadable) %s: %s", path.name, exc)
            continue
        if not isinstance(data, dict):
            logger.warning("seed skip (root not dict) %s", path.name)
            continue
        errors = validate_skill_yaml(data)
        if errors:
            logger.warning("seed skip (schema) %s: %s", path.name, errors)
            continue
        if validate_no_dsn(data):
            logger.warning("seed skip (dsn pattern) %s", path.name)
            continue
        skill_id = data.get("id")
        if not isinstance(skill_id, str) or not skill_id:
            logger.warning("seed skip (no id) %s", path.name)
            continue
        if skill_id in manifest:
            continue  # 已播过：用户删除后也不复活
        manifest.add(skill_id)  # 无论是否落盘都记账（覆盖场景同样不复活）
        target = skills_dir / f"{skill_id}.yaml"
        if target.exists():
            continue  # 用户已有同名文件 → 不覆盖
        target.write_bytes(path.read_bytes())
        seeded.append(skill_id)

    _save_manifest(skills_dir, manifest)
    if seeded:
        logger.info("office seeds planted: %s", seeded)
    return seeded
