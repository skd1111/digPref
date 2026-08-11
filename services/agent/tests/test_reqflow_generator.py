"""test_reqflow_generator.py —— AI 需求卡片生成器测试（reqflow V1）。"""

from __future__ import annotations

import json

import pytest
from agent.reqflow.generator import generate_card_draft

FAKE_FEATURES = [
    {
        "id": "f1",
        "name": "创建订单",
        "description": "用户提交购物车生成订单",
        "related_apis": [{"method": "POST", "path": "/orders"}],
        "related_tables": [{"name": "orders"}],
        "business_rules": [{"text": "库存不足禁止下单"}],
    }
]


@pytest.mark.asyncio
async def test_generate_parses_object():
    payload = {
        "title": "订单支持部分取消",
        "business_value": "减少整单取消带来的库存波动",
        "change_points": "创建订单增加按行取消能力",
        "feasibility": "risky",
        "feasibility_notes": "涉及库存回补时序",
        "impact": "影响退款功能",
        "external_systems": ["支付网关"],
        "priority": "P1",
    }

    async def fake_llm(messages):
        # messages 应包含功能点上下文与对话摘要
        joined = " ".join(str(m.get("content", "")) for m in messages)
        assert "创建订单" in joined and "用户想支持部分取消" in joined
        return json.dumps(payload, ensure_ascii=False)

    draft = await generate_card_draft(
        llm_call=fake_llm,
        features=FAKE_FEATURES,
        conversation_summary="用户想支持部分取消",
        system_name="订单系统",
    )
    assert draft["title"] == "订单支持部分取消"
    assert draft["feasibility"] == "risky"
    assert draft["external_systems"] == ["支付网关"]
    assert draft["priority"] == "P1"


@pytest.mark.asyncio
async def test_generate_normalizes_invalid_values():
    payload = {
        "title": "T",
        "feasibility": "随便写的",
        "priority": "P9",
        "external_systems": "支付网关",
    }

    async def fake_llm(messages):
        return json.dumps(payload, ensure_ascii=False)

    draft = await generate_card_draft(llm_call=fake_llm, features=[], conversation_summary="x")
    assert draft["feasibility"] == "risky"  # 非法值兜底
    assert draft["priority"] == "P2"
    assert draft["external_systems"] == ["支付网关"]  # 字符串自动包成数组


@pytest.mark.asyncio
async def test_generate_raises_on_empty_output():
    async def empty_llm(messages):
        return ""

    with pytest.raises(RuntimeError):
        await generate_card_draft(
            llm_call=empty_llm, features=FAKE_FEATURES, conversation_summary="x"
        )


@pytest.mark.asyncio
async def test_generate_raises_on_non_object():
    async def array_llm(messages):
        return "[1, 2, 3]"

    with pytest.raises(RuntimeError):
        await generate_card_draft(
            llm_call=array_llm, features=FAKE_FEATURES, conversation_summary="x"
        )


@pytest.mark.asyncio
async def test_generate_llm_exception_propagates():
    async def broken_llm(messages):
        raise RuntimeError("所有 LLM 后端均不可用")

    with pytest.raises(RuntimeError, match="所有 LLM 后端均不可用"):
        await generate_card_draft(
            llm_call=broken_llm, features=FAKE_FEATURES, conversation_summary="x"
        )
