"""根治 BUGFIX #164 的回归测试：tool_call / tool_result 的 call_id 配对。

## 背景

前端「工具执行」卡片会永久转圈：``tool_call`` 与 ``tool_result`` 两条 SSE 事件
此前各自 ``str(uuid.uuid4())``，**没有任何共享标识**；前端只能退而求其次用
``evt.result.name`` 配对，而 ``ToolResult.to_dict()`` 从不填 ``name``（TS 侧却
声明必填 —— 协议漂移），于是配对恒失败，running 卡片没人翻牌。

修复后的契约（本文件负责钉死）：

1. ``call["call_id"]`` 由 tool_runner / builtin dispatcher 写入并**回写进 call 字典**
2. SSE 两条事件都带 ``callId``，取值同源
3. ``tool_result`` 事件的 ``result`` 一定带 ``name``（按 call 回填）
4. 重试 / HITL 恢复重跑同一步 → 同一 call_id（前端原地更新，不堆卡）
"""

from __future__ import annotations

from agent.builtin.models import ToolResult
from agent.graph.stream import _convert_chunk


def _updates(delta: dict, node: str = "tool_runner") -> list[dict]:
    """按 updates 模式转换一个节点增量，返回事件列表。"""
    return _convert_chunk("updates", {node: delta}, run_id="run-1", emitted_approvals=set())


def _by_event(events: list[dict], name: str) -> dict | None:
    for e in events:
        if e.get("event") == name:
            return e
    return None


# ---- ToolResult 序列化契约 ---------------------------------------------------


def test_tool_result_to_dict_carries_name_and_call_id() -> None:
    """TS 侧 ToolResult 声明 name —— Python 端必须真的发出来（协议漂移根治）。"""
    d = ToolResult(ok=True, content="hi", name="builtin_read_file", call_id="abc123").to_dict()
    assert d["name"] == "builtin_read_file"
    assert d["call_id"] == "abc123"


def test_tool_result_defaults_keep_keys_present() -> None:
    """字段缺省也要出现在 dict 里（前端按可选字段读，不能 KeyError）。"""
    d = ToolResult().to_dict()
    assert "name" in d and "call_id" in d
    assert d["name"] is None and d["call_id"] is None


# ---- SSE 事件配对字段 -------------------------------------------------------


def test_tool_call_event_exposes_call_id() -> None:
    events = _updates(
        {"pending_tool_call": {"server": "builtin", "name": "builtin_write_file", "call_id": "c-7"}}
    )
    evt = _by_event(events, "tool_call")
    assert evt is not None
    assert evt["data"]["callId"] == "c-7"


def test_tool_call_and_result_share_same_call_id() -> None:
    """这是修复的核心：同一个节点增量里两条事件必须同源取 call_id。"""
    delta = {
        "pending_tool_call": {"server": "builtin", "name": "builtin_read_file", "call_id": "c-9"},
        "tool_result": {"ok": True, "content": "data"},
    }
    events = _updates(delta)
    call_evt = _by_event(events, "tool_call")
    result_evt = _by_event(events, "tool_result")
    assert call_evt is not None and result_evt is not None
    assert call_evt["data"]["callId"] == result_evt["data"]["callId"] == "c-9"


def test_tool_result_name_backfilled_from_call() -> None:
    """MCP invoke() 与 ToolResult 都可能不带 name → stream 层按 call 补齐，
    让 callId 缺失时前端仍有 name 兜底可用。
    """
    events = _updates(
        {
            "pending_tool_call": {"server": "db", "name": "query_sql", "call_id": "c-1"},
            "tool_result": {"ok": True, "rows": []},
        }
    )
    assert _by_event(events, "tool_result")["data"]["result"]["name"] == "query_sql"


def test_tool_result_existing_name_not_overwritten() -> None:
    events = _updates(
        {
            "pending_tool_call": {"server": "db", "name": "call_side", "call_id": "c-2"},
            "tool_result": {"ok": True, "name": "result_side"},
        }
    )
    assert _by_event(events, "tool_result")["data"]["result"]["name"] == "result_side"


def test_missing_call_id_degrades_to_none_not_crash() -> None:
    """旧后端 / 异常路径不带 call_id 时不能炸，callId 为 None，前端走 name 兜底。"""
    events = _updates(
        {"pending_tool_call": {"server": "builtin", "name": "x"}, "tool_result": {"ok": True}}
    )
    assert _by_event(events, "tool_call")["data"]["callId"] is None
    assert _by_event(events, "tool_result")["data"]["callId"] is None


def test_failed_tool_result_still_paired() -> None:
    """失败结果同样要能翻牌成 err，否则失败的卡片继续转圈。"""
    events = _updates(
        {
            "pending_tool_call": {"server": "builtin", "name": "builtin_shell", "call_id": "c-5"},
            "tool_result": {"ok": False, "error": "denied"},
        }
    )
    data = _by_event(events, "tool_result")["data"]
    assert data["callId"] == "c-5"
    assert data["result"]["ok"] is False
