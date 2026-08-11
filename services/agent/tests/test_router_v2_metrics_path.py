"""Phase 2C V2 — metrics.py 路径修复 + router.py 委托 engine。

覆盖：
  - _router_db_path() 走 settings.llm_router_db_path（不再硬编 %APPDATA%）
  - 测试时 chdir tmp_path + 默认 settings → router.db 落 tmp_path
  - 生产绝对路径 → 直接用
"""

from agent.llm.metrics import _router_db_path


def test_default_relative_path_resolves_against_cwd(tmp_path, monkeypatch):
    """默认 settings.llm_router_db_path='router.db'（相对路径）→ tmp_path/router.db。

    模拟 _isolate fixture 的 monkeypatch.chdir(tmp_path)。
    """
    monkeypatch.chdir(tmp_path)
    # 显式重设 settings（pydantic-settings 实例可能缓存）
    monkeypatch.setenv("EAIDE_LLM_ROUTER_DB_PATH", "router.db")
    p = _router_db_path()
    assert p.is_absolute()
    assert p.parent == tmp_path
    assert p.name == "router.db"


def test_absolute_path_used_directly(tmp_path, monkeypatch):
    """绝对路径 → 直接用（生产场景）。"""
    abs_path = str(tmp_path / "custom" / "router.db")
    monkeypatch.setenv("EAIDE_LLM_ROUTER_DB_PATH", abs_path)
    p = _router_db_path()
    assert str(p) == abs_path


def test_parent_dir_created_if_missing(tmp_path, monkeypatch):
    """settings 路径父目录不存在 → 自动 mkdir（防止 sqlite 报错）。"""
    nested = tmp_path / "deep" / "nested" / "router.db"
    monkeypatch.setenv("EAIDE_LLM_ROUTER_DB_PATH", str(nested))
    p = _router_db_path()
    assert p.parent.exists()
    assert p == nested
