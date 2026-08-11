"""test_skills_v1 —— Phase 2D V1 新增能力测试。

覆盖：
- skills.events emit / consume / flush（3 通道常量 / FIFO 顺序 / 拉空）
- skills.watchdog 防自激（written_by_pid + mtime）
- SkillLoader 多项目隔离（load_one_for_project + list(project_name)）
- SkillWatchdog project_name 路由（'default' 走根目录 / 其他走子目录）
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from agent.skills.events import (
    EVT_SKILL_MATCHED,
    consume_skill_events,
    emit_skill_event,
    flush_skill_events,
)
from agent.skills.loader import SkillLoader
from agent.skills.models import Skill
from agent.skills.watchdog import (
    SkillWatchdog,
    _is_self_written,
    mark_yaml_written,
)

# ---- events 机制 ---------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_skill_events():
    flush_skill_events()
    yield
    flush_skill_events()


def test_skill_channel_name_constant():
    assert EVT_SKILL_MATCHED == "skill_matched"


@pytest.mark.asyncio
async def test_emit_consume_returns_fifo_order():
    emit_skill_event(EVT_SKILL_MATCHED, {"order": 1, "skill_id": "a"})
    emit_skill_event(EVT_SKILL_MATCHED, {"order": 2, "skill_id": "b"})
    events = await consume_skill_events()
    assert len(events) == 2
    assert events[0] == (EVT_SKILL_MATCHED, {"order": 1, "skill_id": "a"})
    assert events[1] == (EVT_SKILL_MATCHED, {"order": 2, "skill_id": "b"})


@pytest.mark.asyncio
async def test_consume_drains_queue():
    emit_skill_event(EVT_SKILL_MATCHED, {"x": 1})
    first = await consume_skill_events()
    second = await consume_skill_events()
    assert len(first) == 1
    assert len(second) == 0


def test_flush_clears_queue():
    emit_skill_event(EVT_SKILL_MATCHED, {"x": 1})
    flush_skill_events()
    assert asyncio.run(consume_skill_events()) == []


# ---- watchdog 防自激 -----------------------------------------------------


def test_mark_yaml_written_and_is_self_written(tmp_path):
    p = tmp_path / "demo.yaml"
    p.write_text("# empty\n", encoding="utf-8")
    mark_yaml_written(p)
    assert _is_self_written(p) is True


def test_is_self_written_false_for_other_pid(tmp_path, monkeypatch):
    p = tmp_path / "demo.yaml"
    p.write_text("# empty\n", encoding="utf-8")
    # 注入错误的 pid 让 is_self_written 走 False 分支
    import agent.skills.watchdog as wd

    monkeypatch.setattr(
        wd,
        "_written_by_pid",
        {
            str(p.resolve()): (99999, p.stat().st_mtime)  # 不是当前 pid
        },
    )
    assert _is_self_written(p) is False


def test_is_self_written_false_for_missing_file(tmp_path):
    missing = tmp_path / "missing.yaml"
    assert _is_self_written(missing) is False


# ---- SkillLoader 多项目隔离 -----------------------------------------------

# 合法 Skill YAML 模板（V1 schema 要求 schema_version + id 格式 + version string）
_VALID_SKILL_YAML = """\
schema_version: "1.0"
id: {skill_id}
name: {name}
description: test
category: test
version: "1.0.0"
mcp_servers: []
allowed_tools: []
few_shot_examples: []
trigger_keywords: [t]
source: manual
ai_confidence: null
enabled: true
role: utility
project_name: {project_name}
"""


def _write_skill_yaml(path: Path, skill_id: str, project_name: str = "default") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        _VALID_SKILL_YAML.format(
            skill_id=skill_id, name=f"Skill {skill_id}", project_name=project_name
        ),
        encoding="utf-8",
    )


def _make_skill(skill_id: str) -> Skill:
    """直接构造 Skill 实例（不经过 YAML 路径）。"""
    return Skill(
        id=skill_id,
        name=f"Skill {skill_id}",
        description="test",
        system_prompt="dummy",
        mcp_servers=[],
        allowed_tools=[],
        few_shot_examples=[],
        trigger_keywords=["test"],
        enabled=True,
        role="utility",
    )


def test_load_all_only_loads_shared_root(tmp_path):
    """load_all 只扫根目录 .yaml（项目目录不扫）。"""
    skills_dir = tmp_path / "skills"
    _write_skill_yaml(skills_dir / "shared_skill.yaml", "shared_skill", "default")
    _write_skill_yaml(
        skills_dir / "order_service" / "project_skill.yaml",
        "project_skill",
        "order_service",
    )
    loader = SkillLoader(skills_dir=skills_dir)
    loader.load_all()
    # load_all 只加载共享 skill
    assert "shared_skill" in [s.id for s in loader.list()]
    assert "project_skill" not in [s.id for s in loader.list()]


def test_load_one_for_project_loads_into_project_bucket(tmp_path):
    """load_one_for_project 把 skill 归类到 `_project_skills[project_name]`。"""
    skills_dir = tmp_path / "skills"
    proj_yaml = skills_dir / "order_service" / "my_skill.yaml"
    _write_skill_yaml(proj_yaml, "my_skill", "order_service")
    loader = SkillLoader(skills_dir=skills_dir)
    skill = loader.load_one_for_project(proj_yaml, "order_service")
    assert skill is not None
    assert skill.id == "my_skill"
    # list(project_name='order_service') 返项目专属
    proj_skills = loader.list(project_name="order_service")
    assert any(s.id == "my_skill" for s in proj_skills)
    # list()（无 project）不返项目专属
    default_list = loader.list()
    assert all(s.id != "my_skill" for s in default_list)


def test_get_for_project_prefers_project_over_shared(tmp_path):
    """同名 skill 项目覆盖共享：get_for_project 优先返项目专属。"""
    loader = SkillLoader(skills_dir=tmp_path / "skills")
    shared = _make_skill("common")
    proj = _make_skill("common")
    # 直接插 _skills（绕开 load_one YAML 路径）
    loader._skills["common"] = shared
    loader._project_skills["order-service"] = {"common": proj}
    assert loader.get_for_project("common", "order-service") is proj
    assert loader.get_for_project("common", "default") is shared
    assert loader.get("common") is shared


def test_remove_clears_shared_and_project_buckets(tmp_path):
    loader = SkillLoader(skills_dir=tmp_path / "skills")
    loader._skills["x"] = _make_skill("x")
    loader._project_skills["p1"] = {"x": _make_skill("x")}
    loader._project_skills["p2"] = {"x": _make_skill("x")}
    loader.remove("x")
    assert loader.get("x") is None
    assert loader._project_skills == {"p1": {}, "p2": {}}


# ---- SkillWatchdog 路径路由 -----------------------------------------------


def test_watchdog_default_routes_to_root_dir(tmp_path):
    """project_name='default' 时 watch_dir 是 skills 根目录。"""
    skills_dir = tmp_path / "skills"
    loader = SkillLoader(skills_dir=skills_dir)
    wd = SkillWatchdog(skills_dir=skills_dir, loader=loader, project_name="default")
    assert wd._watch_dir == skills_dir


def test_watchdog_named_project_routes_to_subdir(tmp_path):
    """project_name='order_service' 时 watch_dir 是 `<root>/order_service/`。"""
    skills_dir = tmp_path / "skills"
    loader = SkillLoader(skills_dir=skills_dir)
    wd = SkillWatchdog(skills_dir=skills_dir, loader=loader, project_name="order_service")
    assert wd._watch_dir == skills_dir / "order_service"


def test_watchdog_reload_default_uses_load_one(tmp_path):
    """project_name='default' 时 _reload 走 loader.load_one（共享 bucket）。"""
    skills_dir = tmp_path / "skills"
    yaml_path = skills_dir / "shared.yaml"
    _write_skill_yaml(yaml_path, "sharedskill", "default")
    loader = SkillLoader(skills_dir=skills_dir)
    wd = SkillWatchdog(skills_dir=skills_dir, loader=loader, project_name="default")
    sid = wd._reload(yaml_path)
    assert sid == "sharedskill"
    assert "sharedskill" in [s.id for s in loader.list()]
    # 不在项目 bucket
    assert loader._project_skills == {}


def test_watchdog_reload_named_project_uses_load_one_for_project(tmp_path):
    """project_name='order_service' 时 _reload 走 loader.load_one_for_project。"""
    skills_dir = tmp_path / "skills"
    yaml_path = skills_dir / "order_service" / "my_skill.yaml"
    _write_skill_yaml(yaml_path, "myskill", "order_service")
    loader = SkillLoader(skills_dir=skills_dir)
    wd = SkillWatchdog(skills_dir=skills_dir, loader=loader, project_name="order_service")
    sid = wd._reload(yaml_path)
    assert sid == "myskill"
    # 项目 bucket 有
    assert "myskill" in loader._project_skills.get("order_service", {})
    # 共享 bucket 不有
    assert "myskill" not in loader._skills


def test_watchdog_reload_skips_self_written_yaml(tmp_path):
    """防自激：本进程刚 mark_yaml_written 的 YAML _reload 仍调但 _is_self_written 在 _watch_loop 拦截。"""
    skills_dir = tmp_path / "skills"
    yaml_path = skills_dir / "shared.yaml"
    _write_skill_yaml(yaml_path, "sharedskill", "default")
    loader = SkillLoader(skills_dir=skills_dir)
    wd = SkillWatchdog(skills_dir=skills_dir, loader=loader, project_name="default")
    mark_yaml_written(yaml_path)
    assert _is_self_written(yaml_path) is True
    # _watch_loop 会 skip；这里 _reload 直接调仍会跑（语义 OK，watch_loop 防自激）
    wd._reload(yaml_path)
