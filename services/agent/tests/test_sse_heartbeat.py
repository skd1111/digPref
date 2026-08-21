"""BUGFIX #118 回归：SSE 心跳保活 + CancelledError 终止信号兜底。

卡死链路（2026-08-17 实测）：内网模型挂死 → 流 60s 无字节 → Rust reqwest
read_timeout=60s 断连 → uvicorn 取消图任务 → CancelledError 穿透
`except Exception` → done/error 发不出 → 前端永久卡「思考中」。

覆盖：
  1. 长静默期间按间隔发 SSE 注释行心跳（事件不丢、顺序不乱）
  2. 图执行抛 CancelledError → 仍补发 error + done
  3. 图执行抛普通异常 → 行为不变（error + done）
  4. _sse_format：空 event 名输出注释行（":" 开头，客户端静默忽略）
"""

from __future__ import annotations

import asyncio

import agent.graph.stream as stream_mod
import pytest
from agent.api.chat import _sse_format
from agent.graph.stream import stream_graph_events

pytestmark = pytest.mark.usefixtures("_isolate")


class _CapturingGraph:
    """捕获 initial_state 的基础假图。"""

    def __init__(self) -> None:
        self.captured: dict | None = None

    def _capture(self, initial_state) -> None:
        self.captured = initial_state


class _SlowGraph(_CapturingGraph):
    """第一个 chunk 前有长静默（触发心跳），随后正常出 chunk。"""

    def __init__(self, delay: float) -> None:
        super().__init__()
        self._delay = delay

    async def astream(self, initial_state, cfg, stream_mode=None):
        self._capture(initial_state)
        await asyncio.sleep(self._delay)
        # values 模式 + 2 条 trace（初始 state 已含 1 条，增量下发第 2 条）
        yield ("values", {"trace": [{"node": "init"}, {"node": "intent", "status": "ok"}]})


class _CancelledGraph(_CapturingGraph):
    """出 chunk 后抛 CancelledError（模拟客户端断开 / 超时取消）。"""

    async def astream(self, initial_state, cfg, stream_mode=None):
        self._capture(initial_state)
        yield ("values", {"trace": [{"node": "init"}, {"node": "intent", "status": "ok"}]})
        await asyncio.sleep(0.2)
        raise asyncio.CancelledError()


class _BoomGraph(_CapturingGraph):
    """抛普通异常（回归保护：Exception 路径行为不变）。"""

    async def astream(self, initial_state, cfg, stream_mode=None):
        self._capture(initial_state)
        if False:  # pragma: no cover
            yield None
        raise RuntimeError("boom")


async def _collect(graph, *, monkeypatch, interval: float = 0.05) -> list[dict]:
    monkeypatch.setattr(stream_mod, "_HEARTBEAT_INTERVAL_SEC", interval)
    return [e async for e in stream_graph_events(graph, "run-hb", "ping")]


async def test_heartbeat_emitted_during_silence(monkeypatch):
    """静默 0.2s、心跳间隔 0.05s → 至少 1 条心跳，且业务事件与 done 不丢。"""
    events = await _collect(_SlowGraph(delay=0.2), monkeypatch=monkeypatch)

    heartbeats = [e for e in events if e.get("event") == ""]
    assert len(heartbeats) >= 1
    assert all(e["data"] == "heartbeat" for e in heartbeats)

    kinds = [e["event"] for e in events if e.get("event")]
    assert "trace" in kinds  # intent chunk 正常下发
    assert kinds[-1] == "done"  # done 兜底仍在最后


async def test_cancelled_error_yields_error_then_done(monkeypatch):
    """CancelledError（BaseException）必须被接住：补发 error，finally 补发 done。"""
    events = await _collect(_CancelledGraph(), monkeypatch=monkeypatch)

    named = [e for e in events if e.get("event")]
    assert named[-2]["event"] == "error"
    assert "取消" in named[-2]["data"]
    assert named[-1]["event"] == "done"


async def test_regular_exception_behavior_unchanged(monkeypatch):
    """普通异常路径回归：error（含异常信息）+ done。"""
    events = await _collect(_BoomGraph(), monkeypatch=monkeypatch)

    named = [e for e in events if e.get("event")]
    assert named[-2]["event"] == "error"
    assert "boom" in named[-2]["data"]
    assert named[-1]["event"] == "done"


def test_sse_format_empty_event_is_comment_line():
    """空 event 名 → SSE 注释行（":" 开头），客户端忽略但字节流动保活。"""
    assert _sse_format({"event": "", "data": "heartbeat"}) == ": heartbeat\n\n"
    # 正常事件帧格式不变
    assert _sse_format({"event": "done", "data": {"kind": "done"}}).startswith("event: done\n")
