"""Phase 17 收尾测试：prompt 版本化 / 工具 canonical 顺序 / L3 工具缓存 /
Ollama keep_alive / SSE 通道注册 / 统计扩展。

范围裁剪（2026-08-10 用户决策）：本地不自建 RAG（未来走外部 RAG 接口，
本地检索用 grep），故无 embedding/检索/L2 相关测试。
"""

from __future__ import annotations

import pytest
from agent.graph.stream import _CHANNEL_BY_KIND
from agent.llm import prompts as prompts_mod
from agent.llm import router as router_mod
from agent.llm import tool_cache
from agent.llm.ollama import OllamaClient
from agent.tools.catalog import ToolCatalog


@pytest.fixture(autouse=True)
def _reset_caches():
    router_mod.get_l1_cache().clear()
    tool_cache.get_tool_cache().clear()
    tool_cache.set_tool_cache_enabled(True)
    yield
    router_mod.get_l1_cache().clear()
    tool_cache.get_tool_cache().clear()
    tool_cache.set_tool_cache_enabled(True)


# ---- prompt 版本化 --------------------------------------------------------


def test_prompt_version_default():
    assert prompts_mod.prompt_version("system") == "v1.0.0"
    assert prompts_mod.prompt_version("不存在的资产") == "v1.0.0"


def test_bump_prompt_version_invalidates_l1():
    router_mod.get_l1_cache().put("k", "v")
    assert router_mod.get_l1_cache().size == 1
    before = prompts_mod.prompt_version("summarise")
    after = prompts_mod.bump_prompt_version("summarise")
    assert after != before
    # 主动失效：prompt 变了 → 旧精确缓存全部作废
    assert router_mod.get_l1_cache().size == 0
    # 还原版本表（避免污染其他用例）
    prompts_mod.PROMPT_VERSIONS["summarise"] = before


# ---- L3 工具结果缓存 ------------------------------------------------------


def test_tool_cache_whitelist_and_write_guard():
    assert tool_cache.cacheable_tool("read_file", {"path": "/a"}) is True
    assert tool_cache.cacheable_tool("calculator", {"expr": "1+1"}) is True
    # 白名单外 / 写工具一律不缓存
    assert tool_cache.cacheable_tool("shell", {"cmd": "ls"}) is False
    assert tool_cache.cacheable_tool("write_file", {"path": "/a"}) is False
    assert tool_cache.cacheable_tool("http_post", {"url": "x"}) is False


def test_tool_cache_store_lookup_roundtrip():
    args = {"path": "/tmp/x.txt"}
    assert tool_cache.lookup("read_file", args) is None
    tool_cache.store("read_file", args, {"name": "read_file", "ok": True, "result": "内容"})
    hit = tool_cache.lookup("read_file", args)
    assert hit is not None and hit["result"] == "内容"
    # 写工具 store 无效（双重防御）
    tool_cache.store("write_file", args, {"ok": True})
    assert tool_cache.lookup("write_file", args) is None


async def test_catalog_execute_uses_l3_cache(monkeypatch):
    cat = ToolCatalog(mcp=None)
    calls = {"n": 0}

    async def fake_builtin(name, args, state):
        calls["n"] += 1
        return {"name": name, "ok": True, "result": f"内容-{calls['n']}"}

    monkeypatch.setattr(cat, "_execute_builtin", fake_builtin)
    first = await cat.execute("read_file", {"path": "/a"}, {})
    r2 = await cat.execute("read_file", {"path": "/a"}, {})
    assert calls["n"] == 1  # 第二次走缓存
    assert first["result"] == "内容-1"
    assert r2["result"] == "内容-1"
    assert r2.get("cache_hit") is True

    # 失败结果不缓存
    async def failing(name, args, state):
        calls["n"] += 1
        return {"name": name, "ok": False, "error": "boom"}

    monkeypatch.setattr(cat, "_execute_builtin", failing)
    await cat.execute("list_dir", {"path": "/b"}, {})
    await cat.execute("list_dir", {"path": "/b"}, {})
    assert calls["n"] == 3  # 两次都真执行（失败不入缓存）


async def test_catalog_mcp_specs_sorted_stable():
    class _FakeMCP:
        async def list_tools(self):
            # 乱序返回 —— catalog 必须稳定排序（前缀缓存友好）
            return [
                {"server": "db", "name": "query", "description": ""},
                {"server": "rest", "name": "get", "description": ""},
                {"server": "db", "name": "list_tables", "description": ""},
            ]

    cat = ToolCatalog(mcp=_FakeMCP())
    specs = await cat._get_mcp_specs()
    names = [f"{s['server']}.{s['name']}" for s in specs]
    assert names == ["db.list_tables", "db.query", "rest.get"]


# ---- Ollama keep_alive ----------------------------------------------------


def test_ollama_keep_alive_default_and_inject():
    c = OllamaClient(base_url="http://127.0.0.1:11434", model="m")
    assert c.keep_alive == "10m"
    c2 = OllamaClient(base_url="http://x", model="m", keep_alive="0")
    assert c2.keep_alive == "0"


# ---- SSE 三处同步（Python 侧）--------------------------------------------


def test_sse_channel_registered():
    assert _CHANNEL_BY_KIND.get("llm_cache_stats") == "agent://llm_cache_stats"


# ---- 统计端点扩展 ----------------------------------------------------------


def test_cache_stats_includes_l3(tmp_path, monkeypatch):
    from agent.llm import engine_api
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("APPDATA", str(tmp_path))
    app = FastAPI()
    app.include_router(engine_api.router)
    client = TestClient(app)

    tool_cache.store("read_file", {"path": "/s"}, {"name": "read_file", "ok": True})
    tool_cache.lookup("read_file", {"path": "/s"})  # hit

    r = client.get("/router/cache-stats")
    assert r.status_code == 200
    data = r.json()
    assert data["l3_tool_result"]["enabled"] is True
    assert data["l3_tool_result"]["hits"] == 1
    assert data["l2_semantic"]["enabled"] is False  # 本地不自建 RAG，L2 搁置
