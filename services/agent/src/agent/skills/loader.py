"""Skill loader —— 启动扫描 + 手动 load_one/remove。

V0 实现选择：用 %APPDATA%\\eaide\\skills 目录（与 envconfig/config/ 同父级）。
V1 计划：迁移到 <EAIDE_INSTALL_DIR>/skills（spec §0.5 决策）。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import yaml

from agent.skills.models import Skill
from agent.skills.schema import validate_skill_yaml

logger = logging.getLogger(__name__)


def _default_skills_dir() -> Path:
    """V0: 跟随 envconfig 约定（%APPDATA%\\eaide\\skills）。
    V1: 切到 <EAIDE_INSTALL_DIR>/skills。"""
    appdata = os.environ.get("APPDATA", str(Path.home()))
    return Path(appdata) / "eaide" / "skills"


SKILLS_DIR = _default_skills_dir()


class SkillLoader:
    """启动时一次扫描 + 手动 load_one/remove。

    V0 模式（无 watchdog）：
      - 启动时 load_all() 扫描整个目录
      - 写文件后由 API 端点主动调 load_one() 立即生效
      - 外部编辑器改动需重启 Agent

    V1（Phase 2D V1 收尾）：**多项目隔离**。
      - 启动加载只扫 `_dir` 根目录（共享 skill）
      - 项目专属 skill 存 `<_dir>/<project_name>/*.yaml`，由 SkillWatchdog
        单独加载并归类到 `_project_skills[project_name][skill_id]`
      - `list(project_name=None)` 返全部；`list(project_name='xxx')` 只返该项目
      - **不强制**项目目录存在 —— 没项目目录时只走共享 skill（V0 行为兼容）
    """

    def __init__(self, skills_dir: Path = SKILLS_DIR):
        self._dir = skills_dir
        # V0 共享 skill：root 目录加载
        self._skills: dict[str, Skill] = {}
        # V1 项目专属 skill：project_name → skill_id → Skill
        self._project_skills: dict[str, dict[str, Skill]] = {}

    def load_all(self) -> list[Skill]:
        """扫描根目录，加载所有共享 .yaml（V0 行为）。坏文件记录日志不抛出。

        V1 项目目录不扫（由 SkillWatchdog 按需加载并走 load_one_for_project）。
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        for yaml_path in sorted(self._dir.glob("*.yaml")):
            try:
                skill = self._load_one_inner(yaml_path)
                if skill:
                    self._skills[skill.id] = skill
            except Exception:
                logger.exception("Failed to load skill: %s", yaml_path)
        return list(self._skills.values())

    def load_one(self, path: Path) -> Skill | None:
        """手动加载单个文件（import / save 端点用）。

        解析成功 → 更新内存索引；失败 → 返回 None + 记录日志。
        文件名格式 `<skill_id>.yaml`；V1 调用方根据是否在子目录自动判断
        加载到 `_skills` 还是 `_project_skills[project_name]`。
        """
        try:
            skill = self._load_one_inner(path)
            if skill:
                self._skills[skill.id] = skill
            return skill
        except Exception:
            logger.exception("Failed to load skill: %s", path)
            return None

    def load_one_for_project(self, path: Path, project_name: str) -> Skill | None:
        """V1 增量：加载项目专属 skill 到 `_project_skills[project_name]`。

        路径约定：`<skills_dir>/<project_name>/*.yaml`
        """
        try:
            skill = self._load_one_inner(path)
            if skill:
                self._project_skills.setdefault(project_name, {})[skill.id] = skill
            return skill
        except Exception:
            logger.exception("Failed to load skill: %s", path)
            return None

    def remove(self, skill_id: str) -> None:
        """从内存索引中移除 skill（共享 + 所有项目）。"""
        self._skills.pop(skill_id, None)
        for proj_skills in self._project_skills.values():
            proj_skills.pop(skill_id, None)

    def get(self, skill_id: str) -> Skill | None:
        """查共享 skill（项目专属 skill 暂不暴露全局 get，避免误用）。"""
        return self._skills.get(skill_id)

    def get_for_project(self, skill_id: str, project_name: str) -> Skill | None:
        """V1 增量：先查项目专属，再查共享（项目覆盖共享）。"""
        proj = self._project_skills.get(project_name, {})
        if skill_id in proj:
            return proj[skill_id]
        return self._skills.get(skill_id)

    def list(self, project_name: str | None = None) -> list[Skill]:
        """V0：返全部共享。V1：若指定 project_name，返该项目专属 + 共享（项目覆盖）。"""
        if project_name is None:
            return list(self._skills.values())
        proj = self._project_skills.get(project_name, {})
        # 项目覆盖共享：项目专属 skill 优先
        out: dict[str, Skill] = dict(self._skills)
        out.update(proj)
        return list(out.values())

    def _load_one_inner(self, path: Path) -> Skill | None:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            logger.warning("Skill YAML must be a dict, got %s: %s", type(data).__name__, path)
            return None
        errors = validate_skill_yaml(data)
        if errors:
            logger.warning("Invalid skill YAML %s: %s", path, errors)
            return None
        skill = Skill.from_dict(data)
        skill.source_path = str(path)
        skill.loaded_at = int(time.time() * 1000)
        return skill
