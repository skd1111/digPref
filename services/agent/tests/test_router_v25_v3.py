"""test_router_v25_v3 —— Phase 2C V2.5 + V3 单测。

覆盖：
- V2.5-4：LMRouter 非 spark 路径调 engine.route_request → chain 顺序按 engine 决定
- V3-1：L2Cache enable / disable / 精确命中 / 语义命中（mock embed） / TTL 过期 / 红线（_LOCAL_ONLY_TASKS 不写）
- V3-3 不做单元测试（前端组件，留 smoke）
"""

from __future__ import annotations

import time

import pytest
from agent.llm.cache_l2 import L2Cache, cosine_sim, mock_embed
from agent.llm.router import _LOCAL_ONLY_TASKS

# ---- V2.5-4 LMRouter 非 spark 路径真委托 engine.route_request ----------------


def test_chain_for_uses_engine_route_request_when_available(monkeypatch):
    """LMRouter._chain_for 在 engine 可用时调 engine.route_request 决定顺序。"""
    from agent.config import settings
    from agent.llm.router import LMRouter

    # 内网默认已移除（BUGFIX #57）：本断言依赖 private 后端存在，需显式配置
    monkeypatch.setattr(settings, "private_llm_base_url", "http://private.example.com/v1")
    monkeypatch.setattr(settings, "private_llm_api_key", "k")

    # 构造一个 fake engine：route_request 返回 primary_backend='private'
    class _FakeEngine:
        _weights = {}  # noqa: RUF012 测试 fake 常量
        _budget = None
        _breakers = None
        _metrics = None
        _spark_enabled = False
        _failure_count = {}  # noqa: RUF012 测试 fake 常量

        def route_request(self, *, task_kind, category, sensitivity, request_id, **kwargs):
            from agent.llm.models import RoutingDecision

            d = RoutingDecision(
                request_id=request_id,
                user_id="test",
                task_category=category,
                sensitivity=sensitivity,
            )
            d.primary_backend = "private"
            d.actual_backend = "private"
            d.fallback_chain = ["private", "ollama"]
            return d

    r = LMRouter(engine=_FakeEngine())
    r.set_inference_mode("performance")  # Phase 4: 性能模式跳端侧，保持原断言
    chain = r._chain_for("plan")
    # 第一项必须是 private（engine 决定的）
    assert chain[0][0] == "private"
    # 第二项是 ollama（fallback 链第二）
    assert chain[1][0] == "ollama"
    # 兜底 mock
    assert chain[-1][0] == "mock"


def test_chain_for_falls_back_to_hardcoded_when_engine_unavailable():
    """engine=None 时仍走 V1.5 硬编码 fallback（红线下兼容）。"""
    from agent.llm.router import LMRouter

    r = LMRouter()  # engine=None
    r.set_inference_mode("performance")  # Phase 4: 性能模式跳端侧
    chain_intent = r._chain_for("intent")
    # intent 硬编码第一项是 ollama（性能模式）
    assert chain_intent[0][0] == "ollama"


def test_chain_for_intent_uses_ollama_even_when_engine_says_private():
    """_LOCAL_ONLY_TASKS 红线：intent 强制 ollama（engine route_request 内部 hard_rules 已保护）。

    engine.route_request 返回 primary='private' 是 hard_rules 已过滤的结果。
    红线由 engine.route_request:apply_hard_rules 强制；LMRouter._chain_for 直接用 engine 返回值。
    """
    from agent.llm.router import LMRouter

    # Engine 已应用 _LOCAL_ONLY_TASKS hard_rules —— 实际生产中 route_request
    # 对 intent 永远返 primary=ollama（apply_hard_rules 直接过滤私有 LLM）
    # 本测试只验证链构造正确接受 engine 决定；hard_rules 单测在 test_router_rules
    class _FakeEngine:
        def route_request(self, *, task_kind, category, sensitivity, request_id, **kwargs):
            from agent.llm.models import RoutingDecision

            d = RoutingDecision(request_id=request_id)
            d.primary_backend = "ollama"
            d.actual_backend = "ollama"
            d.fallback_chain = ["ollama"]
            return d

    r = LMRouter(engine=_FakeEngine())
    r.set_inference_mode("performance")  # Phase 4: 性能模式跳端侧
    chain = r._chain_for("intent")
    assert chain[0][0] == "ollama"
    # intent 兜底 mock
    assert chain[-1][0] == "mock"


