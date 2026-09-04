"""decompose「RAG 优先作答」路由回归测试（根因修复 2026-09-04）。

背景：用户问「找一下对公转账汇兑的规章制度」，知识库 bm25=80 命中，但意图被判
need_tool=true → decompose 走 TOOL_ONLY → 模型进工具循环用 shell 跨盘符全盘翻找
卡死。修复：文档/制度类查询且 RAG 已 BM25 命中相关资料（chunk.metadata.matched
非空）时，decompose 直接走 MAIN_AGENT 据召回作答，不进工具循环。带开关。

判据用 BM25 词元命中而非 reranker 绝对分（cross-encoder logit 量纲不稳）。
"""

from __future__ import annotations

import pytest
from agent.config import settings
from agent.graph.nodes.decompose import decompose_node


@pytest.fixture(autouse=True)
def _enable_tool_loop(monkeypatch):
    """conftest._isolate 默认 tool_loop_enabled=False；本组用例需要工具循环路径。"""
    monkeypatch.setattr(settings, "tool_loop_enabled", True)


class _Chunk:
    def __init__(self, matched):
        self.metadata = {"matched": matched}


class _Result:
    def __init__(self, matched):
        self.chunk = _Chunk(matched)


class _Ctx:
    def __init__(self, results):
        self.results = results


class _ExplodingLLM:
    """快速路径命中时不应调编排决策器 LLM。"""

    async def decompose(self, **kw):  # pragma: no cover
        raise AssertionError("RAG 优先快速路径不应调编排决策器 LLM")


def _state(**over) -> dict:
    st = {
        "run_id": "run-rag-first",
        "intent": "query",
        "user_prompt": "找一下对公转账汇兑的规章制度",
        "messages": [],
        "intent_analysis": {
            "intent": "query",
            "intent_category": "data_query",
            "need_tool": True,
            "need_clarification": False,
            "confidence": 0.92,
            "entities": {"topic": "对公转账汇兑", "doc_type": "规章制度"},
        },
        "rag_context": _Ctx([_Result(["对公转账", "规章制度"])]),
    }
    st.update(over)
    return st


async def test_rag_first_routes_main_agent(monkeypatch):
    monkeypatch.setattr(settings, "rag_first_answer_enabled", True)
    out = await decompose_node(_state(), _ExplodingLLM())
    assert out["decompose_decision"]["decision"]["mode"] == "MAIN_AGENT"
    assert any(t.get("reason") == "rag_first_answer" for t in out["trace"])


async def test_disabled_falls_through_to_tool_only(monkeypatch):
    monkeypatch.setattr(settings, "rag_first_answer_enabled", False)
    out = await decompose_node(_state(), _ExplodingLLM())
    # 关闭开关 → 恢复旧行为：need_tool → TOOL_ONLY
    assert out["decompose_decision"]["decision"]["mode"] == "TOOL_ONLY"


async def test_no_matched_hits_not_rag_first(monkeypatch):
    """召回全为空 matched（无 BM25 字面命中）→ 不判为可据库作答。"""
    monkeypatch.setattr(settings, "rag_first_answer_enabled", True)
    st = _state(rag_context=_Ctx([_Result([]), _Result([])]))
    out = await decompose_node(st, _ExplodingLLM())
    assert out["decompose_decision"]["decision"]["mode"] == "TOOL_ONLY"


async def test_no_rag_context_not_rag_first(monkeypatch):
    monkeypatch.setattr(settings, "rag_first_answer_enabled", True)
    st = _state(rag_context=None)
    out = await decompose_node(st, _ExplodingLLM())
    assert out["decompose_decision"]["decision"]["mode"] == "TOOL_ONLY"


async def test_db_query_not_hijacked(monkeypatch):
    """数据库查询（无 doc_type、无知识库关键词）即使 RAG 有命中也照走工具循环。"""
    monkeypatch.setattr(settings, "rag_first_answer_enabled", True)
    st = _state(
        user_prompt="查询订单表最近一周的记录",
        intent_analysis={
            "intent": "query",
            "intent_category": "data_query",
            "need_tool": True,
            "confidence": 0.9,
            "entities": {"table": "订单表"},
        },
    )
    out = await decompose_node(st, _ExplodingLLM())
    assert out["decompose_decision"]["decision"]["mode"] == "TOOL_ONLY"


async def test_semantic_route_tool_hit_not_hijacked(monkeypatch):
    """已被工具型语义路由命中（带 _route）→ 不抢，交给工具循环。"""
    monkeypatch.setattr(settings, "rag_first_answer_enabled", True)
    st = _state(
        user_prompt="查一下订单表",
        intent_analysis={
            "intent": "query",
            "intent_category": "data_query",
            "need_tool": True,
            "confidence": 0.85,
            "entities": {"doc_type": "规章制度"},
            "_route": "db_query",
        },
    )
    out = await decompose_node(st, _ExplodingLLM())
    assert out["decompose_decision"]["decision"]["mode"] == "TOOL_ONLY"


async def test_min_matched_hits_threshold(monkeypatch):
    """阈值提高到 2 时，只有 1 条 matched 命中不再触发 RAG 优先。"""
    monkeypatch.setattr(settings, "rag_first_answer_enabled", True)
    monkeypatch.setattr(settings, "rag_first_min_matched_hits", 2)
    st = _state(rag_context=_Ctx([_Result(["对公转账"]), _Result([])]))
    out = await decompose_node(st, _ExplodingLLM())
    assert out["decompose_decision"]["decision"]["mode"] == "TOOL_ONLY"
