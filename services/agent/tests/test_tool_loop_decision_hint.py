"""编排决策交接 + think 剥离测试（2026-08-17，BUGFIX #108）。

背景：用户在确认卡点「确认执行」后，新一轮 user_prompt 只剩一句确认文本，
工具循环模型重建不出上一轮谈好的参数 → 直接 FINAL_ANSWER 放弃，且推理模型
的 <think> 内心独白原样透传给用户。

覆盖：
    - _decision_hint：TOOL_ONLY 决策压成交接文本（含确认方案与建议工具调用）
    - DynamicToolLoop：decision_hint 随 orchestrate_tools 传入
    - FINAL_ANSWER / ASK_USER 文本剥离 <think> 块（提示词协议 + native 两条路径）
"""

from __future__ import annotations

from typing import Any

from agent.graph.state import empty_state
from agent.tools.loop import DynamicToolLoop, _decision_hint

# ---- 伪对象（与 test_tool_loop.py 同款风格）---------------------------------


class _ScriptedLoopLLM:
    def __init__(self, actions: list) -> None:
        self._actions = list(actions)
        self.calls: list[dict] = []

    async def orchestrate_tools(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        if not self._actions:
            raise AssertionError("script exhausted")
        return self._actions.pop(0)


class _FakeCatalog:
    async def summaries(self) -> list[dict]:
        return [
            {
                "name": "model_config_upsert",
                "description": "d",
                "category": "builtin",
                "keywords": [],
            }
        ]

    async def definitions(self, names: list[str] | None = None) -> list[dict]:
        return []

    async def execute(self, name: str, args: dict, state: dict) -> dict:
        return {"name": name, "ok": True, "result": "ok"}


class _NativeBackend:
    """原生 function calling 后端替身：按剧本返回响应。"""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.seen_messages: list[list[dict]] = []

    async def chat_with_tools(self, messages: list[dict], defs: list[dict]) -> dict:
        self.seen_messages.append([dict(m) for m in messages])
        if not self._responses:
            raise AssertionError("script exhausted")
        return self._responses.pop(0)


class _NativeLLM:
    def __init__(self, backend: _NativeBackend) -> None:
        self._backend = backend

    async def resolve_native_backend(self) -> tuple[str, Any]:
        return ("cloud", self._backend)


def _action(kind: str, **extra: Any) -> dict:
    base: dict[str, Any] = {
        "action": kind,
        "reason": "test",
        "confidence": 0.9,
        "selected_tool_names": [],
        "desired_capabilities": [],
        "missing_capability": "",
        "tool_calls": [],
        "final_answer": "",
        "ask_user_message": "",
        "need_full_toolset": False,
    }
    base.update(extra)
    return base


def _loop_state(**patch: Any) -> dict:
    st = empty_state("确认执行")
    st.update(patch)
    return st


def _confirm_decision() -> dict:
    """截图同款：TOOL_ONLY + 已确认的参数方案 + 建议工具调用。"""
    return {
        "decision": {
            "mode": "TOOL_ONLY",
            "reason": "两个必填槽位齐全，按模型接入 SOP 执行",
            "user_confirmation_required": False,
            "confirmation_message": "将按以下参数接入 DeepSeek-RD-Llama-70B-Int8：endpoint=http://172.1.0.134:8000",
            "clarifying_questions": [],
        },
        "tool_calls": [
            {
                "tool": "model_config_upsert",
                "purpose": "写入接入配置",
                "inputs": {"model_name": "DeepSeek-RD-Llama-70B-Int8"},
            }
        ],
    }


# ---- _decision_hint ---------------------------------------------------------


class TestDecisionHint:
    def test_tool_only_with_confirmation_and_calls(self):
        hint = _decision_hint(_confirm_decision())
        assert "model_config_upsert" in hint
        assert "DeepSeek-RD-Llama-70B-Int8" in hint
        assert "已向用户出示并获确认的参数方案" in hint

    def test_non_tool_only_returns_empty(self):
        d = _confirm_decision()
        d["decision"]["mode"] = "MAIN_AGENT"
        assert _decision_hint(d) == ""

    def test_invalid_input_returns_empty(self):
        assert _decision_hint(None) == ""
        assert _decision_hint({}) == ""
        assert _decision_hint({"decision": "not-a-dict"}) == ""

    def test_ask_user_returns_empty(self):
        assert (
            _decision_hint({"decision": {"mode": "ASK_USER", "clarifying_questions": ["x？"]}})
            == ""
        )


# ---- 提示词协议路径 -----------------------------------------------------------


class TestPromptProtocol:
    async def test_decision_hint_passed_to_orchestrator(self):
        llm = _ScriptedLoopLLM([_action("FINAL_ANSWER", final_answer="已完成。")])
        loop = DynamicToolLoop(llm, _FakeCatalog())  # type: ignore[arg-type]
        st = _loop_state(decompose_decision=_confirm_decision())
        out = await loop.run(st)
        assert out["final_answer"] == "已完成。"
        assert llm.calls, "orchestrate_tools 未被调用"
        hint = str(llm.calls[0].get("decision_hint") or "")
        assert "model_config_upsert" in hint
        assert "确认" in hint

    async def test_no_decision_hint_without_decompose(self):
        llm = _ScriptedLoopLLM([_action("FINAL_ANSWER", final_answer="好。")])
        loop = DynamicToolLoop(llm, _FakeCatalog())  # type: ignore[arg-type]
        out = await loop.run(_loop_state())
        assert out["final_answer"] == "好。"
        assert llm.calls[0].get("decision_hint") == ""

    async def test_final_answer_think_stripped(self):
        leaky = "<think>The user wants me to proceed, but I don't have context…</think>抱歉，我无法继续。"
        llm = _ScriptedLoopLLM([_action("FINAL_ANSWER", final_answer=leaky)])
        loop = DynamicToolLoop(llm, _FakeCatalog())  # type: ignore[arg-type]
        out = await loop.run(_loop_state(decompose_decision=_confirm_decision()))
        assert "<think>" not in out["final_answer"]
        assert "抱歉，我无法继续。" in out["final_answer"]

    async def test_ask_user_think_stripped(self):
        leaky = "<think>thinking…</think>请提供目标地址。"
        llm = _ScriptedLoopLLM([_action("ASK_USER", ask_user_message=leaky)])
        loop = DynamicToolLoop(llm, _FakeCatalog())  # type: ignore[arg-type]
        out = await loop.run(_loop_state())
        assert "<think>" not in out["final_answer"]
        assert "请提供目标地址。" in out["final_answer"]

    async def test_pure_think_final_answer_falls_back(self):
        """整段只有 think（剥完为空）→ 走既有兜底文案，不吐空消息。"""
        llm = _ScriptedLoopLLM([_action("FINAL_ANSWER", final_answer="<think>…</think>")])
        loop = DynamicToolLoop(llm, _FakeCatalog())  # type: ignore[arg-type]
        out = await loop.run(_loop_state())
        assert "<think>" not in out["final_answer"]
        assert out["final_answer"].strip() != ""


# ---- native 路径 --------------------------------------------------------------


class TestNativePath:
    async def test_hint_injected_into_first_user_message_and_think_stripped(self):
        backend = _NativeBackend(
            [{"content": "<think>reconstructing…</think>接入完成。", "tool_calls": []}]
        )
        loop = DynamicToolLoop(_NativeLLM(backend), _FakeCatalog())  # type: ignore[arg-type]
        st = _loop_state(
            tool_calling_mode="native",
            decompose_decision=_confirm_decision(),
        )
        out = await loop.run(st)
        assert out["final_answer"] == "接入完成。"
        assert "<think>" not in out["final_answer"]
        # 末条 user 消息带交接提示（前面是历史消息）
        first_user = next(m for m in reversed(backend.seen_messages[0]) if m.get("role") == "user")
        assert "编排决策交接" in first_user["content"]
        assert "model_config_upsert" in first_user["content"]

    async def test_no_hint_no_prefix(self):
        backend = _NativeBackend([{"content": "完成。", "tool_calls": []}])
        loop = DynamicToolLoop(_NativeLLM(backend), _FakeCatalog())  # type: ignore[arg-type]
        out = await loop.run(_loop_state(tool_calling_mode="native"))
        assert out["final_answer"] == "完成。"
        first_user = next(m for m in reversed(backend.seen_messages[0]) if m.get("role") == "user")
        assert "编排决策交接" not in first_user["content"]
