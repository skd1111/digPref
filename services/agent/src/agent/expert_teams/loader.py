"""专家团 loader —— 启动扫描 + 手动 load_one/remove（仿 skills/loader.py）。

存储目录：<数据根>/expert_teams（BUGFIX #98：生产环境数据根 = 安装目录，
与 config/ 同父级；解析见 agent/paths.py）。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import yaml

from agent.expert_teams.models import ExpertTeam
from agent.expert_teams.schema import validate_expert_team_yaml
from agent.paths import data_root

logger = logging.getLogger(__name__)


def _default_expert_teams_dir() -> Path:
    """数据根下的 expert_teams/（生产=安装目录，BUGFIX #98）。"""
    return data_root() / "expert_teams"


EXPERT_TEAMS_DIR = _default_expert_teams_dir()


class ExpertTeamLoader:
    """启动时一次扫描 + 手动 load_one/remove（无 watchdog，与 Skill 一致）。"""

    def __init__(self, teams_dir: Path = EXPERT_TEAMS_DIR):
        self._dir = teams_dir
        self._teams: dict[str, ExpertTeam] = {}

    def load_all(self) -> list[ExpertTeam]:
        """扫描目录，加载所有 .yaml。坏文件记录日志不抛出。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        for yaml_path in sorted(self._dir.glob("*.yaml")):
            try:
                team = self._load_one_inner(yaml_path)
                if team:
                    self._teams[team.id] = team
            except Exception:
                logger.exception("Failed to load expert team: %s", yaml_path)
        return list(self._teams.values())

    def load_one(self, path: Path) -> ExpertTeam | None:
        """手动加载单个文件（import / save 端点用）。失败返回 None + 记录日志。"""
        try:
            team = self._load_one_inner(path)
            if team:
                self._teams[team.id] = team
            return team
        except Exception:
            logger.exception("Failed to load expert team: %s", path)
            return None

    def remove(self, team_id: str) -> None:
        self._teams.pop(team_id, None)

    def get(self, team_id: str) -> ExpertTeam | None:
        return self._teams.get(team_id)

    def list(self) -> list[ExpertTeam]:
        return list(self._teams.values())

    def _load_one_inner(self, path: Path) -> ExpertTeam | None:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            logger.warning("Expert team YAML must be a dict, got %s: %s", type(data).__name__, path)
            return None
        errors = validate_expert_team_yaml(data)
        if errors:
            logger.warning("Invalid expert team YAML %s: %s", path, errors)
            return None
        team = ExpertTeam.from_dict(data)
        team.source_path = str(path)
        team.loaded_at = int(time.time() * 1000)
        return team
