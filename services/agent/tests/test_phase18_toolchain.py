"""Phase 18 工具链探测：配置路径 → PATH → 常见目录 → unavailable。"""

from __future__ import annotations

from agent.coding.toolchain import (
    clear_cache,
    load_toolchain_config,
    resolve_toolchain,
    save_toolchain_config,
)


def setup_function():
    clear_cache()


def test_user_configured_path_wins(monkeypatch, tmp_path):
    fake = tmp_path / "python.exe"
    fake.write_text("")
    monkeypatch.setenv("PATH", "")
    got = resolve_toolchain("python", configured={"python": str(fake)})
    assert got.path == str(fake)
    assert got.source == "configured"
    assert got.available is True


def test_user_configured_path_missing_falls_through(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "")
    got = resolve_toolchain("python", configured={"python": str(tmp_path / "nope.exe")})
    assert got.source != "configured"


def test_path_env_resolution(monkeypatch, tmp_path):
    fake = tmp_path / "mytool.exe"
    fake.write_text("")
    monkeypatch.setenv("PATH", str(tmp_path))
    got = resolve_toolchain("mytool", configured={})
    assert got.available is True
    assert got.source == "path"


def test_fallback_not_found(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))  # 空目录
    got = resolve_toolchain("nonexistent_tool_xyz", configured={})
    assert got.available is False
    assert got.path is None
    assert got.source == "unavailable"


def test_probe_common_dirs(monkeypatch, tmp_path):
    """常见目录探测：注入候选列表后能找到文件。"""
    import agent.coding.toolchain as tc

    fake_dir = tmp_path / "fakeprog"
    fake_dir.mkdir()
    fake = fake_dir / "mytool2.exe"
    fake.write_text("")
    monkeypatch.setenv("PATH", str(tmp_path))  # PATH 里没有
    monkeypatch.setitem(tc.COMMON_PROBES, "mytool2", [str(fake_dir / "*.exe")])
    got = resolve_toolchain("mytool2", configured={})
    assert got.available is True
    assert got.source == "probe"


def test_session_cache(monkeypatch, tmp_path):
    fake = tmp_path / "cached_tool.exe"
    fake.write_text("")
    monkeypatch.setenv("PATH", str(tmp_path))
    first = resolve_toolchain("cached_tool", configured={})
    # 删除文件后缓存仍命中（会话级缓存）
    fake.unlink()
    second = resolve_toolchain("cached_tool", configured={})
    assert first.path == second.path


def test_config_roundtrip(tmp_path, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "toolchain_config_path", str(tmp_path / "tc.json"))
    save_toolchain_config({"python": "D:/py/python.exe"})
    assert load_toolchain_config() == {"python": "D:/py/python.exe"}


def test_config_missing_returns_empty(tmp_path, monkeypatch):
    from agent.config import settings

    monkeypatch.setattr(settings, "toolchain_config_path", str(tmp_path / "absent.json"))
    assert load_toolchain_config() == {}
