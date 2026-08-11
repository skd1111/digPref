"""test_codenav_path_guard.py —— Phase 2F V3 路径护栏测试。"""

from __future__ import annotations

import pytest


@pytest.fixture
def setup_projects(tmp_path, monkeypatch):
    """建 2 个项目目录 + 1 个非项目目录。"""
    p1 = tmp_path / "project1"
    p1.mkdir()
    p1_file = p1 / "main.java"
    p1_file.write_text("public class A {}", encoding="utf-8")

    p2 = tmp_path / "project2"
    p2.mkdir()
    p2_subdir = p2 / "src"
    p2_subdir.mkdir()
    p2_inner = p2_subdir / "Foo.java"
    p2_inner.write_text("public class F {}", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    outside_file = outside / "Secret.java"
    outside_file.write_text("public class S {}", encoding="utf-8")

    return {
        "p1": p1,
        "p1_file": p1_file,
        "p2": p2,
        "p2_inner": p2_inner,
        "outside": outside,
        "outside_file": outside_file,
    }


@pytest.fixture
def guard(setup_projects):
    """每个测试前清空 + 重新初始化。"""
    from agent.codenav import path_guard

    path_guard._opened = []
    path_guard.init_opened_projects([str(setup_projects["p1"]), str(setup_projects["p2"])])
    return path_guard


def test_init_loads_projects(guard, setup_projects):
    assert len(guard.get_opened_projects()) == 2


def test_init_from_env(monkeypatch, setup_projects):
    from agent.codenav import path_guard

    monkeypatch.setenv("EAIDE_AGENT_OPENED_PROJECTS", str(setup_projects["p1"]))
    path_guard._opened = []
    guard = path_guard.init_opened_projects()
    assert str(setup_projects["p1"].resolve()) in guard


def test_is_within_opened_file(guard, setup_projects):
    assert guard.is_within_opened(str(setup_projects["p1_file"]))
    assert guard.is_within_opened(str(setup_projects["p2_inner"]))


def test_is_within_opened_outside(guard, setup_projects):
    assert not guard.is_within_opened(str(setup_projects["outside_file"]))
    assert not guard.is_within_opened(str(setup_projects["outside"]))


def test_check_passes_for_within(guard, setup_projects):
    # 不抛
    guard.check(str(setup_projects["p1_file"]), operation="read")


def test_check_raises_for_outside(guard, setup_projects):
    from agent.codenav.path_guard import PathOutsideProjectsError

    with pytest.raises(PathOutsideProjectsError) as exc:
        guard.check(str(setup_projects["outside_file"]), operation="write")
    assert exc.value.path == str(setup_projects["outside_file"])
    assert len(exc.value.opened_projects) == 2


def test_add_opened_project(guard, setup_projects):
    ok = guard.add_opened_project(str(setup_projects["outside"]))
    assert ok is True
    assert guard.is_within_opened(str(setup_projects["outside_file"]))


def test_add_opened_project_duplicate(guard, setup_projects):
    """重复添加返回 False，不重复。"""
    ok1 = guard.add_opened_project(str(setup_projects["outside"]))
    ok2 = guard.add_opened_project(str(setup_projects["outside"]))
    assert ok1 is True
    assert ok2 is False


def test_remove_opened_project(guard, setup_projects):
    ok = guard.remove_opened_project(str(setup_projects["p1"]))
    assert ok is True
    assert not guard.is_within_opened(str(setup_projects["p1_file"]))


def test_remove_opened_project_not_found(guard, setup_projects):
    ok = guard.remove_opened_project(str(setup_projects["outside"]))
    assert ok is False


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_list_opened_projects(guard):
    from fastapi.testclient import TestClient

    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TestClient(app)
    resp = c.get("/codenav/opened-projects")
    assert resp.status_code == 200
    assert len(resp.json()["opened_projects"]) == 2


@pytest.mark.asyncio
async def test_api_sync_opened_projects(setup_projects):
    from fastapi.testclient import TestClient

    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TestClient(app)
    resp = c.post(
        "/codenav/opened-projects/sync",
        json={"folders": [str(setup_projects["p1"])]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["opened_projects"]) == 1


@pytest.mark.asyncio
async def test_api_add_then_remove(setup_projects):
    from fastapi.testclient import TestClient

    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TestClient(app)
    # 同步到只剩 p1
    c.post("/codenav/opened-projects/sync", json={"folders": [str(setup_projects["p1"])]})
    # 添加 p2
    resp = c.post(
        "/codenav/opened-projects/add",
        json={"folder": str(setup_projects["p2"])},
    )
    assert len(resp.json()["opened_projects"]) == 2
    # 移除 p1
    resp2 = c.post(
        "/codenav/opened-projects/remove",
        json={"folder": str(setup_projects["p1"])},
    )
    assert len(resp2.json()["opened_projects"]) == 1
