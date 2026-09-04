"""resolve_native_backend 无后端快路径（2026-09-01 首响性能修复）回归。

背景：每个 run 首节点做原生工具调用探测，探测前会构建内网/云端客户端并真实
发一次带 tools 的请求（外层超时兜底）。未配置任何已启用内网/云端后端时此前仍
逐个走构建链路；修复后先查注册表，无可用后端直接判无 —— 不构造客户端、不发
任何探测请求，毫秒级返回并缓存。
"""

from __future__ import annotations

from types import SimpleNamespace

from agent.llm import storage
from agent.llm.router import LMRouter


def _router() -> LMRouter:
    r = LMRouter()
    r._mock_mode = False
    return r


async def test_no_enabled_backend_skips_probe(monkeypatch):
    """空注册表 → 直接返回 None，不构建客户端、不发探测请求。"""
    r = _router()

    async def _empty(*a, **k):
        return []

    monkeypatch.setattr(storage, "list_backends", _empty)

    async def _boom_build(self):
        raise AssertionError("无后端快路径不得构建客户端")

    monkeypatch.setattr(LMRouter, "_build_private_client", _boom_build)
    monkeypatch.setattr(LMRouter, "_build_cloud_client", _boom_build)

    assert await r.resolve_native_backend() is None
    assert r._native_probe_cache is None  # 结果已缓存，后续轮次零开销


async def test_only_local_backend_skips_probe(monkeypatch):
    """只有本地（Ollama）后端 → 本地不参与原生探测，同样直接判无。"""
    r = _router()

    async def _local_only(*a, **k):
        return [SimpleNamespace(type="local")]

    monkeypatch.setattr(storage, "list_backends", _local_only)

    async def _boom_build(self):
        raise AssertionError("仅本地后端时不得构建内网/云端客户端")

    monkeypatch.setattr(LMRouter, "_build_private_client", _boom_build)
    monkeypatch.setattr(LMRouter, "_build_cloud_client", _boom_build)

    assert await r.resolve_native_backend() is None


async def test_enabled_private_backend_still_probes(monkeypatch):
    """对照组：存在已启用内网后端 → 快路径不拦截，照常探测并命中。"""
    r = _router()

    async def _private_enabled(*a, **k):
        return [SimpleNamespace(type="private")]

    monkeypatch.setattr(storage, "list_backends", _private_enabled)

    class _FakeClient:
        async def chat_with_tools(self, *a, **k):
            return None

        async def supports_tool_calling(self):
            return True

    client = _FakeClient()

    async def _build_private(self):
        return client

    async def _no_cloud(self):
        return None

    monkeypatch.setattr(LMRouter, "_build_private_client", _build_private)
    monkeypatch.setattr(LMRouter, "_build_cloud_client", _no_cloud)

    result = await r.resolve_native_backend()
    assert result is not None
    name, backend = result
    assert name == "private"
    assert backend is client
