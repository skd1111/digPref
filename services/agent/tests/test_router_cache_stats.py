"""Phase 17 V0：缓存命中率统计 + L1 接线测试。

覆盖：
    - normalize 稳定 key（空白归一 / 字段顺序 / 差异区分）
    - summarise L1 端到端：相同请求第二次命中，省一次后端调用
    - 红线：含写工具的 plan 绝不缓存（写操作结果不可复用）
    - 一键回滚开关：关闭后不查不写
    - GET /router/cache-stats / POST /router/cache-toggle 端点
"""

from __future__ import annotations

import pytest
from agent.llm import engine_api
from agent.llm import router as router_mod
from agent.llm.normalize import build_response_cache_key, canonical_json, normalize_text
from agent.llm.router import LMRouter
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_l1():
    """每个用例独立：清空模块级 L1 单例 + 恢复开关。"""
    router_mod.get_l1_cache().clear()
    router_mod.set_l1_cache_enabled(True)
    yield
    router_mod.get_l1_cache().clear()
    router_mod.set_l1_cache_enabled(True)


class _FakeBackend:
    """计数后端：记录真实调用次数，返回可区分的回答。"""

    def __init__(self) -> None:
        self.calls = 0
        self.base_url = "http://fake-backend"

    async def summarise(self, *, intent, user_prompt, plan, results, history=None):
        self.calls += 1
        return (f"答案-{self.calls}", ["src-a"])


def _make_router(monkeypatch) -> tuple[LMRouter, _FakeBackend]:
    r = LMRouter()
    fake = _FakeBackend()
    monkeypatch.setattr(r, "ollama", fake)
    monkeypatch.setattr(r, "private", None)
    return r, fake


# ---- normalize -----------------------------------------------------------


def test_canonical_json_stable_key_order():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_normalize_text_collapses_whitespace():
    assert normalize_text("  你好   世界 \n ") == "你好 世界"


def test_cache_key_stable_and_distinct():
    k1 = build_response_cache_key(
        task_kind="summarise", intent="query", user_prompt="  查订单  ", plan=[], results=[]
    )
    k2 = build_response_cache_key(
        task_kind="summarise", intent="query", user_prompt="查订单", plan=[], results=[]
    )
    assert k1 == k2  # 空白归一 → 相同 key
    k3 = build_response_cache_key(
        task_kind="summarise", intent="chitchat", user_prompt="查订单", plan=[], results=[]
    )
    assert k1 != k3  # intent 不同 → 不同 key


# ---- summarise L1 端到端 --------------------------------------------------


async def test_summarise_second_call_hits_l1(monkeypatch):
    r, fake = _make_router(monkeypatch)
    kw = {"intent": "query", "user_prompt": "订单 123 状态？", "plan": [], "results": []}
    a1, s1 = await r.summarise(**kw)
    a2, s2 = await r.summarise(**kw)
    assert (a1, s1) == (a2, s2) == ("答案-1", ["src-a"])
    assert fake.calls == 1  # 第二次走缓存，后端只被打 1 次
    l1 = router_mod.get_l1_cache()
    assert l1.hits == 1 and l1.misses == 1


async def test_summarise_different_prompt_misses(monkeypatch):
    r, fake = _make_router(monkeypatch)
    await r.summarise(intent="query", user_prompt="问题A", plan=[], results=[])
    await r.summarise(intent="query", user_prompt="问题B", plan=[], results=[])
    assert fake.calls == 2


async def test_write_tool_plan_never_cached(monkeypatch):
    """红线回归锁：含写工具的 plan 不查不写（写操作绝不被缓存复用）。"""
    r, fake = _make_router(monkeypatch)
    kw = {
        "intent": "query",
        "user_prompt": "删掉临时文件",
        "plan": [{"tool": "delete_file", "args": {"path": "/tmp/x"}}],
        "results": [],
    }
    await r.summarise(**kw)
    await r.summarise(**kw)
    assert fake.calls == 2
    assert router_mod.get_l1_cache().size == 0


async def test_toggle_off_bypasses_cache(monkeypatch):
    r, fake = _make_router(monkeypatch)
    router_mod.set_l1_cache_enabled(False)
    kw = {"intent": "query", "user_prompt": "同一问题", "plan": [], "results": []}
    await r.summarise(**kw)
    await r.summarise(**kw)
    assert fake.calls == 2
    assert router_mod.get_l1_cache().size == 0


# ---- 统计端点 -------------------------------------------------------------


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    app = FastAPI()
    app.include_router(engine_api.router)
    return TestClient(app)


def test_cache_stats_endpoint(client):
    router_mod.get_l1_cache().put("k", "v")
    router_mod.get_l1_cache().get("k")  # hit
    r = client.get("/router/cache-stats")
    assert r.status_code == 200
    data = r.json()
    assert data["l1_exact"]["enabled"] is True
    assert data["l1_exact"]["hits"] == 1
    assert data["l1_exact"]["hit_rate"] == 1.0
    assert data["l2_semantic"]["enabled"] is False
    assert "decisions" in data


def test_cache_toggle_endpoint(client):
    r = client.post("/router/cache-toggle", json={"enabled": False})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "l1_enabled": False}
    assert router_mod.is_l1_cache_enabled() is False
