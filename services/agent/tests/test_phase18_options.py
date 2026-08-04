"""Phase 18 推荐选项生成：work 决策点的候选方案 + 推荐项。"""
from __future__ import annotations

import json

import pytest

from agent.dual.options import generate_approval_options
from protocol.approval import ApprovalOption


class _FakeLLM:
    def __init__(self, text: str):
        self._text = text

    async def route(self, *, task: str, prompt: str) -> str:
        return self._text


_GOOD_JSON = json.dumps({
    "options": [
        {"id": "o1", "label": "执行（限近 7 天）", "adjusted_plan": "SELECT ... WHERE ts >= now()-7d", "risk_note": "数据量可控"},
        {"id": "o2", "label": "不执行", "adjusted_plan": "", "risk_note": None},
    ],
    "recommended_option_id": "o1",
    "recommendation_reason": "限定时间窗后风险可控",
}, ensure_ascii=False)


async def test_options_parsed_with_abort_option():
    opts, rec, reason = await generate_approval_options(
        _FakeLLM(_GOOD_JSON), call={"name": "run_sql"}
    )
    assert isinstance(opts[0], ApprovalOption)
    assert any(o.label == "不执行" for o in opts)
    assert rec == "o1"
    assert reason


async def test_abort_option_injected_when_missing():
    payload = json.dumps({
        "options": [
            {"id": "o1", "label": "直接执行", "adjusted_plan": "...", "risk_note": None},
        ],
        "recommended_option_id": "o1",
        "recommendation_reason": "r",
    }, ensure_ascii=False)
    opts, rec, reason = await generate_approval_options(
        _FakeLLM(payload), call={"name": "run_sql"}
    )
    assert any(o.label == "不执行" for o in opts)


async def test_invalid_json_returns_empty():
    opts, rec, reason = await generate_approval_options(
        _FakeLLM("抱歉我无法生成"), call={"name": "run_sql"}
    )
    assert opts == []
    assert rec is None
    assert reason is None


async def test_llm_exception_returns_empty():
    class _Boom:
        async def route(self, *, task: str, prompt: str) -> str:
            raise RuntimeError("llm down")

    opts, rec, reason = await generate_approval_options(_Boom(), call={"name": "x"})
    assert (opts, rec, reason) == ([], None, None)


async def test_json_in_code_fence_extracted():
    text = "以下是候选项：\n```json\n" + _GOOD_JSON + "\n```"
    opts, rec, reason = await generate_approval_options(
        _FakeLLM(text), call={"name": "run_sql"}
    )
    assert rec == "o1" and len(opts) >= 2


async def test_none_llm_returns_empty():
    opts, rec, reason = await generate_approval_options(None, call={"name": "x"})
    assert (opts, rec, reason) == ([], None, None)
