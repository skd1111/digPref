"""Phase 18 自动模式决策矩阵：risk × autonomy × hard_block 全组合（确定性）。"""

from __future__ import annotations

import itertools

import pytest
from agent.dual.autonomy import AutonomyDecision, decide, is_hard_blocked


@pytest.mark.parametrize(
    "risk,autonomy,blocked",
    itertools.product(
        ["low", "medium", "high", "critical"],
        ["interactive", "auto"],
        [False, True],
    ),
)
def test_autonomy_decision_matrix(risk, autonomy, blocked):
    d = decide(risk_level=risk, autonomy=autonomy, hard_blocked=blocked)
    assert isinstance(d, AutonomyDecision)
    if blocked:
        assert d.action == "reject"
        assert d.decided_by == "hard_block"
    elif autonomy == "interactive":
        assert d.action == ("approve" if risk == "low" else "wait_user")
        assert d.decided_by == ("policy" if risk == "low" else "pending_user")
    else:  # auto
        if risk == "low":
            assert d.action == "approve"
            assert d.decided_by == "auto_low_risk"
        else:
            assert d.action == "auto_select_recommended"
            assert d.decided_by == "auto_mode"


def test_unknown_risk_treated_as_medium():
    d = decide(risk_level="weird", autonomy="interactive", hard_blocked=False)
    assert d.action == "wait_user"


def test_unknown_autonomy_falls_back_to_interactive():
    d = decide(risk_level="high", autonomy="god_mode", hard_blocked=False)
    assert d.action == "wait_user"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("DROP TABLE orders", True),
        ("drop table users;", True),
        ("TRUNCATE TABLE logs", True),
        ("SELECT * FROM orders", False),
        ("UPDATE orders SET status='paid'", False),
    ],
)
def test_is_hard_blocked_sql(text, expected):
    call = {"name": "run_sql", "args": {"sql": text}}
    assert is_hard_blocked(call) is expected


def test_is_hard_blocked_no_args():
    assert is_hard_blocked({"name": "write_file"}) is False