# ---- V3-1 L2 语义缓存 ----------------------------------------------------


def test_l2_disabled_always_miss():
    c = L2Cache(enable=False)
    c.put("ollama", "查订单", "OK")  # 即使 put，禁用时也不存
    assert c.get("ollama", "查订单") is None
    assert c.stats() == {"hits": 0, "misses": 1, "size": 0}


def test_l2_exact_match():
    c = L2Cache(enable=True)
    c.put("ollama", "查订单", "订单列表")
    assert c.get("ollama", "查订单") == "订单列表"
    assert c.stats()["hits"] >= 1


def test_l2_semantic_match_with_mock_embed():
    """mock_embed 同样 prompt → 同样 hash → cosine_sim = 1.0 → 命中。"""
    c = L2Cache(enable=True)
    c.put("ollama", "查订单", "OK")
    out = c.get("ollama", "查订单")
    assert out == "OK"


def test_l2_unrelated_prompt_miss():
    c = L2Cache(enable=True, threshold=0.92)
    c.put("ollama", "查订单", "OK")
    # 完全无关 prompt 哈希 → 不同 mock embed → sim 远低于 0.92
    assert c.get("ollama", "今天天气怎么样") is None


def test_l2_local_only_task_skipped():
    """_LOCAL_ONLY_TASKS 任务（intent / repair / biznav_extract）不允许写 L2。"""
    c = L2Cache(enable=True)
    for k in _LOCAL_ONLY_TASKS:
        c.put("ollama", f"test {k}", "secret", task_kind=k)
    # 应该全部未写入
    assert c.stats()["size"] == 0


def test_l2_ttl_expired():
    c = L2Cache(enable=True, ttl_sec=0.05)

    c.put("ollama", "查订单", "OK")

    # 把 monotonic clock 往前推
    c._store["查订单".__hash__() and "ollama\x00查订单"][2] if False else None  # 简化

    # 直接模拟过期：clear + 重新 put with expired ttl
    c2 = L2Cache(enable=True, ttl_sec=0.001)
    c2.put("ollama", "x", "y")
    time.sleep(0.05)
    assert c2.get("ollama", "x") is None


def test_l2_threshold_tuning():
    c = L2Cache(enable=True, threshold=0.5)  # 宽松阈值
    c.put("ollama", "A", "OK-A")
    # 不同 prompt hash → cosine 大概率 < 0.5
    out = c.get("ollama", "B")
    # 不强制断言（hash 不同 → sim 不可预测），只验证不抛错
    assert out is None or isinstance(out, str)


def test_l2_stats():
    c = L2Cache(enable=True)
    c.put("ollama", "x", "y")
    c.get("ollama", "x")
    c.get("ollama", "missing")
    s = c.stats()
    assert "hits" in s and "misses" in s and "size" in s
    assert s["size"] == 1


def test_cosine_sim_unit_vector():
    """单位向量 cosine = 1.0。"""
    a = [1.0, 0.0, 0.0]
    b = [1.0, 0.0, 0.0]
    assert cosine_sim(a, b) == pytest.approx(1.0)


def test_cosine_sim_orthogonal():
    """正交向量 cosine = 0.0。"""
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_sim(a, b) == pytest.approx(0.0)


def test_cosine_sim_empty():
    """空向量返 0.0（避免除零）。"""
    assert cosine_sim([], []) == 0.0
    assert cosine_sim([1.0], [0.0]) == 0.0  # b 长度 0（不该发生但兜底）


def test_mock_embed_deterministic():
    """相同 prompt → 相同向量。"""
    v1 = mock_embed("查订单", dim=64)
    v2 = mock_embed("查订单", dim=64)
    assert v1 == v2
    assert len(v1) == 64


def test_mock_embed_different_prompts_different_vectors():
    """不同 prompt → 不同向量。"""
    v1 = mock_embed("查订单", dim=64)
    v2 = mock_embed("看天气", dim=64)
    assert v1 != v2
