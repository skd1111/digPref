"""Tests for hitl_gate_node — verify approval_id is cleared after decision.

回归测试：decision 返回后，approval_id 必须设为 None，否则下次再进入时
带过期 UUID → check_decision() 返 None → 落到"等待"分支 → 无限循环。

fail-closed 回归（借鉴 dsh approval seam；2026-08-14）：
    - gate 侧超时守卫：后台轮询任务丢失（Agent 重启）时无人写决策，
      gate 必须在超时后自动 reject，绝不无限等待
    - start_approval 异常 → 直接 reject（审批缺失不放行写操作）
"""

from __future__ import annotations

import asyncio
import time

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
        "回归 #2: 决策返回后没清 approval_id，会导致下次进入时 check_decision() 返 None → 无限循环"
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


# ---- fail-closed 回归（2026-08-14） ------------------------------------------


def test_gate_timeout_guard_rejects_without_decision():
    """后台轮询任务丢失（如 Agent 重启）时无决策到达 → gate 超时自动 reject。

    对齐 dsh approval 词汇表：无应答 / 不可用 → fail-closed 拒绝，绝不无限等待。
    """
    _clear_local_decisions()
    s = _make_write_state(None)
    out1 = _run(hitl_gate_node(s))
    s.update(out1)
    assert isinstance(s.get("approval_started_at"), float)

    # 模拟重启后场景：轮询任务没了、决策从未到达、时间已超 approval_timeout_sec
    _clear_local_decisions()
    s["approval_started_at"] = time.time() - 10_000
    out2 = _run(hitl_gate_node(s))

    assert out2["approval_decision"] == "reject", "gate 侧超时必须 fail-closed 拒绝"
    assert out2["approval_id"] is None
    assert out2["awaiting_approval"] is False
    assert out2["approval_started_at"] is None
    assert any(t.get("reason") == "timeout_guard" for t in out2["trace"])
    _clear_local_decisions()


def test_gate_timeout_guard_backfills_missing_started_at():
    """存量进行中的审批无时间戳（老状态）→ 补记当前时刻，下轮开始守卫。"""
    _clear_local_decisions()
    s = _make_write_state(None)
    out1 = _run(hitl_gate_node(s))
    s.update(out1)
    s.pop("approval_started_at", None)  # 模拟无新字段的存量状态

    before = time.time()
    out2 = _run(hitl_gate_node(s))
    assert out2["awaiting_approval"] is True
    assert isinstance(out2.get("approval_started_at"), float)
    assert out2["approval_started_at"] >= before
    _clear_local_decisions()


def test_start_approval_failure_fails_closed(monkeypatch):
    """start_approval 异常 → 直接 reject，绝不在审批缺失时放行写操作。"""
    _clear_local_decisions()

    async def broken_start(**kwargs):
        raise RuntimeError("redis down")

    monkeypatch.setattr("agent.graph.nodes.hitl_gate.start_approval", broken_start)
    s = _make_write_state(None)
    out = _run(hitl_gate_node(s))

    assert out["approval_decision"] == "reject"
    assert out["awaiting_approval"] is False
    assert any(t.get("reason") == "start_failed" for t in out["trace"])
    _clear_local_decisions()
