"""test_biznav_events —— Phase 2G V1.3 SSE 事件 emit 机制测试。

覆盖：
- emit_biznav_event 写入 deque
- consume_biznav_events 异步拉出（FIFO）
- flush_biznav_events 清空（测试隔离）
- 3 个事件通道名常量正确
"""
from __future__ import annotations

import asyncio

import pytest

from agent.biznav.events import (
    EVT_EXTRACTION_DONE,
    EVT_FEATURE_AFFECTED,
    EVT_YAML_RELOADED,
    consume_biznav_events,
    emit_biznav_event,
    flush_biznav_events,
)


@pytest.fixture(autouse=True)
def _clean_biznav_events():
    """每个测试前后清空事件队列，避免跨用例污染。"""
    flush_biznav_events()
    yield
    flush_biznav_events()


def test_three_channel_names_match_python_rust_ts():
    """3 个事件通道名必须严格与 Rust sse_bridge.rs + TS events.ts 一致。"""
    assert EVT_YAML_RELOADED == "biznav_yaml_reloaded"
    assert EVT_FEATURE_AFFECTED == "biznav_feature_affected"
    assert EVT_EXTRACTION_DONE == "biznav_extraction_done"


@pytest.mark.asyncio
async def test_emit_then_consume_returns_fifo_order():
    """emit 多次 → consume 返回 FIFO 顺序。"""
    emit_biznav_event(EVT_YAML_RELOADED, {"order": 1})
    emit_biznav_event(EVT_FEATURE_AFFECTED, {"order": 2})
    emit_biznav_event(EVT_EXTRACTION_DONE, {"order": 3})
    events = await consume_biznav_events()
    assert len(events) == 3
    assert events[0] == (EVT_YAML_RELOADED, {"order": 1})
    assert events[1] == (EVT_FEATURE_AFFECTED, {"order": 2})
    assert events[2] == (EVT_EXTRACTION_DONE, {"order": 3})


@pytest.mark.asyncio
async def test_consume_drains_queue_returns_empty_on_second_call():
    """consume 一次性拉空 → 第二次返空 list。"""
    emit_biznav_event(EVT_YAML_RELOADED, {"k": "v"})
    first = await consume_biznav_events()
    second = await consume_biznav_events()
    assert len(first) == 1
    assert len(second) == 0


def test_flush_clears_queue():
    """flush 后 consume 返空。"""
    emit_biznav_event(EVT_YAML_RELOADED, {"k": "v"})
    emit_biznav_event(EVT_FEATURE_AFFECTED, {"k": "v"})
    flush_biznav_events()
    events = asyncio.run(consume_biznav_events())
    assert events == []