"""BUGFIX #118 / #152 回归：SSE 心跳保活 + 终止信号兜底。

卡死链路（2026-08-17 实测）：内网模型挂死 → 流 60s 无字节 → Rust reqwest
read_timeout=60s 断连 → uvicorn 取消图任务 → CancelledError 穿透
`except Exception` → done/error 发不出 → 前端永久卡「思考中」。
#152 补充（2026-08-26）：图在终止环节挂死而心跳照常发（流被保活、
read_timeout 永不触发）→ 无图块熔断主动收尾；CancelledError 改为直接重抛
（消费方已关连接，yield 违反 GeneratorExit 协议），终止信号由 Rust 桥兑底。

覆盖：
  1. 长静默期间按间隔发具名 heartbeat 心跳（事件不丢、顺序不乱；
     BUGFIX #161 起为具名事件，前端看门狗可见）
  2. 图执行抛 CancelledError → 重抛（不再违反协议 yield）
  3. 图执行抛普通异常 → error + done（安全路径）
  4. 图挂死零块超熔断阈值 → 主动收尾发 done（#152）
  5. _sse_format：空 event 名输出注释行（":" 开头，客户端静默忽略）
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
    """静默 0.2s、心跳间隔 0.05s → 至少 1 条心跳，且业务事件与 done 不丢。

    BUGFIX #161：心跳为具名事件（event=heartbeat，data 带 runId），
    前端看门狗据此感知流存活；原注释行心跳前端不可见。
    """
    events = await _collect(_SlowGraph(delay=0.2), monkeypatch=monkeypatch)

    heartbeats = [e for e in events if e.get("event") == "heartbeat"]
    assert len(heartbeats) >= 1
    assert all('"kind": "heartbeat"' in e["data"] for e in heartbeats)
    assert all('"runId": "run-hb"' in e["data"] for e in heartbeats)

    kinds = [e["event"] for e in events if e.get("event")]
    assert "trace" in kinds  # intent chunk 正常下发
    assert kinds[-1] == "done"  # done 兜底仍在最后


async def test_cancelled_error_reraised_for_bridge_fallback(monkeypatch):
    """#152 新契约：CancelledError 直接重抛，不再在取消路径 yield。

    取消源自消费方关连接，此时 yield 要么写不出去、要么违反 async 生成器
    GeneratorExit 协议；终止信号由 Rust 桥 DONE(cancelled)/ERROR 兑底。
    """
    with pytest.raises(asyncio.CancelledError):
        await _collect(_CancelledGraph(), monkeypatch=monkeypatch)


async def test_regular_exception_behavior_unchanged(monkeypatch):
    """普通异常路径回归：error（含异常信息）+ done。"""
    events = await _collect(_BoomGraph(), monkeypatch=monkeypatch)

    named = [e for e in events if e.get("event")]
    assert named[-2]["event"] == "error"
    assert "boom" in named[-2]["data"]
    assert named[-1]["event"] == "done"


class _HungGraph(_CapturingGraph):
    """出一个 chunk 后永久挂死（不抛 StopAsyncIteration）—— #152 实测场景：
    responder 已产出终答但 astream 不退出，心跳保活下前端永久转圈。"""

    async def astream(self, initial_state, cfg, stream_mode=None):
        self._capture(initial_state)
        yield ("values", {"trace": [{"node": "init"}, {"node": "intent", "status": "ok"}]})
        await asyncio.sleep(3600)  # 挂死；熔断器会在远早于此的时间点放弃等待
        if False:  # pragma: no cover
            yield None


async def test_silence_breaker_ends_hung_stream(monkeypatch):
    """#152 无图块熔断：零块超过阈值 → 主动收尾，done 仍是最后一条。"""
    monkeypatch.setattr(stream_mod, "_MAX_SILENCE_SEC", 0.12)
    events = await _collect(_HungGraph(), monkeypatch=monkeypatch)

    named = [e for e in events if e.get("event")]
    kinds = [e["event"] for e in named]
    assert "trace" in kinds  # 已产出的块不丢
    assert kinds[-1] == "done"  # 熔断后仍走安全路径发 done


def test_sse_format_empty_event_is_comment_line():
    """空 event 名 → SSE 注释行（":" 开头），客户端忽略但字节流动保活。"""
    assert _sse_format({"event": "", "data": "heartbeat"}) == ": heartbeat\n\n"
    # 正常事件帧格式不变
    assert _sse_format({"event": "done", "data": {"kind": "done"}}).startswith("event: done\n")
