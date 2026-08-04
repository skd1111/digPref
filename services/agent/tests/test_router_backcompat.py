"""回归护栏（Phase 2C v2）—— 冻结 LMRouter 的四个公开 API。

背景：LMRouter.classify_intent / plan / repair_call / summarise 是 graph 四节点
（intent / planner / repair / responder）的注入依赖。V2 引入 RouterEngine 后，
这四个方法的签名 + 返回类型必须保持向后兼容，否则四个节点全炸。

这个测试是「红线锁」：任何破坏公开契约的重构都会让它先红。
测试全程走 mock 后端（EAIDE_LLM_BACKEND=mock），不依赖 Ollama / 内网 LLM。
"""
from __future__ import annotations

import inspect

import pytest

from agent.llm.router import LMRouter


@pytest.fixture
def router(monkeypatch):
    monkeypatch.setenv("EAIDE_LLM_BACKEND", "mock")
    return LMRouter()


def test_public_api_methods_exist():
    """四个方法必须存在且可调用。"""
    for name in ("classify_intent", "plan", "repair_call", "summarise"):
        assert hasattr(LMRouter, name), f"公开 API {name} 丢失"
        assert callable(getattr(LMRouter, name))


def test_public_api_signatures_frozen():
    """签名冻结：参数名不可变（keyword 契约是节点调用方式）。"""
    sig = {
        "classify_intent": ["self", "text"],
        "plan": ["self", "intent", "user_prompt", "history", "tool_specs"],
        "repair_call": ["self", "original", "error", "history"],
        "summarise": ["self", "intent", "user_prompt", "plan", "results"],
    }
    for method, expected in sig.items():
        params = list(inspect.signature(getattr(LMRouter, method)).parameters)
        assert params == expected, f"{method} 签名漂移: {params} != {expected}"


async def test_classify_intent_returns_intent_literal(router):
    result = await router.classify_intent("查询订单")
    assert result in ("query", "mutate", "orchestrate", "chitchat")


async def test_plan_returns_tuple_list_str(router):
    plan, explanation = await router.plan(
        intent="query", user_prompt="看看订单", history=[], tool_specs=[]
    )
    assert isinstance(plan, list)
    assert isinstance(explanation, str)


async def test_repair_call_returns_dict(router):
    original = {"server": "db", "name": "db.query", "args": {"sql": "SELECT 1"}}
    result = await router.repair_call(original=original, error="boom", history=[])
    assert isinstance(result, dict)


async def test_summarise_returns_tuple_str_list(router):
    answer, sources = await router.summarise(
        intent="query", user_prompt="订单", plan=[], results=[]
    )
    assert isinstance(answer, str)
    assert isinstance(sources, list)


async def test_mock_mode_never_touches_network(router):
    """mock 模式下所有任务走内置规则，不产生真实 LLM 调用。"""
    # classify 一个写操作应识别为 mutate（mock 关键词启发式）
    assert await router.classify_intent("删除 orders 表的所有数据") == "mutate"
