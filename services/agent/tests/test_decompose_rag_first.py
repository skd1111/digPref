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


# ---- 追问抑制（2026-09-06）：知识库已命中 = 来源已具备，不得反问用户 ----


def _clarify_state(**over) -> dict:
    """「提取出审核规则」式场景：意图分析判来源缺失要追问，但库里有用户上传的资料。"""
    st = _state(
        user_prompt="提取出审核规则",
        intent_analysis={
            "intent": "query",
            "intent_category": "content_generation",
            "need_tool": True,
            "need_clarification": True,
            "clarification_message": "请告知审核规则的来源是哪个？",
            "confidence": 0.9,
            "entities": {},
        },
    )
    st.update(over)
    return st


async def test_clarification_suppressed_when_kb_hits(monkeypatch):
    """need_clarification=true 但知识库已命中 → 据库作答，不把用户上传的文件反问回去。"""
    monkeypatch.setattr(settings, "rag_first_answer_enabled", True)
    out = await decompose_node(_clarify_state(), _ExplodingLLM())
    assert out["decompose_decision"]["decision"]["mode"] == "MAIN_AGENT"
    assert any(t.get("reason") == "rag_first_answer" for t in out["trace"])


async def test_clarification_suppressed_even_low_confidence(monkeypatch):
    """低置信度分析（交决策器复核档）同样先被 RAG 优先拦截，不进盲决策。"""
    monkeypatch.setattr(settings, "rag_first_answer_enabled", True)
    st = _clarify_state(
        intent_analysis={
            "intent": "query",
            "intent_category": "knowledge_qa",
            "need_tool": True,
            "need_clarification": True,
            "confidence": 0.5,
            "entities": {},
        }
    )
    out = await decompose_node(st, _ExplodingLLM())
    assert out["decompose_decision"]["decision"]["mode"] == "MAIN_AGENT"


async def test_clarification_still_asks_without_kb_hits(monkeypatch):
    """知识库无命中 → 追问保留，不过度抑制（真缺信息还是要问）。"""
    monkeypatch.setattr(settings, "rag_first_answer_enabled", True)
    out = await decompose_node(_clarify_state(rag_context=None), _ExplodingLLM())
    assert out["decompose_decision"]["decision"]["mode"] == "ASK_USER"


# ---- 决策器 LLM 召回注入（2026-09-06）：盲决策是「反问来源」的另一条路 ----


class _CapturingLLM:
    """记录编排决策器入参；accept_rag=False 模拟不支持 rag_context 的旧替身。"""

    def __init__(self, accept_rag: bool = True):
        self.kwargs: dict | None = None
        self._accept_rag = accept_rag

    async def decompose(self, **kw):
        if not self._accept_rag and "rag_context" in kw:
            raise TypeError("decompose() got an unexpected keyword argument 'rag_context'")
        self.kwargs = kw
        return {
            "decision": {
                "mode": "MAIN_AGENT",
                "should_enable_subagent": False,
                "execution_allowed": True,
                "user_confirmation_required": False,
                "confidence": 0.9,
                "reason": "captured",
                "clarifying_questions": [],
                "confirmation_message": None,
                "refusal_message": None,
            },
            "scoring": {},
            "selected_subagents": [],
            "tool_calls": [],
            "plan": [],
            "fallback": "none",
        }


def _decider_state(**over) -> dict:
    """低置信度 + 无 rag_context 实例（仅有 addon）→ 必进编排决策器 LLM。"""
    st = _state(
        user_prompt="提取出审核规则",
        rag_context=None,
        system_prompt_addon="【知识库参考】[1] 审核管理办法 第三条 审核规则如下…",
        intent_analysis={
            "intent": "query",
            "intent_category": "content_generation",
            "need_tool": True,
            "need_clarification": False,
            "confidence": 0.5,
            "entities": {},
        },
    )
    st.update(over)
    return st


async def test_decider_receives_rag_context(monkeypatch):
    """召回非空时拼进决策 prompt——决策器不再盲判「来源缺失」。"""
    monkeypatch.setattr(settings, "rag_first_answer_enabled", True)
    llm = _CapturingLLM()
    out = await decompose_node(_decider_state(), llm)
    assert llm.kwargs is not None
    assert "审核管理办法" in str(llm.kwargs.get("rag_context") or "")
    assert out["decompose_decision"]["decision"]["mode"] == "MAIN_AGENT"


async def test_decider_double_without_rag_context_kwarg(monkeypatch):
    """注入替身不接受 rag_context → TypeError 后自动降级旧签名（向后兼容）。"""
    monkeypatch.setattr(settings, "rag_first_answer_enabled", True)
    llm = _CapturingLLM(accept_rag=False)
    out = await decompose_node(_decider_state(), llm)
    assert llm.kwargs is not None
    assert "rag_context" not in llm.kwargs
    assert out["decompose_decision"]["decision"]["mode"] == "MAIN_AGENT"
