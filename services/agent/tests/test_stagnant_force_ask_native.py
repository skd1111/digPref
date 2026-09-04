"""BUGFIX #182 —— 停滞熔断达阈先反向追问用户缺什么，而不是直接停（native 模式）。

截图事故（2026-08-31）：PPT 任务连续 3 轮零成功 → 循环直接甩模板句
「请补充关键信息或换一种表述」终止；用户看不出缺什么，任务卡死。
修复：达阈先插入一次强制追问轮（追加系统指令进 messages，逼模型调
ask_user 列出缺的东西）；追问后仍无进展才终止，且终答附最近失败原因。

提示词协议路径的同类测试在 test_tool_loop.py（TestStagnantForceAsk）。
"""

from __future__ import annotations

import asyncio

from agent.tools.loop import DynamicToolLoop


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _StubBackend:
    """OpenAI 兼容后端桩：按脚本返回响应，并快照每次请求的 messages。"""

    def __init__(self, scripted: list[dict]):
        self._scripted = list(scripted)
        self.request_messages: list[list[dict]] = []

    async def chat_with_tools(self, messages, tools, **kwargs):
        self.request_messages.append([dict(m) for m in messages])
        return self._scripted.pop(0)


class _StubLLM:
    def __init__(self, backend):
        self._backend = backend

    async def resolve_native_backend(self):
        return ("cloud", self._backend)


class _StubCatalog:
    def __init__(self, results: list[dict]):
        self._results = list(results)
        self.executed: list[tuple[str, dict]] = []

    async def definitions(self, names=None):
        return [
            {
                "name": "shell",
                "description": "执行命令",
                "parameters": {"type": "object", "properties": {}},
            }
        ]

    async def execute(self, name, args, state):
        self.executed.append((name, dict(args)))
        return self._results.pop(0)


def _base_state(**extra) -> dict:
    st = {
        "tool_calling_mode": "native",
        "user_prompt": "做一个介绍联想公司的 ppt",
        "messages": [],
        "tool_results": [],
        "tool_turn_count": 0,
    }
    st.update(extra)
    return st


def _shell_call(i: int) -> dict:
    return {
        "content": None,
        "tool_calls": [{"id": f"c{i}", "name": "shell", "arguments": {"command": f"cmd{i}"}}],
    }


_FAIL = {"name": "shell", "ok": False, "error": "boom"}


def test_native_stagnant_force_ask_then_model_asks_user():
    """连续 3 轮失败 → 不直接停；注入强制追问指令后模型调 ask_user，
    用户收到具体追问（缺哪些东西）。"""
    backend = _StubBackend(
        [_shell_call(i) for i in range(3)]
        + [
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "ask1",
                        "name": "ask_user",
                        "arguments": {"message": "缺少素材：请提供联想公司介绍的资料文件路径。"},
                    }
                ],
            }
        ]
    )
    catalog = _StubCatalog([dict(_FAIL) for _ in range(3)])
    loop = DynamicToolLoop(_StubLLM(backend), catalog)
    out = _run(loop.run(_base_state()))

    assert out["final_answer"] == "缺少素材：请提供联想公司介绍的资料文件路径。"
    assert out["tool_loop_active"] is False
    # 第四次模型轮之前，强制追问指令已追加进 messages
    assert len(backend.request_messages) == 4
    last_req = backend.request_messages[-1]
    assert any("系统强制指令" in str(m.get("content") or "") for m in last_req)


def test_native_stagnant_terminates_after_force_ask_with_error_summary():
    """强制追问后仍失败 → 终止，终答附最近失败原因（不再只甩模板句）。"""
    backend = _StubBackend([_shell_call(i) for i in range(4)])
    catalog = _StubCatalog([dict(_FAIL) for _ in range(4)])
    loop = DynamicToolLoop(_StubLLM(backend), catalog)
    out = _run(loop.run(_base_state()))

    final = out["final_answer"]
    assert "均无有效结果" in final
    assert "boom" in final  # 具体失败原因可见
    assert out["tool_loop_active"] is False


def test_native_stagnant_asked_resets_on_success():
    """有进展即复位：追问轮后工具成功一次 → 计数与追问旗标清零，继续干活。"""
    backend = _StubBackend(
        [_shell_call(i) for i in range(4)] + [{"content": "完成。", "tool_calls": []}]
    )
    ok = {"name": "shell", "ok": True, "result": "ok"}
    catalog = _StubCatalog([dict(_FAIL) for _ in range(3)] + [dict(ok)])
    loop = DynamicToolLoop(_StubLLM(backend), catalog)
    out = _run(loop.run(_base_state()))

    assert out["final_answer"] == "完成。"
    assert len(catalog.executed) == 4  # 3 次失败 + 1 次成功，未被熔断提前终止
