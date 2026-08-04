"""Phase 12 V2 —— 自动多智能体判定（编排决策器）测试。

覆盖：
    - decompose 节点：MULTI/SINGLE_SUBAGENT 派生、ASK_USER/REFUSE/确认门槛不执行、
      TOOL_ONLY/MAIN_AGENT 单 Agent、异常与 fallback 保守回退
    - 路由：route_after_decompose 六种模式 + 确认门槛
    - responder：终态模式输出 + 子智能体回报汇总
    - LMRouter：决策 JSON 解析 / 提示词占位符填充 / mock fallback
    - context_strategy：执行模板渲染
    - 图级端到端：MULTI_SUBAGENT 自动派生 → responder 汇总
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from agent.graph.compile import Runtime, compile_graph
from agent.graph.edges import route_after_decompose
from agent.graph.nodes.decompose import decompose_node
from agent.graph.nodes.responder import responder_node
from agent.graph.state import empty_state
from agent.llm import router as router_mod
from agent.llm.router import LMRouter, _fallback_decision, _parse_orchestration_decision
from agent.orchestrator.context_strategy import build_context
from agent.orchestrator.orchestrator import reset_orchestrator
from agent.orchestrator.spec import (
    ContextPolicy,
    ModelPolicy,
    StateDelta,
    SubAgentReport,
    SubAgentSpec,
    SubAgentStatus,
)


# ---- 决策构造工具 ----------------------------------------------------------

def _subagent(name="research_agent", role="调研分析", task="调研主流框架并生成对比报告"):
    return {
        "name": name,
        "role": role,
        "task": task,
        "inputs": {
            "user_goal": "完成技术选型",
            "context": "需要可靠来源",
            "constraints": ["输出中文"],
            "output_format": "结构化报告",
        },
        "expected_output": "包含对比表的中文报告",
        "allowed_tools": [],
        "priority": "high",
        "dependencies": [],
        "stop_condition": "信息足够后停止",
    }


def _decision(
    mode: str,
    *,
    subagents: list[dict] | None = None,
    execution_allowed: bool = True,
    confirmation: bool = False,
    confirmation_message: str = "",
    questions: list[str] | None = None,
    refusal: str = "",
) -> dict:
    return {
        "decision": {
            "mode": mode,
            "should_enable_subagent": mode in ("SINGLE_SUBAGENT", "MULTI_SUBAGENT"),
            "execution_allowed": execution_allowed,
            "user_confirmation_required": confirmation,
            "confidence": 0.9,
            "reason": "test decision",
            "clarifying_questions": questions or [],
            "confirmation_message": confirmation_message,
            "refusal_message": refusal,
        },
        "scoring": {"complexity": 4, "specialist_need": 4, "risk": 2},
        "selected_subagents": subagents or [],
        "tool_calls": [],
        "plan": [],
        "fallback": "none",
    }


# ---- 伪对象 ----------------------------------------------------------------

class _DeciderLLM:
    """支持 decompose 决策 + summarise 的 LMRouter 替身。"""

    def __init__(self, decision: Any, fail: bool = False, plan_steps: list | None = None) -> None:
        self.decision = decision
        self.fail = fail
        self.plan_steps = plan_steps or []
        self.decompose_calls: list[dict] = []
        self.summarise_calls = 0

    async def classify_intent(self, text: str) -> str:
        return "query"

    async def plan(self, *, intent, user_prompt, history, tool_specs):
        return self.plan_steps, "scripted plan"

    async def repair_call(self, *, original, error, history):
        return original

    async def decompose(self, **kwargs: Any) -> Any:
        self.decompose_calls.append(kwargs)
        if self.fail:
            raise RuntimeError("decompose boom")
        return self.decision

    async def summarise(self, *, intent, user_prompt, plan, results):
        self.summarise_calls += 1
        return "综合后的最终答案。", ["sub"]


class _FakeOrchestrator:
    """记录 spawn 调用并返回 OK 报告的替身。"""

    def __init__(self) -> None:
        self.spawned: list[SubAgentSpec] = []

    async def spawn(self, spec: SubAgentSpec) -> SubAgentReport:
        self.spawned.append(spec)
        return SubAgentReport(
            spec_version=1,
            sub_agent_id=spec.sub_agent_id,
            parent_run_id=spec.parent_run_id,
            parent_sub_agent_id=None,
            status=SubAgentStatus.OK,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            summary=f"report:{spec.task_description[:20]}",
            confidence=0.9,
            state_delta=StateDelta(fields_added={"task_type": spec.task_type}),
            backend_used="ollama",
            model_used="",
            latency_ms=10,
            error_message="",
            attempts=1,
        )


def _state(prompt: str = "帮我分析多智能体框架", *, intent: str = "query", plan: list | None = None) -> dict:
    st = empty_state(prompt)
    st["run_id"] = "run-test"
    st["intent"] = intent
    st["plan"] = plan or []
    return st


# ---- decompose 节点 ---------------------------------------------------------

class TestDecomposeNode:
    async def test_multi_subagent_spawns_and_collects_reports(self):
        decision = _decision(
            "MULTI_SUBAGENT",
            subagents=[_subagent("analysis_agent", "性能分析", "分析性能瓶颈"),
                       _subagent("research_agent", "对比研究", "对比主流方案")],
        )
        llm = _DeciderLLM(decision)
        orch = _FakeOrchestrator()
        out = await decompose_node(_state(), llm, orch=orch)

        assert out["multi_agent"] is True
        assert len(out["sub_agent_reports"]) == 2
        assert len(orch.spawned) == 2
        for spec in orch.spawned:
            assert spec.parent_run_id == "run-test"
            assert spec.depth == 1
            assert spec.requires_write is False
            assert spec.task_type in ("plan", "summarise", "custom", "data_summary")
            assert "execution_template_fields" in spec.input_payload
        assert out["sub_agent_reports"][0]["status"] == "ok"

    async def test_single_subagent_spawns_one(self):
        decision = _decision("SINGLE_SUBAGENT", subagents=[_subagent()])
        llm = _DeciderLLM(decision)
        orch = _FakeOrchestrator()
        out = await decompose_node(_state(), llm, orch=orch)
        assert out["multi_agent"] is True
        assert len(orch.spawned) == 1
        assert orch.spawned[0].task_type == "summarise"  # research → summarise

    async def test_ask_user_no_spawn(self):
        decision = _decision("ASK_USER", questions=["操作对象是什么？"])
        llm = _DeciderLLM(decision)
        orch = _FakeOrchestrator()
        out = await decompose_node(_state(), llm, orch=orch)
        assert out["multi_agent"] is False
        assert orch.spawned == []
        assert out["decompose_decision"]["decision"]["mode"] == "ASK_USER"

    async def test_refuse_no_spawn(self):
        decision = _decision("REFUSE", refusal="该请求无法执行。")
        llm = _DeciderLLM(decision)
        orch = _FakeOrchestrator()
        out = await decompose_node(_state(), llm, orch=orch)
        assert out["multi_agent"] is False
        assert orch.spawned == []
        assert out["decompose_decision"]["decision"]["mode"] == "REFUSE"

    async def test_confirmation_required_blocks_execution(self):
        decision = _decision(
            "SINGLE_SUBAGENT",
            subagents=[_subagent()],
            execution_allowed=False,
            confirmation=True,
            confirmation_message="删除操作需确认。",
        )
        llm = _DeciderLLM(decision)
        orch = _FakeOrchestrator()
        out = await decompose_node(_state(intent="query"), llm, orch=orch)
        assert out["multi_agent"] is False
        assert orch.spawned == []
        assert out["decompose_decision"]["decision"]["user_confirmation_required"] is True

    async def test_tool_only_no_spawn(self):
        decision = _decision("TOOL_ONLY")
        llm = _DeciderLLM(decision)
        orch = _FakeOrchestrator()
        out = await decompose_node(_state(plan=[{"server": "db", "name": "db.query"}]), llm, orch=orch)
        assert out["multi_agent"] is False
        assert orch.spawned == []
        assert out["decompose_decision"]["decision"]["mode"] == "TOOL_ONLY"

    async def test_llm_error_falls_back_to_single_agent(self):
        llm = _DeciderLLM(None, fail=True)
        orch = _FakeOrchestrator()
        out = await decompose_node(_state(), llm, orch=orch)
        assert out["multi_agent"] is False
        assert out["decompose_decision"] is None
        assert orch.spawned == []

    async def test_fallback_flag_is_conservative(self):
        llm = _DeciderLLM(_fallback_decision([]))
        orch = _FakeOrchestrator()
        out = await decompose_node(_state(), llm, orch=orch)
        assert out["multi_agent"] is False
        assert out["decompose_decision"] is None

    async def test_chitchat_and_mutate_skipped(self):
        orch = _FakeOrchestrator()
        llm = _DeciderLLM(_decision("MULTI_SUBAGENT", subagents=[_subagent(), _subagent("plan_agent")]))
        out = await decompose_node(_state(intent="chitchat"), llm, orch=orch)
        assert out["multi_agent"] is False
        assert orch.spawned == []
        out = await decompose_node(_state(intent="mutate", plan=[{"server": "db", "name": "db.execute"}]), llm, orch=orch)
        assert out["multi_agent"] is False
        assert orch.spawned == []


# ---- 路由 ------------------------------------------------------------------

class TestRouting:
    @pytest.mark.parametrize(
        ("state_patch", "expected"),
        [
            ({"multi_agent": True, "plan": [{"server": "db", "name": "db.query"}]}, "responder"),
            ({"decompose_decision": _decision("MAIN_AGENT"), "plan": [{"server": "db", "name": "db.query"}]}, "responder"),
            ({"decompose_decision": _decision("ASK_USER", questions=["?"]), "plan": [{"server": "db", "name": "db.query"}]}, "responder"),
            ({"decompose_decision": _decision("REFUSE", refusal="no")}, "responder"),
            (
                {"decompose_decision": _decision("SINGLE_SUBAGENT", subagents=[_subagent()], confirmation=True, confirmation_message="确认")},
                "responder",
            ),
            ({"decompose_decision": _decision("TOOL_ONLY"), "plan": [{"server": "db", "name": "db.query"}]}, "tool_runner"),
            ({"plan": [{"server": "db", "name": "db.query"}]}, "tool_runner"),
            ({}, "responder"),
        ],
    )
    def test_route_after_decompose(self, state_patch: dict, expected: str):
        st = empty_state("x")
        st.update(state_patch)
        assert route_after_decompose(st) == expected


# ---- responder -------------------------------------------------------------

class TestResponder:
    async def test_refuse_answer(self):
        st = _state()
        st["decompose_decision"] = _decision("REFUSE", refusal="出于安全策略，无法执行。")
        out = await responder_node(st, _DeciderLLM(None))
        assert "安全策略" in out["final_answer"]

    async def test_ask_user_answer(self):
        st = _state()
        st["decompose_decision"] = _decision("ASK_USER", questions=["目标环境？", "时间范围？"])
        out = await responder_node(st, _DeciderLLM(None))
        assert "目标环境" in out["final_answer"]
        assert "时间范围" in out["final_answer"]

    async def test_confirmation_answer(self):
        st = _state()
        st["decompose_decision"] = _decision(
            "SINGLE_SUBAGENT", subagents=[_subagent()],
            confirmation=True, confirmation_message="将删除测试库 90 天前的数据。",
        )
        out = await responder_node(st, _DeciderLLM(None))
        assert "删除测试库" in out["final_answer"]
        assert "未确认前不会执行" in out["final_answer"]

    async def test_main_agent_direct_answer(self):
        st = _state("解释一下 RAG")
        st["decompose_decision"] = _decision("MAIN_AGENT")
        llm = _DeciderLLM(None)
        out = await responder_node(st, llm)
        assert out["final_answer"] == "综合后的最终答案。"
        assert llm.summarise_calls == 1

    async def test_merges_sub_agent_reports(self):
        st = _state()
        st["multi_agent"] = True
        st["sub_agent_reports"] = [
            {"sub_agent_id": "run-test-sub1", "status": "ok", "summary": "A 分析",
             "error_message": "", "confidence": 0.9, "latency_ms": 5,
             "state_delta": {"fields_added": {"task_type": "summarise"}}},
            {"sub_agent_id": "run-test-sub2", "status": "ok", "summary": "B 分析",
             "error_message": "", "confidence": 0.8, "latency_ms": 7,
             "state_delta": {"fields_added": {"task_type": "plan"}}},
        ]
        llm = _DeciderLLM(None)
        out = await responder_node(st, llm)
        assert out["final_answer"] == "综合后的最终答案。"
        assert out["multi_agent"] is True


# ---- LMRouter 解析 / 提示词 --------------------------------------------------

class TestLMRouterDecision:
    def test_parse_valid_multi_subagent(self):
        raw = json.dumps(
            _decision("MULTI_SUBAGENT", subagents=[_subagent(), _subagent("plan_agent")]),
            ensure_ascii=False,
        )
        parsed = _parse_orchestration_decision(raw)
        assert parsed is not None
        assert parsed["decision"]["mode"] == "MULTI_SUBAGENT"
        assert parsed["decision"]["should_enable_subagent"] is True
        assert len(parsed["selected_subagents"]) == 2

    def test_parse_rejects_inconsistent_single_subagent(self):
        raw = json.dumps(_decision("SINGLE_SUBAGENT", subagents=[_subagent(), _subagent()]))
        assert _parse_orchestration_decision(raw) is None

    def test_parse_rejects_multi_with_one(self):
        raw = json.dumps(_decision("MULTI_SUBAGENT", subagents=[_subagent()]))
        assert _parse_orchestration_decision(raw) is None

    def test_parse_rejects_unknown_mode(self):
        raw = json.dumps({"decision": {"mode": "BOGUS"}, "selected_subagents": []})
        assert _parse_orchestration_decision(raw) is None

    def test_parse_rejects_ask_user_without_questions(self):
        raw = json.dumps(_decision("ASK_USER", questions=[]))
        assert _parse_orchestration_decision(raw) is None

    def test_parse_rejects_refuse_without_message(self):
        raw = json.dumps(_decision("REFUSE", refusal=""))
        assert _parse_orchestration_decision(raw) is None

    def test_parse_handles_fenced_json(self):
        raw = "```json\n" + json.dumps(_decision("MAIN_AGENT")) + "\n```"
        parsed = _parse_orchestration_decision(raw)
        assert parsed is not None
        assert parsed["decision"]["mode"] == "MAIN_AGENT"

    def test_fallback_decision_conservative(self):
        with_plan = _fallback_decision([{"server": "db"}])
        assert with_plan["decision"]["mode"] == "TOOL_ONLY"
        assert with_plan["_fallback"] is True
        no_plan = _fallback_decision([])
        assert no_plan["decision"]["mode"] == "MAIN_AGENT"

    async def test_decompose_fills_prompt_and_parses(self, monkeypatch):
        router = LMRouter()

        async def fake_route(*, task: str, prompt: str) -> str:
            assert task == "decompose"
            assert "{{CURRENT_TIME}}" not in prompt
            assert "{{USER_INPUT}}" not in prompt
            assert "{{AVAILABLE_SUBAGENTS}}" not in prompt
            assert "{{AVAILABLE_TOOLS}}" not in prompt
            return json.dumps(
                _decision("TOOL_ONLY"),
                ensure_ascii=False,
            )

        monkeypatch.setattr(router, "route", fake_route)
        decision = await router.decompose(
            user_prompt="查一下天气",
            plan=[],
            history=[],
            available_subagents=[{"name": "x"}],
            available_tools=[{"name": "weather"}],
        )
        assert decision["decision"]["mode"] == "TOOL_ONLY"

    async def test_decompose_mock_mode_fallback(self, monkeypatch):
        monkeypatch.setenv("EAIDE_LLM_BACKEND", "mock")
        router = LMRouter()
        decision = await router.decompose(user_prompt="x", plan=[], history=[])
        assert decision["_fallback"] is True

    async def test_decompose_unparseable_falls_back(self, monkeypatch):
        router = LMRouter()

        async def fake_route(*, task: str, prompt: str) -> str:
            return "not json at all"

        monkeypatch.setattr(router, "route", fake_route)
        decision = await router.decompose(user_prompt="x", plan=[{"server": "db"}], history=[])
        assert decision["_fallback"] is True
        assert decision["decision"]["mode"] == "TOOL_ONLY"


# ---- context_strategy 执行模板 ----------------------------------------------

class TestExecutionTemplate:
    async def test_build_context_renders_execution_template(self):
        spec = SubAgentSpec(
            spec_version=1,
            sub_agent_id="run-sub1",
            parent_run_id="run",
            parent_sub_agent_id=None,
            depth=1,
            task_type="summarise",
            task_description="调研主流框架",
            input_payload={
                "execution_template_fields": {
                    "name": "research_agent",
                    "role": "调研",
                    "user_goal": "技术选型",
                    "task": "调研主流框架",
                    "inputs": {"context": "可靠来源"},
                    "allowed_tools": ["search"],
                    "expected_output": "报告",
                    "stop_condition": "信息足够",
                    "safety_policy": {"read_only": True},
                },
            },
            context_policy=ContextPolicy(strategy="passthrough", required_fields=[], shared_keys=[], max_summary_tokens=500),
            model_policy=ModelPolicy(role="execution", task_type="summarise", carries_sensitive_payload=False, preferred_backend=None),
            requires_write=False,
        )
        composed = build_context(spec)
        assert "你是由主智能体派发的子智能体" in composed.prompt
        assert "调研主流框架" in composed.prompt
        assert "可靠来源" in composed.prompt
        assert '"read_only": true' in composed.prompt


# ---- 图级端到端 -------------------------------------------------------------

class TestGraphEndToEnd:
    async def test_multi_subagent_auto_runs_to_answer(self):
        reset_orchestrator(router=None)  # router=None → orchestrator 走 mock 文本
        llm = _DeciderLLM(
            _decision(
                "MULTI_SUBAGENT",
                subagents=[_subagent("analysis_agent"), _subagent("research_agent")],
            ),
            plan_steps=[],
        )
        graph = compile_graph(Runtime(llm=llm, mcp=None))
        st = _state("帮我对比三个方案的性能与成本")
        result = await graph.ainvoke(st)
        assert result["multi_agent"] is True
        assert len(result["sub_agent_reports"]) == 2
        assert result["final_answer"] == "综合后的最终答案。"

    async def test_tool_only_keeps_single_agent_tool_path(self):
        reset_orchestrator(router=None)
        llm = _DeciderLLM(
            _decision("TOOL_ONLY"),
            plan_steps=[{
                "server": "db",
                "name": "db.query",
                "args": {"sql": "SELECT 1"},
                "risk_level": "read",
                "rationale": "test",
            }],
        )

        class _MCP:
            async def list_tools(self):
                return [{
                    "server": "db",
                    "name": "db.query",
                    "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}},
                }]

            async def invoke(self, call, *, timeout_sec, row_limit):
                return {"ok": True, "columns": ["n"], "rows": [[1]], "truncated": False, "rows_returned": 1}

        graph = compile_graph(Runtime(llm=llm, mcp=_MCP()))
        st = _state("查询订单数量")
        result = await graph.ainvoke(st)
        assert result["multi_agent"] is False
        # Phase 18：开发模式下 work 任务命中关键词 → 路由偏离，回复前置声明
        assert result["routing"] == "work"
        assert result["routing_overridden"] is True
        assert result["final_answer"].endswith("综合后的最终答案。")
        assert result["final_answer"].startswith("> ")
