"""Phase 18：chat 请求透传 workMode / autonomy 到状态机初始状态。"""

from __future__ import annotations

import pytest
from agent.api.chat import ChatRequest
from pydantic import ValidationError


def test_chat_request_accepts_phase18_fields():
    req = ChatRequest(prompt="hi", workMode="operator", autonomy="auto")
    assert req.work_mode == "operator"
    assert req.autonomy == "auto"


def test_chat_request_defaults():
    req = ChatRequest(prompt="hi")
    assert req.work_mode == "full"
    assert req.autonomy == "interactive"


def test_chat_request_invalid_values_rejected():
    with pytest.raises(ValidationError):
        ChatRequest(prompt="hi", workMode="hacker")
    with pytest.raises(ValidationError):
        ChatRequest(prompt="hi", autonomy="god_mode")


def test_stream_initial_state_carries_phase18_fields():
    """stream_graph_events 的 extra_state 应合并进初始状态。"""
    from agent.graph.state import empty_state

    s = empty_state("hi")
    extra = {"work_mode": "analyst", "autonomy": "auto"}
    s.update(extra)
    assert s["work_mode"] == "analyst"
    assert s["autonomy"] == "auto"
