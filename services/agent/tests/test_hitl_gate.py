"""Tests for hitl_gate_node — verify approval_id is cleared after decision.

回归测试：decision 返回后，approval_id 必须设为 None，否则下次再进入时
带过期 UUID → check_decision() 返 None → 落到"等待"分支 → 无限循环。
"""
from __future__ import annotations

import asyncio

from agent.graph.interrupt import _LOCAL_DECISIONS, post_decision
from agent.graph.nodes.hitl_gate import hitl_gate_node
from agent.graph.state import empty_state


def _run(coro):
    """Run async coroutine in a fresh event loop (no pytest-asyncio needed)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _clear_local_decisions():
    _LOCAL_DECISIONS.clear()


def _make_write_state(approval_id):
    s = empty_state("do a write")
    s["plan"] = [
        {
            "server": "db",
            "name": "db.execute",
            "args": {"sql": "DELETE FROM x"},
            "risk_level": "high",
            "rationale": "test",
        }
    ]
    s["current_step_index"] = 0
    if approval_id is not None:
        s["approval_id"] = approval_id
    return s


def test_first_visit_returns_approval_id():
    _clear_local_decisions()
    s = _make_write_state(None)
    out = _run(hitl_gate_node(s))
    assert "approval_id" in out
    assert out["approval_id"] is not None
    assert out["awaiting_approval"] is True
    _clear_local_decisions()


def test_decision_clears_approval_id():
    """回归 #2: 决策到达后，approval_id 必须清空 None。"""
    _clear_local_decisions()
    s = _make_write_state(None)
    out1 = _run(hitl_gate_node(s))
    approval_id = out1["approval_id"]

    # 模拟：手动 post decision（避免依赖后台轮询时序）
    _run(post_decision(approval_id, "approve"))

    # 第二次进入：决策到达
    s["approval_id"] = approval_id
    out2 = _run(hitl_gate_node(s))
    # 关键断言：approval_id 必须 = None
    assert out2["approval_id"] is None, (
        "回归 #2: 决策返回后没清 approval_id，会导致下次进入时 "
        "check_decision() 返 None → 无限循环"
    )
    assert out2["approval_decision"] == "approve"
    assert out2["awaiting_approval"] is False
    _clear_local_decisions()


def test_pending_decision_keeps_id():
    """没决定时 approval_id 保留（不能清，否则就丢了）。

    pending 分支的返回值是 partial state update —— state 里的 approval_id 已经在了，
    不需要在返回值里再写一次。验证：
      - 返回 awaiting_approval=True
      - 状态里 approval_id 仍是原值（没被覆盖为 None）
    """
    _clear_local_decisions()
    s = _make_write_state(None)
    out1 = _run(hitl_gate_node(s))
    approval_id = out1["approval_id"]
    # 模拟 merge：partial state update 合并进 state
    s.update(out1)
    # 第二次进入：还没决定
    out2 = _run(hitl_gate_node(s))
    s.update(out2)
    # state 里 approval_id 仍应是原值（pending 分支不清空）
    assert s.get("approval_id") == approval_id
    assert out2["awaiting_approval"] is True
    _clear_local_decisions()
