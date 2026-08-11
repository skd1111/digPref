"""OpenAI 原生 function calling 工具循环测试（2026-08-07）。

覆盖：
    - native 循环：工具执行 → 最终回答
    - ask_user 伪工具 → 追问直出
    - use_more_tools → 全量加载后放行全部工具
    - HITL 暂停（awaiting_approval）与批准后恢复执行
    - 后端故障未执行过工具 → 返 None（回退提示词协议）
    - 未注册工具 → tool 错误消息，不执行
    - PrivateLLMClient.supports_tool_calling 不可达 → False
"""

from __future__ import annotations

from agent.tools.loop import DynamicToolLoop

# ---- 伪对象 ------------------------------------------------------------------


class _FakeBackend:
    """按脚本逐轮返回 chat_with_tools 响应。"""

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.requests: list[tuple[list, list]] = []

    async def chat_with_tools(self, messages, tools, **kw):
        self.requests.append((list(messages), list(tools)))
        if not self.script:
            return {"content": "脚本耗尽", "tool_calls": []}
        return self.script.pop(0)


class _FakeRouter:
    def __init__(self, backend) -> None:
        self._backend = backend

    async def resolve_native_backend(self):
        if self._backend is None:
            return None
        return ("cloud", self._backend)


class _ApprovingCatalog:
    """指定工具返回 awaiting_approval（模拟写操作 HITL 闸门）。"""

    def __init__(self, gate_names: set[str]) -> None:
        self._gate = gate_names
        self.executed: list[str] = []

    async def definitions(self, names=None):
        from agent.tools.catalog import ToolCatalog

        return await ToolCatalog(mcp=None).definitions(names)

    async def execute(self, name, args, state):
        # 与真实闸门同语义：写工具未批准时拦截，approve 后放行
        if name in self._gate and state.get("approval_decision") != "approve":
            return {
                "awaiting_approval": True,
                "pending_tool_call": {
                    "call_id": "call-w1",
                    "server": "builtin",
                    "name": name,
                    "args": args,
                },
            }
        self.executed.append(name)
        return {"id": f"r-{name}", "name": name, "ok": True, "result": {"value": 42}}


def _state(**over) -> dict:
    st = {
        "user_prompt": "今天几号",
        "messages": [],
        "tool_calling_mode": "native",
        "tool_results": [],
        "tool_turn_count": 0,
        "full_toolset_loaded": False,
        "native_turn_context": None,
        "dual_rules_addon": "",
    }
    st.update(over)
    return st


def _tool_call(call_id: str, name: str, args: dict | None = None) -> dict:
    return {"id": call_id, "name": name, "arguments": args or {}}


# ---- 主流程 ------------------------------------------------------------------


class TestNativeLoop:
    async def test_execute_tool_then_final_answer(self):
        from agent.tools.catalog import ToolCatalog

        backend = _FakeBackend(
            [
                {"content": None, "tool_calls": [_tool_call("c1", "datetime_now")]},
                {"content": "今天是 2026-08-07，星期五。", "tool_calls": []},
            ]
        )
        loop = DynamicToolLoop(_FakeRouter(backend), ToolCatalog(mcp=None))
        out = await loop.run(_state())
        assert out["tool_loop_active"] is False
        assert out["final_answer"] == "今天是 2026-08-07，星期五。"
        assert any(r["name"] == "datetime_now" and r["ok"] for r in out["tool_results"])
        # 第二轮请求带上了 tool 结果消息
        second_msgs = backend.requests[1][0]
        assert any(m.get("role") == "tool" for m in second_msgs)

    async def test_ask_user_pseudo_tool(self):
        from agent.tools.catalog import ToolCatalog

        backend = _FakeBackend(
            [
                {
                    "content": None,
                    "tool_calls": [
                        _tool_call(
                            "c1", "ask_user", {"message": "请问您从哪个城市出发？例如：北京。"}
                        ),
                    ],
                },
            ]
        )
        loop = DynamicToolLoop(_FakeRouter(backend), ToolCatalog(mcp=None))
        out = await loop.run(_state(user_prompt="帮我订票"))
        assert out["final_answer"] == "请问您从哪个城市出发？例如：北京。"
        assert out["tool_loop_active"] is False

    async def test_use_more_tools_unlocks_full_set(self):
        from agent.tools.catalog import ToolCatalog

        backend = _FakeBackend(
            [
                {"content": None, "tool_calls": [_tool_call("c1", "use_more_tools")]},
                {
                    "content": None,
                    "tool_calls": [_tool_call("c2", "calculator", {"expression": "1+2"})],
                },
                {"content": "结果是 3。", "tool_calls": []},
            ]
        )
        loop = DynamicToolLoop(_FakeRouter(backend), ToolCatalog(mcp=None))
        out = await loop.run(_state(user_prompt="算 1+2"))
        assert out["full_toolset_loaded"] is True
        assert out["final_answer"] == "结果是 3。"
        assert any(r["name"] == "calculator" for r in out["tool_results"])

    async def test_unregistered_tool_gets_error_message(self):
        from agent.tools.catalog import ToolCatalog

        backend = _FakeBackend(
            [
                {"content": None, "tool_calls": [_tool_call("c1", "no_such_tool")]},
                {"content": "抱歉做不到。", "tool_calls": []},
            ]
        )
        loop = DynamicToolLoop(_FakeRouter(backend), ToolCatalog(mcp=None))
        out = await loop.run(_state())
        assert out["final_answer"] == "抱歉做不到。"
        # 未注册工具没有产生执行结果
        assert out["tool_results"] == []

    async def test_backend_unavailable_returns_none(self):
        """后端不可用 → 返 None，调用方回退提示词协议。"""
        from agent.tools.catalog import ToolCatalog

        class _BrokenBackend:
            async def chat_with_tools(self, messages, tools, **kw):
                raise ConnectionError("boom")

        loop = DynamicToolLoop(_FakeRouter(_BrokenBackend()), ToolCatalog(mcp=None))
        out = await loop._run_native(_state())
        assert out is None

    async def test_no_native_backend_returns_none(self):
        from agent.tools.catalog import ToolCatalog

        loop = DynamicToolLoop(_FakeRouter(None), ToolCatalog(mcp=None))
        out = await loop._run_native(_state())
        assert out is None


