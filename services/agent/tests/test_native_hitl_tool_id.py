"""BUGFIX #139 —— HITL 审批恢复后，tool 消息必须复用模型下发的 tool_call id。

2026-08-25 实测（#137 让 400 错误正文可见后定位）：写操作被拦截进审批，恢复执行
后发给 MiniMax 的消息里，tool 结果消息的 tool_call_id 是 dispatcher 现生成的
uuid，与前面 assistant 消息 tool_calls[].id（模型下发的 id）对不上 → 云端严格
校验直接 400「tool result's tool id(...) not found」→ 整轮工具循环硬停。

本文件用桩后端/桩目录模拟「模型下发 tool_call → 写操作拦截暂停 → 批准恢复」
全链，断言：
  1. 暂停时把模型 tool_call id 暂存进 pending（model_call_id）；
  2. 恢复执行后追加的 tool 消息用模型 id（与 assistant tool_calls 配对）；
  3. 拒绝分支同理（拒绝回执消息也用模型 id）。
"""

from __future__ import annotations

import asyncio

from agent.tools.loop import DynamicToolLoop, _pending_model_call_id

MODEL_CALL_ID = "toolu_ABC_from_model"
DISPATCHER_UUID = "5aa5b7522e844dc39e3558ba6d3ed2e8"


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
        "user_prompt": "看下当前目录",
        "messages": [],
        "tool_results": [],
        "tool_turn_count": 0,
    }
    st.update(extra)
    return st


# ---- 单元：id 解析优先级 ----------------------------------------------------


def test_pending_model_call_id_prefers_model_id():
    pending = {"call_id": DISPATCHER_UUID, "model_call_id": MODEL_CALL_ID}
    assert _pending_model_call_id(pending) == MODEL_CALL_ID


def test_pending_model_call_id_falls_back_to_dispatcher():
    """存量/异常态无 model_call_id → 回退 dispatcher call_id，再不济用占位。"""
    assert _pending_model_call_id({"call_id": DISPATCHER_UUID}) == DISPATCHER_UUID
    assert _pending_model_call_id({"call_id": ""}) == "pending"
    assert _pending_model_call_id(None) == "pending"


# ---- 全链：暂停时暂存模型 id --------------------------------------------------


def test_pause_stashes_model_tool_call_id():
    backend = _StubBackend(
        [
            {
                "content": None,
                "tool_calls": [
                    {"id": MODEL_CALL_ID, "name": "shell", "arguments": {"command": "pwd"}}
                ],
            }
        ]
    )
    catalog = _StubCatalog(
        [
            {
                "awaiting_approval": True,
                "pending_tool_call": {
                    "server": "builtin",
                    "name": "shell",
                    "args": {"command": "pwd"},
                    "call_id": DISPATCHER_UUID,
                },
            }
        ]
    )
    loop = DynamicToolLoop(_StubLLM(backend), catalog)
    out = _run(loop.run(_base_state()))

    assert out["awaiting_approval"] is True
    pending = out["pending_tool_call"]
    # 模型 id 必须暂存，恢复后才能与 assistant tool_calls 配对
    assert pending["model_call_id"] == MODEL_CALL_ID
    # dispatcher 内部 id 原样保留（审计/事件链不受影响）
    assert pending["call_id"] == DISPATCHER_UUID


# ---- 全链：批准后恢复，tool 消息用模型 id（不再产生孤儿） ----------------------


def test_resume_appends_tool_message_with_model_id():
    backend = _StubBackend(
        [
            # 恢复后：先执行重放调用（不发请求），再把结果交模型 → 终答
            {"content": "已完成。", "tool_calls": []},
        ]
    )
    catalog = _StubCatalog([{"ok": True, "result": "done"}])
    loop = DynamicToolLoop(_StubLLM(backend), catalog)

    st = _base_state(
        awaiting_approval=True,
        approval_decision="approve",
        pending_tool_call={
            "server": "builtin",
            "name": "shell",
            "args": {"command": "pwd"},
            "call_id": DISPATCHER_UUID,
            "model_call_id": MODEL_CALL_ID,
        },
        native_turn_context={
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "看下当前目录"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": MODEL_CALL_ID,
                            "type": "function",
                            "function": {"name": "shell", "arguments": "{}"},
                        }
                    ],
                },
            ],
            "pending_calls": [],
        },
        tool_turn_count=1,
    )
    out = _run(loop.run(st))
    assert out["final_answer"] == "已完成。"
    assert catalog.executed == [("shell", {"command": "pwd"})]

    # 关键断言：恢复后发给后端的 messages 里，tool 结果消息的 tool_call_id
    # 必须等于 assistant tool_calls 里的模型 id（配对成立，云端不再 400）
    sent = backend.request_messages[0]
    tool_msgs = [m for m in sent if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == MODEL_CALL_ID
    assert tool_msgs[0]["tool_call_id"] != DISPATCHER_UUID


# ---- 全链：拒绝分支回执也用模型 id ---------------------------------------------


def test_reject_tool_message_uses_model_id():
    backend = _StubBackend([{"content": "好的，已取消。", "tool_calls": []}])
    catalog = _StubCatalog([])  # 拒绝不执行任何调用
    loop = DynamicToolLoop(_StubLLM(backend), catalog)

    st = _base_state(
        awaiting_approval=True,
        approval_decision="reject",
        pending_tool_call={
            "server": "builtin",
            "name": "shell",
            "args": {"command": "pwd"},
            "call_id": DISPATCHER_UUID,
            "model_call_id": MODEL_CALL_ID,
        },
        native_turn_context={
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "看下当前目录"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": MODEL_CALL_ID,
                            "type": "function",
                            "function": {"name": "shell", "arguments": "{}"},
                        }
                    ],
                },
            ],
            "pending_calls": [],
        },
        tool_turn_count=1,
    )
    out = _run(loop.run(st))
    assert out["final_answer"] == "好的，已取消。"

    sent = backend.request_messages[0]
    tool_msgs = [m for m in sent if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "拒绝" in str(tool_msgs[0]["content"])
    assert tool_msgs[0]["tool_call_id"] == MODEL_CALL_ID
