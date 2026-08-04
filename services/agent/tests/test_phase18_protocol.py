"""Phase 18 协议契约：ApprovalRequest 选项扩展（向后兼容）。

验收：
- options / recommendedOptionId / recommendationReason 三字段可序列化往返
- 不带新字段时保持原有行为（options 默认空列表）
"""
from __future__ import annotations

from protocol.approval import ApprovalOption, ApprovalRequest


def test_approval_request_options_roundtrip():
    req = ApprovalRequest(
        id="a1",
        runId="r1",
        plan={"server": "database", "name": "run_sql", "args": {}},
        riskLevel="medium",
        options=[
            ApprovalOption(
                id="o1",
                label="执行（限近7天）",
                adjustedPlan="SELECT ... LIMIT ...",
                riskNote=None,
            )
        ],
        recommendedOptionId="o1",
        recommendationReason="数据量可控",
        createdAt="2026-08-04T00:00:00Z",
    )
    data = req.model_dump(by_alias=True)
    assert data["recommendedOptionId"] == "o1"
    assert data["recommendationReason"] == "数据量可控"
    assert data["options"][0]["adjustedPlan"] == "SELECT ... LIMIT ..."
    # 反序列化往返
    again = ApprovalRequest.model_validate(data)
    assert again.options[0].label == "执行（限近7天）"
    assert again.recommended_option_id == "o1"


def test_approval_request_backward_compatible_without_options():
    req = ApprovalRequest(
        id="a1",
        runId="r1",
        plan={"server": "builtin", "name": "read_file", "args": {}},
        riskLevel="low",
        createdAt="2026-08-04T00:00:00Z",
    )
    assert req.options == []
    assert req.recommended_option_id is None
    assert req.recommendation_reason is None