class TestNativeHitl:
    async def test_write_call_pauses_for_approval_and_resumes(self):
        catalog = _ApprovingCatalog(gate_names={"write_file"})
        backend = _FakeBackend(
            [
                # 首轮：先加载全量工具（write_file 不在首轮轻量集）
                {"content": None, "tool_calls": [_tool_call("c0", "use_more_tools")]},
                # 写文件（将触发审批）+ 之后还有一个调用
                {
                    "content": None,
                    "tool_calls": [
                        _tool_call("c1", "write_file", {"path": "/tmp/a.txt", "content": "x"}),
                        _tool_call("c2", "datetime_now"),
                    ],
                },
                # 审批通过恢复后：继续执行剩余调用 → 模型收尾
                {"content": "文件已写入。", "tool_calls": []},
            ]
        )
        loop = DynamicToolLoop(_FakeRouter(backend), catalog)
        out = await loop.run(_state(user_prompt="写个文件"))
        assert out["awaiting_approval"] is True
        assert out["pending_tool_call"]["name"] == "write_file"
        # 剩余调用存入上下文，审批后继续
        ctx = out["native_turn_context"]
        assert ctx["pending_calls"][0]["name"] == "datetime_now"

        # ---- 审批通过 → 恢复执行 ----
        resumed_state = _state(
            user_prompt="写个文件",
            approval_decision="approve",
            awaiting_approval=False,
            pending_tool_call={
                "call_id": "call-w1",
                "name": "write_file",
                "args": {"path": "/tmp/a.txt"},
            },
            native_turn_context=out["native_turn_context"],
            tool_results=out.get("tool_results") or [],
            tool_turn_count=out.get("tool_turn_count") or 0,
            full_toolset_loaded=out.get("full_toolset_loaded") or False,
        )
        out2 = await loop.run(resumed_state)
        assert out2["awaiting_approval"] is False
        assert out2["final_answer"] == "文件已写入。"
        # 被审批的调用 + 剩余调用都执行了
        assert "write_file" in catalog.executed
        assert "datetime_now" in catalog.executed

    async def test_reject_records_user_rejected(self):
        catalog = _ApprovingCatalog(gate_names={"write_file"})
        backend = _FakeBackend(
            [
                {"content": None, "tool_calls": [_tool_call("c0", "use_more_tools")]},
                {
                    "content": None,
                    "tool_calls": [
                        _tool_call("c1", "write_file", {"path": "/tmp/a.txt"}),
                    ],
                },
                {"content": "好的，已取消。", "tool_calls": []},
            ]
        )
        loop = DynamicToolLoop(_FakeRouter(backend), catalog)
        out = await loop.run(_state(user_prompt="写个文件"))
        assert out["awaiting_approval"] is True

        resumed_state = _state(
            user_prompt="写个文件",
            approval_decision="reject",
            pending_tool_call={"call_id": "call-w1", "name": "write_file", "args": {}},
            native_turn_context=out["native_turn_context"],
            tool_results=out.get("tool_results") or [],
            tool_turn_count=out.get("tool_turn_count") or 0,
            full_toolset_loaded=out.get("full_toolset_loaded") or False,
        )
        out2 = await loop.run(resumed_state)
        assert any(r.get("error") == "user_rejected" for r in out2["tool_results"])
        assert catalog.executed == []  # 拒绝后未执行任何工具


class TestProbe:
    async def test_supports_tool_calling_unreachable_returns_false(self):
        from agent.llm.private_llm import PrivateLLMClient

        client = PrivateLLMClient(
            base_url="http://127.0.0.1:9/v1",
            api_key="x",
            model="m",
        )
        assert await client.supports_tool_calling() is False
