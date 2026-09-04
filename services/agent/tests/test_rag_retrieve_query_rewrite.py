"""rag_retrieve 检索词优选回归测试（根因修复 2026-09-04）。

现象：追问轮「知识库里没有吗」用原始短句检索（日志 query_chars=7 bm25=15），
只匹配到「知识库」这种泛词，检索退化。修复：优先用意图改写句 rewritten_query
检索；改写句仍短（<12 字）则拼接上一轮用户主题（会话式查询改写，仅影响检索）。
"""

from __future__ import annotations

from typing import ClassVar

from agent.graph.nodes.rag_retrieve import _augment_followup_query, rag_retrieve_node


class _Ctx:
    results: ClassVar[list] = []
    elapsed_ms = 0
    backend = "hybrid"
    formatted_prompt = ""


class _FakeRetriever:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def retrieve(self, query, **kw):
        self.queries.append(query)
        return _Ctx()


async def test_uses_rewritten_query_when_present():
    r = _FakeRetriever()
    state = {
        "user_prompt": "知识库里没有吗",
        "rewritten_query": "在知识库中查找是否有对公转账汇兑相关的规章制度文档",
        "messages": [{"role": "user", "content": "知识库里没有吗"}],
        "trace": [],
    }
    await rag_retrieve_node(state, r)
    assert len(r.queries) == 1
    assert "对公转账汇兑" in r.queries[0]


async def test_falls_back_to_user_prompt():
    r = _FakeRetriever()
    state = {
        "user_prompt": "找一下对公转账汇兑的规章制度",
        "messages": [{"role": "user", "content": "找一下对公转账汇兑的规章制度"}],
        "trace": [],
    }
    await rag_retrieve_node(state, r)
    assert r.queries == ["找一下对公转账汇兑的规章制度"]


async def test_short_followup_augmented_with_prior_turn():
    """改写句缺失且当轮很短 → 拼接上一轮用户主题，让检索拿到实质词。"""
    r = _FakeRetriever()
    state = {
        "user_prompt": "知识库里没有吗",
        "messages": [
            {"role": "user", "content": "找一下对公转账汇兑的规章制度"},
            {"role": "assistant", "content": "请问属于哪个范围？A/B/C"},
            {"role": "user", "content": "知识库里没有吗"},
        ],
        "trace": [],
    }
    await rag_retrieve_node(state, r)
    assert len(r.queries) == 1
    assert "对公转账汇兑" in r.queries[0]
    assert "知识库里没有吗" in r.queries[0]


def test_augment_noop_for_long_query():
    state = {
        "user_prompt": "x",
        "messages": [{"role": "user", "content": "找一下对公转账汇兑的规章制度"}],
    }
    long_q = "在知识库中查找是否有对公转账汇兑相关的规章制度文档"
    assert _augment_followup_query(long_q, state) == long_q


def test_augment_returns_query_when_no_prior_turn():
    state = {"user_prompt": "库里有吗", "messages": [{"role": "user", "content": "库里有吗"}]}
    assert _augment_followup_query("库里有吗", state) == "库里有吗"
