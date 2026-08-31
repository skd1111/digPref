"""上下文感知与短期记忆测试（2026-08-31）：活跃实体注入 + 操作链路继承。"""

from __future__ import annotations

import pytest
from agent.graph.state import empty_state, format_page_context

# ---- 活跃实体协议 ----------------------------------------------------------------


class TestActiveEntity:
    def test_format_active_entity_table(self):
        ctx = {
            "page": {
                "workMode": "operator",
                "tabTitle": "数据工作台",
                "activeEntity": {"kind": "table", "name": "order_main"},
            }
        }
        line = format_page_context(ctx)
        assert "当前正查看数据表 order_main" in line
        assert "模式 operator" in line

    def test_format_active_entity_terminal_and_file(self):
        assert "终端会话" in format_page_context(
            {"page": {"activeEntity": {"kind": "terminal", "name": "prod-web-01"}}}
        )
        assert "文件" in format_page_context(
            {"page": {"activeEntity": {"kind": "file", "name": "a.py"}}}
        )

    def test_format_active_entity_invalid_ignored(self):
        assert format_page_context({"page": {"activeEntity": "bad"}}) == ""
        assert format_page_context({"page": {"activeEntity": {"kind": "table"}}}) == ""
        assert format_page_context(None) == ""


# ---- 操作链路短期记忆 --------------------------------------------------------------


class _CapturingLLM:
    """捕获 page_context 的意图分析替身。"""

    def __init__(self, analysis: dict):
        self.analysis = analysis
        self.captured_page_context: str | None = None

    async def analyze_intent(self, text, history=None, page_context=""):
        self.captured_page_context = page_context
        return dict(self.analysis)

    async def classify_intent(self, text):
        return "query"


class TestOperationChainMemory:
    @pytest.fixture
    def stub_analysis(self):
        return {
            "intent": "query",
            "intent_category": "data_query",
            "need_tool": True,
            "confidence": 0.9,
            "rewritten_query": "查询订单表昨日数据",
            "entities": {"target_table": "order_main"},
            "backend": "ollama",
        }

    async def test_followup_injects_recent_chain(self, monkeypatch, stub_analysis):
        from agent.graph import intent_memory
        from agent.graph.nodes.intent import intent_node

        # 预置该任务页签的近期链路
        await intent_memory.record_recent("tab-1", "data_query｜target_table=order_main")

        # 屏蔽回写副作用（本用例只验证注入）
        async def _noop(*a, **kw):
            return None

        monkeypatch.setattr(intent_memory, "record_example", _noop)
        monkeypatch.setattr(intent_memory, "record_recent", _noop)

        llm = _CapturingLLM(stub_analysis)
        st = empty_state("换个参数再跑一次")
        st["run_id"] = "run-chain"
        st["task_id"] = "tab-1"
        out = await intent_node(st, llm)

        assert out["intent"] == "query"
        assert llm.captured_page_context is not None
        assert "前几轮操作" in llm.captured_page_context
        assert "order_main" in llm.captured_page_context

    async def test_new_task_prompt_no_chain_injection(self, monkeypatch, stub_analysis):
        """长/新任务输入不触发链路注入。"""
        from agent.graph import intent_memory
        from agent.graph.nodes.intent import intent_node

        await intent_memory.record_recent("tab-1", "data_query｜target_table=order_main")

        async def _noop(*a, **kw):
            return None

        monkeypatch.setattr(intent_memory, "record_example", _noop)
        monkeypatch.setattr(intent_memory, "record_recent", _noop)

        llm = _CapturingLLM(stub_analysis)
        st = empty_state("帮我查询信用库最近一周的放款总额并按产品分组汇总")
        st["run_id"] = "run-new"
        st["task_id"] = "tab-1"
        await intent_node(st, llm)
        assert "前几轮操作" not in (llm.captured_page_context or "")

    async def test_slot_guard_runs_after_analysis(self, monkeypatch):
        """analyze_intent 产出高风险缺槽结果 → intent_node 强制追问。"""
        from agent.graph import intent_memory
        from agent.graph.nodes.intent import intent_node

        async def _noop(*a, **kw):
            return None

        monkeypatch.setattr(intent_memory, "record_example", _noop)
        monkeypatch.setattr(intent_memory, "record_recent", _noop)

        risky = {
            "intent": "mutate",
            "intent_category": "task_execution",
            "risk_level": "critical",
            "need_tool": True,
            "confidence": 0.8,
            "rewritten_query": "清空那张表",
            "entities": {},
            "backend": "ollama",
        }
        llm = _CapturingLLM(risky)
        st = empty_state("把那张表的数据全部清掉，生产环境的那张")
        st["run_id"] = "run-guard"
        out = await intent_node(st, llm)
        assert out["intent_analysis"]["need_clarification"] is True
        assert out["intent_analysis"]["missing_fields"]
