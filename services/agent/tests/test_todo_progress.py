"""任务进度待办列表（2026-08-25）—— update_todos 伪工具回归测试。

用户需求：长链任务要让用户实时看到进度。实现：模型调 update_todos（伪工具，
不执行任何操作）→ 循环把全量待办经 trace 通道（todos 字段）下发 → 前端按
runId 固定消息 id 原地更新进度卡片。断言：
  1. 脏数据清洗（非列表/缺 content/非法 status/超长超量）；
  2. native 循环拦截 update_todos：不真执行、回执 tool 消息、trace 带 todos；
  3. 提示词与工具定义注册（模型能看到该工具）。
"""

from __future__ import annotations

import asyncio

from agent.tools.loop import (
    _NATIVE_SYSTEM_PROMPT,
    _UPDATE_TODOS_TOOL,
    DynamicToolLoop,
    _normalize_todos,
)


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ---- 单元：脏数据清洗 --------------------------------------------------------


def test_normalize_todos_cleans_dirty_input():
    raw = [
        {"content": "  收集资料  ", "status": "done"},
        {"content": "", "status": "pending"},  # 空内容 → 丢弃
        {"status": "done"},  # 缺 content → 丢弃
        {"content": "生成大纲", "status": "weird"},  # 非法状态 → 归一 pending
        "not a dict",  # 非对象 → 丢弃
        {"content": "导出文件", "status": "in_progress"},
    ]
    out = _normalize_todos(raw)
    assert [t["content"] for t in out] == ["收集资料", "生成大纲", "导出文件"]
    assert [t["status"] for t in out] == ["done", "pending", "in_progress"]


def test_normalize_todos_bounds():
    assert _normalize_todos(None) == []
    assert _normalize_todos("不是列表") == []
    long_content = "字" * 500
    out = _normalize_todos([{"content": long_content, "status": "pending"}])
    assert len(out[0]["content"]) == 200  # 截断
    many = [{"content": f"t{i}", "status": "pending"} for i in range(50)]
    assert len(_normalize_todos(many)) == 30  # 限量


# ---- 全链：native 循环拦截伪工具 ---------------------------------------------


class _StubBackend:
    def __init__(self, scripted: list[dict]):
        self._scripted = list(scripted)
        self.request_tools: list[list[dict]] = []

    async def chat_with_tools(self, messages, tools, **kwargs):
        self.request_tools.append(list(tools))
        return self._scripted.pop(0)


class _StubLLM:
    def __init__(self, backend):
        self._backend = backend

    async def resolve_native_backend(self):
        return ("cloud", self._backend)


class _StubCatalog:
    def __init__(self):
        self.executed: list[tuple[str, dict]] = []

    async def definitions(self, names=None):
        return []

    async def execute(self, name, args, state):
        self.executed.append((name, dict(args)))
        return {"name": name, "ok": True, "result": "ok"}


def _base_state() -> dict:
    return {
        "tool_calling_mode": "native",
        "user_prompt": "做个 PPT",
        "messages": [],
        "tool_results": [],
        "tool_turn_count": 0,
    }


TODOS_V1 = [
    {"content": "收集资料", "status": "in_progress"},
    {"content": "生成大纲", "status": "pending"},
    {"content": "导出文件", "status": "pending"},
]
TODOS_V2 = [
    {"content": "收集资料", "status": "done"},
    {"content": "生成大纲", "status": "done"},
    {"content": "导出文件", "status": "done"},
]


def test_update_todos_emits_trace_and_skips_execution():
    backend = _StubBackend(
        [
            {
                "content": None,
                "tool_calls": [
                    {"id": "c1", "name": "update_todos", "arguments": {"items": TODOS_V1}}
                ],
            },
            {
                "content": None,
                "tool_calls": [
                    {"id": "c2", "name": "update_todos", "arguments": {"items": TODOS_V2}}
                ],
            },
            {"content": "PPT 已完成", "tool_calls": []},
        ]
    )
    catalog = _StubCatalog()
    loop = DynamicToolLoop(_StubLLM(backend), catalog)
    out = _run(loop.run(_base_state()))

    # 伪工具不真执行任何操作
    assert catalog.executed == []
    assert out["final_answer"] == "PPT 已完成"
    # trace 里有两条 todo 条目，携带全量待办（前端据此原地更新卡片）
    todo_traces = [t for t in out["trace"] if t.get("node") == "todo"]
    assert len(todo_traces) == 2
    assert todo_traces[0]["todos"] == TODOS_V1
    assert todo_traces[1]["todos"] == TODOS_V2
    assert "完成 3" in todo_traces[1]["summary"]


def test_update_todos_tool_registered_and_prompted():
    """模型必须能看到该工具：工具定义注册 + 系统提示词纪律。"""
    assert _UPDATE_TODOS_TOOL["function"]["name"] == "update_todos"
    assert "update_todos" in _NATIVE_SYSTEM_PROMPT
    backend = _StubBackend([{"content": "done", "tool_calls": []}])
    loop = DynamicToolLoop(_StubLLM(backend), _StubCatalog())
    _run(loop.run(_base_state()))
    names = {t["function"]["name"] for t in backend.request_tools[0]}
    assert "update_todos" in names
    assert "ask_user" in names
