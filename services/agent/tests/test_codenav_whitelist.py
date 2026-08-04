"""test_codenav_whitelist.py —— Phase 2F 用户路径白名单校验测试。"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def setup_dirs(tmp_path, monkeypatch):
    """建 4 个测试目录：
      allowed_inside_home : Documents/foo  → 允许
      allowed_in_cwd      : tmp_path/foo   → 允许（cwd）
      outside             : tmp_path/../outside → 不允许
    """
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    monkeypatch.setenv("EAIDE_WORKSPACE_INDEX_DB", str(tmp_path / "idx.db"))
    (tmp_path / "appdata").mkdir()

    # Documents/foo
    home = Path(os.path.expanduser("~"))
    docs = home / "Documents"
    if not docs.exists():
        # CI/Windows Server 不一定有 Documents → 用 tmp_path 模拟
        # 通过环境变量追加
        monkeypatch.setenv("EAIDE_CODENAV_EXTRA_ROOTS", str(tmp_path))
        docs = tmp_path

    inside = docs / "eaide_test_allowed"
    inside.mkdir(exist_ok=True)
    outside = tmp_path / "outside_zone"
    outside.mkdir(exist_ok=True)

    return {"inside": inside, "outside": outside, "tmp": tmp_path}


def test_allowed_roots_includes_home_and_cwd(setup_dirs):
    import importlib
    from agent.codenav import api as codenav_api
    importlib.reload(codenav_api)
    roots = codenav_api._allowed_roots()
    assert len(roots) >= 1
    # 至少包含 tmp_path（通过 EXTRA_ROOTS 或 cwd）
    roots_str = [str(r) for r in roots]
    assert any("eaide_test_allowed" in r or str(setup_dirs["tmp"]) in r for r in roots_str)


def test_is_within_allowed_true(setup_dirs):
    import importlib
    from agent.codenav import api as codenav_api
    importlib.reload(codenav_api)
    assert codenav_api._is_within_allowed(setup_dirs["inside"])


def test_is_within_allowed_always_true_v2(setup_dirs):
    """V2 简化：_is_within_allowed 总返回 True（不做白名单校验）。"""
    import importlib
    from agent.codenav import api as codenav_api
    importlib.reload(codenav_api)
    fake = Path("C:/Windows/System32/drivers/etc/hosts") if os.name == "nt" else Path("/etc/passwd")
    assert codenav_api._is_within_allowed(fake) is True


def test_validate_user_paths_accepts_inside(setup_dirs):
    import importlib
    from agent.codenav import api as codenav_api
    importlib.reload(codenav_api)
    out = codenav_api._validate_user_paths([str(setup_dirs["inside"])])
    assert len(out) == 1
    assert Path(out[0]).exists()


def test_validate_user_paths_v2_accepts_any_existing():
    """V2 简化：_validate_user_paths 接受任意已存在路径（不挡用户）。"""
    import importlib
    from agent.codenav import api as codenav_api
    importlib.reload(codenav_api)
    fake = Path("C:/Windows/System32/drivers/etc/hosts")
    if not fake.exists():
        pytest.skip("Windows-only path")
    out = codenav_api._validate_user_paths([str(fake)])
    assert len(out) == 1


def test_validate_user_paths_rejects_nonexistent():
    import importlib
    from agent.codenav import api as codenav_api
    importlib.reload(codenav_api)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        codenav_api._validate_user_paths(["Z:/totally/nonexistent/path"])
    assert exc.value.status_code == 404


def test_allowed_roots_endpoint(setup_dirs):
    import importlib
    from agent.codenav import api as codenav_api
    importlib.reload(codenav_api)
    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TestClient(app)
    resp = c.get("/codenav/allowed-roots")
    assert resp.status_code == 200
    body = resp.json()
    assert "roots" in body
    assert "extra_env" in body
    assert body["extra_env"] == "EAIDE_CODENAV_EXTRA_ROOTS"


def test_index_with_add_roots(setup_dirs):
    """POST /codenav/index 带 add_roots 应该被接受。"""
    import importlib
    from agent.codenav import api as codenav_api
    importlib.reload(codenav_api)
    # 在 inside 目录写一个 java 文件
    sample = setup_dirs["inside"] / "Demo.java"
    sample.write_text("public class Demo { void m() {} }", encoding="utf-8")

    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TestClient(app)
    resp = c.post("/codenav/index", json={
        "root_paths": None,
        "add_roots": [str(setup_dirs["inside"])],
        "files": None,
    })
    assert resp.status_code == 200
    body = resp.json()
    # 至少索引到了 Demo.java
    assert body["total_files"] >= 1


def test_index_with_files_param(setup_dirs):
    sample = setup_dirs["inside"] / "Foo.java"
    sample.write_text("public class Foo { void bar() {} }", encoding="utf-8")

    import importlib
    from agent.codenav import api as codenav_api
    importlib.reload(codenav_api)
    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TestClient(app)
    resp = c.post("/codenav/index", json={
        "files": [str(sample)],
        "add_roots": None,
        "root_paths": None,
    })
    assert resp.status_code == 200


def test_index_accepts_any_path_v2():
    """V2 简化：POST /codenav/index 接受任意路径（V3 路径护栏在 tool_runner 层）。"""
    import importlib
    from agent.codenav import api as codenav_api
    importlib.reload(codenav_api)
    app = __import__("agent.main", fromlist=["create_app"]).create_app()
    c = TestClient(app)
    fake = "C:/Windows/System32/drivers/etc/hosts"
    if not Path(fake).exists():
        pytest.skip("Windows-only path")
    resp = c.post("/codenav/index", json={
        "files": [fake],
        "add_roots": None,
        "root_paths": None,
    })
    assert resp.status_code == 200
