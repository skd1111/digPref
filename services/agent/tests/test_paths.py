"""BUGFIX #98 · agent/paths.py —— 数据根解析回归。

覆盖：
- EAIDE_DATA_ROOT 注入优先（生产 = 安装目录）
- 未注入时回退 %APPDATA%\\eaide（开发/独立运行）
- skills / expert_teams 默认目录跟随数据根

旧 %APPDATA%\\eaide 数据已由用户手动清理，无迁移逻辑（不留搬运代码）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent.paths import data_root


@pytest.fixture(autouse=True)
def _fake_home(monkeypatch, tmp_path):
    """把 Path.home() 指到临时目录，防止测试误碰真实 ~/.eaide。"""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


def test_data_root_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("EAIDE_DATA_ROOT", str(tmp_path / "install"))
    assert data_root() == tmp_path / "install"


def test_data_root_fallback_appdata(monkeypatch, tmp_path):
    monkeypatch.delenv("EAIDE_DATA_ROOT", raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert data_root() == tmp_path / "eaide"


def test_data_root_fallback_home_dot_eaide(monkeypatch, tmp_path, _fake_home):
    monkeypatch.delenv("EAIDE_DATA_ROOT", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    assert data_root() == _fake_home / ".eaide"


def test_skills_and_teams_dir_follow_data_root(monkeypatch, tmp_path):
    """skills / expert_teams 默认目录跟随数据根（不在 import 时写死）。"""
    monkeypatch.setenv("EAIDE_DATA_ROOT", str(tmp_path / "install"))
    from agent.expert_teams.loader import _default_expert_teams_dir
    from agent.skills.loader import _default_skills_dir

    assert _default_skills_dir() == tmp_path / "install" / "skills"
    assert _default_expert_teams_dir() == tmp_path / "install" / "expert_teams"
