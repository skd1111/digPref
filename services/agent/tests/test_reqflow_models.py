"""test_reqflow_models.py —— 需求卡片数据模型 + 状态机测试（reqflow V1）。"""

from __future__ import annotations

from agent.reqflow.models import (
    APPROVED,
    DEVELOPING,
    DONE,
    DRAFT,
    PENDING_APPROVAL,
    REJECTED,
    ReqCard,
    can_transition,
)


def test_can_transition_happy_path():
    assert can_transition(DRAFT, PENDING_APPROVAL)
    assert can_transition(PENDING_APPROVAL, APPROVED)
    assert can_transition(APPROVED, DEVELOPING)
    assert can_transition(DEVELOPING, DONE)


def test_can_transition_reject_from_any_non_terminal():
    for s in (DRAFT, PENDING_APPROVAL, APPROVED, DEVELOPING):
        assert can_transition(s, REJECTED)


def test_can_transition_illegal():
    assert not can_transition(DRAFT, APPROVED)  # 不能跳级
    assert not can_transition(DONE, DEVELOPING)  # 终态不可回退
    assert not can_transition(REJECTED, DRAFT)
    assert not can_transition("unknown", DRAFT)


def test_req_card_defaults():
    c = ReqCard(id="", batch_id="BAT-1", project_name="p", system_name="s", title="t")
    assert c.status == DRAFT
    assert c.feature_ids == []
    assert c.external_systems == []
    assert c.priority == "P2"
    assert c.version == 1  # 创建即 v1
    assert c.approved_by is None
    assert c.approved_at is None


def test_req_card_roundtrip():
    c = ReqCard(
        id="REQ-1",
        batch_id="BAT-1",
        project_name="p",
        system_name="订单系统",
        title="部分取消",
        feature_ids=["f1", "f2"],
        external_systems=["支付网关"],
        status=PENDING_APPROVAL,
    )
    d = c.to_dict()
    assert d["feature_ids"] == ["f1", "f2"]
    c2 = ReqCard.from_dict(d)
    assert c2 == c
