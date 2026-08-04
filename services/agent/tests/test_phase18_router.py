"""Phase 18 ModeRouter：三级判定（关键词 → 模式先验 → LLM 兜底）。"""
from __future__ import annotations

import pytest

from agent.dual.router import ModeRouter, mode_router_node


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("帮我修复这个函数的 bug", "coding"),
        ("重构 utils.py 并补上单元测试", "coding"),
        ("查询生产库昨天的订单量", "work"),
        ("生成月度报表并发送通知", "work"),
    ],
)
def test_keyword_layer(prompt, expected):
    r = ModeRouter(llm=None)
    assert r.keyword_route(prompt) == expected


def test_mixed_keywords():
    r = ModeRouter(llm=None)
    assert r.keyword_route("写个导出脚本，然后在数据库里跑一下报表") == "mixed"


def test_no_keyword_hit_returns_none():
    r = ModeRouter(llm=None)
    assert r.keyword_route("今天天气怎么样") is None


def test_mode_prior_fallback():
    r = ModeRouter(llm=None)
    # 关键词无命中时按模式先验：full（开发模式）→ coding，其他 → work
    assert r.prior_route("处理一下这个东西", work_mode="full") == "coding"
    assert r.prior_route("处理一下这个东西", work_mode="operator") == "work"
    assert r.prior_route("处理一下这个东西", work_mode="analyst") == "work"


async def test_route_without_llm_uses_keyword_then_prior():
    r = ModeRouter(llm=None)
    routing, overridden = await r.route("修复这个 bug", work_mode="operator")
    # 关键词命中 coding，但模式先验是 work → overridden=True
    assert routing == "coding"
    assert overridden is True

    routing, overridden = await r.route("修复这个 bug", work_mode="full")
    assert routing == "coding"
    assert overridden is False

    routing, overridden = await r.route("随便聊聊", work_mode="operator")
    assert routing == "work"
    assert overridden is False


async def test_mode_router_node_writes_state():
    state = {"user_prompt": "查询生产库昨天的订单量", "work_mode": "full"}
    out = await mode_router_node(state, llm=None)
    assert out["routing"] == "work"
    assert out["routing_overridden"] is True
    assert out["routing_declaration"]
    assert any(t["node"] == "mode_router" for t in out["trace"])


async def test_mode_router_node_no_declaration_when_aligned():
    state = {"user_prompt": "修复这个 bug", "work_mode": "full"}
    out = await mode_router_node(state, llm=None)
    assert out["routing"] == "coding"
    assert out["routing_overridden"] is False
    assert out["routing_declaration"] is None


async def test_llm_layer_maps_hybrid_to_mixed():
    """提示词体系的 HYBRID 与内部 mixed 等价（Code/Work 双模式提示词对齐）。"""

    class _LLM:
        async def route(self, *, task: str, prompt: str) -> str:
            return "HYBRID"

    r = ModeRouter(llm=_LLM())
    assert await r.llm_route("x", work_mode="full") == "mixed"


async def test_mode_router_node_injects_dual_rules():
    """mode_router 必须写入双模式执行纪律（注入工具循环 prompt）。"""
    state = {"user_prompt": "修复这个 bug", "work_mode": "full"}
    out = await mode_router_node(state, llm=None)
    assert "双模式执行纪律" in out["dual_rules_addon"]
    assert "CODE" in out["dual_rules_addon"]
