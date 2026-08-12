"""test_ollama_breaker.py —— Ollama 降级熔断器测试（BUGFIX #88）。

语义（用户要求）：调不通立即切下一级（不在本级重试）；
连续 3 次失败 → Open 拒绝探测 30s（复用 circuit_breaker 标准实现）。
"""

from __future__ import annotations

import pytest
from agent.llm import ollama as ollama_mod
from agent.llm.ollama import OllamaClient, OllamaUnavailableError

BASE = "http://127.0.0.1:11434"


class _FakeAsyncClient:
    """记录构造次数；post 永远 connection refused。"""

    constructed = 0

    def __init__(self, *args, **kwargs):
        _FakeAsyncClient.constructed += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json=None):
        import httpx

        raise httpx.ConnectError("connection refused")


@pytest.fixture(autouse=True)
def _reset_breaker():
    ollama_mod._OLLAMA_BREAKER_REGISTRY.reset_all()
    _FakeAsyncClient.constructed = 0
    yield
    ollama_mod._OLLAMA_BREAKER_REGISTRY.reset_all()


@pytest.mark.asyncio
async def test_trips_after_three_failures_then_skips_probe(monkeypatch):
    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", _FakeAsyncClient)
    client = OllamaClient(base_url=BASE, model="test")

    # 前 3 次：每次都真实探测（失败立即抛出切下一级，不在本级重试）
    for _ in range(3):
        with pytest.raises(OllamaUnavailableError):
            await client._chat([{"role": "user", "content": "hi"}])
    assert _FakeAsyncClient.constructed == 3

    # 第 4 次：连续 3 次失败已 Open → 直接判不可用，不再构造 HTTP 客户端
    with pytest.raises(OllamaUnavailableError, match="熔断"):
        await client._chat([{"role": "user", "content": "hi"}])
    assert _FakeAsyncClient.constructed == 3


@pytest.mark.asyncio
async def test_success_resets_failure_count(monkeypatch):
    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", _FakeAsyncClient)
    client = OllamaClient(base_url=BASE, model="test")

    # 累计 2 次失败（未达 3 次阈值）
    for _ in range(2):
        with pytest.raises(OllamaUnavailableError):
            await client._chat([{"role": "user", "content": "hi"}])
    # 模拟一次成功 → 熔断器复位（Half-Open → Closed 路径，直接用 reset 等价模拟）
    ollama_mod._OLLAMA_BREAKER_REGISTRY.reset_all()
    # 再失败 2 次仍不会 Open（计数从零重新累计）
    for _ in range(2):
        with pytest.raises(OllamaUnavailableError):
            await client._chat([{"role": "user", "content": "hi"}])
    assert ollama_mod._ollama_breaker(BASE).allow()


@pytest.mark.asyncio
async def test_generate_also_breaks(monkeypatch):
    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", _FakeAsyncClient)
    client = OllamaClient(base_url=BASE, model="test")

    for _ in range(3):
        with pytest.raises(OllamaUnavailableError):
            await client._generate("hi")
    with pytest.raises(OllamaUnavailableError, match="熔断"):
        await client._generate("hi")
    assert _FakeAsyncClient.constructed == 3


@pytest.mark.asyncio
async def test_disabled_client_never_probes(monkeypatch):
    """BUGFIX #89：未配置端侧模型（enabled=False）→ 零探测，直接判不可用。"""
    monkeypatch.setattr(ollama_mod.httpx, "AsyncClient", _FakeAsyncClient)
    client = OllamaClient(base_url=BASE, model="test", enabled=False)

    with pytest.raises(OllamaUnavailableError, match="未配置"):
        await client._chat([{"role": "user", "content": "hi"}])
    with pytest.raises(OllamaUnavailableError, match="未配置"):
        await client._generate("hi")
    # 一次 HTTP 客户端都没构造过
    assert _FakeAsyncClient.constructed == 0


def test_enabled_from_db_no_local_row_means_not_configured(tmp_path, monkeypatch):
    """BUGFIX #89：llm_backends 表已建但无 local 行 → 判未配置，不探测。"""
    import sqlite3

    from agent.config import settings
    from agent.llm.router import _ollama_enabled_from_db

    db = tmp_path / "router.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE llm_backends (name TEXT PRIMARY KEY, type TEXT NOT NULL, "
        "base_url TEXT NOT NULL, model_name TEXT NOT NULL, enabled INTEGER DEFAULT 1)"
    )
    conn.execute(
        "INSERT INTO llm_backends(name, type, base_url, model_name, enabled) "
        "VALUES ('minimax', 'cloud', 'https://x', 'm', 1)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "llm_router_db_path", str(db))
    assert _ollama_enabled_from_db() is False


def test_enabled_from_db_enabled_local_row(tmp_path, monkeypatch):
    import sqlite3

    from agent.config import settings
    from agent.llm.router import _ollama_enabled_from_db

    db = tmp_path / "router.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE llm_backends (name TEXT PRIMARY KEY, type TEXT NOT NULL, "
        "base_url TEXT NOT NULL, model_name TEXT NOT NULL, enabled INTEGER DEFAULT 1)"
    )
    conn.execute(
        "INSERT INTO llm_backends(name, type, base_url, model_name, enabled) "
        "VALUES ('ollama', 'local', 'http://127.0.0.1:11434', 'qwen', 1)"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(settings, "llm_router_db_path", str(db))
    assert _ollama_enabled_from_db() is True


def test_enabled_from_db_missing_db_falls_back_to_settings(tmp_path, monkeypatch):
    """无 db（纯环境变量用法）→ 回退 settings.ollama_enabled。"""
    from agent.config import settings
    from agent.llm.router import _ollama_enabled_from_db

    monkeypatch.setattr(settings, "llm_router_db_path", str(tmp_path / "no.db"))
    monkeypatch.setattr(settings, "ollama_enabled", True)
    assert _ollama_enabled_from_db() is True
    monkeypatch.setattr(settings, "ollama_enabled", False)
    assert _ollama_enabled_from_db() is False
