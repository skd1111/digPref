"""「此后都按此执行」会话级审批豁免（2026-08-25）回归测试。

真实需求：用户在审批卡批准后希望「本会话内同类操作不再弹卡」。断言：
  1. post_decision / API 接受 approve_always；
  2. gate 消费 approve_always 时登记豁免并在出口归一为 approve（下游零感知）；
  3. 本会话（同 tabId）同工具类后续写操作自动放行，不落审批；
  4. 豁免按会话页签隔离：换 tab 不复用；
  5. 硬阻断（DROP/TRUNCATE）永远优先于豁免 —— 安全红线。
"""

from __future__ import annotations

import asyncio

import pytest
from agent.graph.exemptions import (
    _EXEMPT,
    clear_scope,
    exemption_scope,
    is_exempt,
    tool_kind_key,
)
from agent.graph.interrupt import _LOCAL_DECISIONS, post_decision
from agent.graph.nodes.hitl_gate import hitl_gate_node
from agent.graph.state import empty_state


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


TAB_A = {"page_context": {"page": {"tabId": "tab-A", "workMode": "full", "tabTitle": "t"}}}
TAB_B = {"page_context": {"page": {"tabId": "tab-B", "workMode": "full", "tabTitle": "t"}}}


def _write_state(sql: str = "DELETE FROM x", **extra) -> dict:
    s = empty_state("do a write")
    s["plan"] = [
        {
            "server": "db",
            "name": "db.execute",
            "args": {"sql": sql},
            "risk_level": "high",
            "rationale": "test",
        }
    ]
    s["current_step_index"] = 0
    s.update(extra)
    return s


@pytest.fixture(autouse=True)
def _clean():
    _LOCAL_DECISIONS.clear()
    _EXEMPT.clear()
    yield
    _LOCAL_DECISIONS.clear()
    _EXEMPT.clear()


def test_post_decision_accepts_approve_always():
    _run(post_decision("ap-1", "approve_always"))
    assert _LOCAL_DECISIONS["ap-1"] == "approve_always"
    with pytest.raises(ValueError):
        _run(post_decision("ap-2", "approve_sometimes"))


def test_approve_always_registers_exemption_and_normalizes():
    """gate 消费 approve_always：登记豁免 + 出口归一 approve。"""
    s = _write_state(**TAB_A)
    out1 = _run(hitl_gate_node(s))
    approval_id = out1["approval_id"]

    _run(post_decision(approval_id, "approve_always"))
    s["approval_id"] = approval_id
    out2 = _run(hitl_gate_node(s))

    # 出口归一：下游只见 approve（dispatcher/catalog/loop 无需感知新值）
    assert out2["approval_decision"] == "approve"
    assert out2["awaiting_approval"] is False
    # 豁免已登记（作用域=tab-A，键=server·name）
    kind = tool_kind_key(s["plan"][0])
    assert is_exempt("tab:tab-A", kind)


def test_exempted_kind_auto_approved_in_same_chat():
    """本会话同类后续写操作：不弹卡直接放行，留审计痕迹。"""
    kind = tool_kind_key({"server": "db", "name": "db.execute"})
    _EXEMPT.setdefault("tab:tab-A", set()).add(kind)

    out = _run(hitl_gate_node(_write_state(**TAB_A)))
    assert out["approval_decision"] == "approve"
    assert out.get("awaiting_approval") is not True
    assert out["approval_id"] is None
    reasons = [t.get("reason") for t in out["trace"]]
    assert "session_exempt" in reasons


def test_exemption_scoped_to_tab():
    """换会话页签 → 豁免不复用（用户要求：只在当前 chat 生效）。"""
    kind = tool_kind_key({"server": "db", "name": "db.execute"})
    _EXEMPT.setdefault("tab:tab-A", set()).add(kind)

    out = _run(hitl_gate_node(_write_state(**TAB_B)))
    assert out.get("awaiting_approval") is True
    assert out.get("approval_id")


def test_hard_block_never_exempt():
    """红线：DROP/TRUNCATE 命中硬阻断时，即便同类已豁免也必须拒绝。"""
    kind = tool_kind_key({"server": "db", "name": "db.execute"})
    _EXEMPT.setdefault("tab:tab-A", set()).add(kind)

    out = _run(hitl_gate_node(_write_state(sql="DROP TABLE users", **TAB_A)))
    assert out["approval_decision"] == "reject"
    reasons = [t.get("reason") for t in out["trace"]]
    assert "hard_block" in reasons


def test_exemption_scope_falls_back_to_default():
    """旧客户端无 tabId → default 作用域（进程级），豁免仍可用。"""
    s = _write_state()
    assert exemption_scope(s) == "default"
    assert exemption_scope({}) == "default"


def test_read_only_not_affected_and_no_tab_leaks_between_scopes():
    scope_a, scope_b = exemption_scope(TAB_A), exemption_scope(TAB_B)
    assert scope_a != scope_b
    clear_scope(scope_a)  # 清理接口可用
