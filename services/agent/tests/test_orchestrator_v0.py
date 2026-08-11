"""Phase 12 V0 — Orchestrator 单元测试。

覆盖：
  - 派生树硬上限（max_depth=2 + total_nodes≤30）
  - SubAgentReport 语义校验（status=ok 时 summary 必填；status=err 时 error_message 必填）
  - ContextPolicy / ModelPolicy 默认值正确
"""

import pytest
from agent.orchestrator.spec import (
    ContextPolicy,
    ModelPolicy,
    SubAgentReport,
    SubAgentSpec,
    SubAgentStatus,
)
from agent.orchestrator.tree_guard import (
    MAX_DEPTH,
    MAX_TOTAL_NODES,
    TreeLimitExceeded,
    enforce_tree_limits,
)

# ---- 派生树硬上限（铁律 2） -----------------------------------


def test_tree_guard_accepts_valid_depth():
    """depth=1 + total=0 → 通过"""
    spec = SubAgentSpec(
        sub_agent_id="s1",
        parent_run_id="p",
        depth=1,
        task_type="plan",
        task_description="test",
    )
    enforce_tree_limits(spec, current_total_nodes=0)


def test_tree_guard_accepts_max_depth():
    """depth=MAX_DEPTH (=2) → 通过"""
    spec = SubAgentSpec(
        sub_agent_id="s2",
        parent_run_id="p",
        depth=MAX_DEPTH,
        task_type="plan",
        task_description="test",
    )
    enforce_tree_limits(spec, current_total_nodes=0)


def test_tree_guard_rejects_depth_too_large():
    """depth > MAX_DEPTH → 抛"""
    spec = SubAgentSpec(
        sub_agent_id="s3",
        parent_run_id="p",
        depth=MAX_DEPTH + 1,
        task_type="plan",
        task_description="test",
    )
    with pytest.raises(TreeLimitExceeded) as exc:
        enforce_tree_limits(spec, current_total_nodes=0)
    assert exc.value.reason.startswith("depth")
    assert exc.value.current == MAX_DEPTH + 1
    assert exc.value.limit == MAX_DEPTH


def test_tree_guard_rejects_total_too_large():
    """current_total_nodes >= MAX_TOTAL_NODES → 抛"""
    spec = SubAgentSpec(
        sub_agent_id="s4",
        parent_run_id="p",
        depth=1,
        task_type="plan",
        task_description="test",
    )
    with pytest.raises(TreeLimitExceeded) as exc:
        enforce_tree_limits(spec, current_total_nodes=MAX_TOTAL_NODES)
    assert exc.value.reason.startswith("total_nodes")
    assert exc.value.limit == MAX_TOTAL_NODES


def test_tree_guard_rejects_depth_zero():
    """depth<1 → 抛（最低 1）"""
    spec = SubAgentSpec(
        sub_agent_id="s5",
        parent_run_id="p",
        depth=0,
        task_type="plan",
        task_description="test",
    )
    with pytest.raises(TreeLimitExceeded) as exc:
        enforce_tree_limits(spec, current_total_nodes=0)
    assert exc.value.reason == "depth<1"


# ---- SubAgentReport 语义校验 -----------------------------------


def test_report_validate_semantic_ok():
    """status=ok + summary 非空 → 无错误"""
    r = SubAgentReport(
        sub_agent_id="r1",
        parent_run_id="p",
        status=SubAgentStatus.OK,
        started_at="2026-07-22T00:00:00Z",
        summary="something meaningful",
        confidence=0.85,
        latency_ms=120,
    )
    assert r.validate_semantic() == []


def test_report_validate_semantic_ok_requires_summary():
    """status=ok + summary 空 → 错误"""
    r = SubAgentReport(
        sub_agent_id="r2",
        parent_run_id="p",
        status=SubAgentStatus.OK,
        started_at="2026-07-22T00:00:00Z",
        summary="   ",
    )
    errors = r.validate_semantic()
    assert any("summary" in e for e in errors)


def test_report_validate_semantic_err_requires_error():
    """status=err + error_message 空 → 错误"""
    r = SubAgentReport(
        sub_agent_id="r3",
        parent_run_id="p",
        status=SubAgentStatus.ERR,
        started_at="2026-07-22T00:00:00Z",
    )
    errors = r.validate_semantic()
    assert any("error_message" in e for e in errors)


def test_report_validate_semantic_confidence_bounds():
    """confidence > 1 → 错误"""
    r = SubAgentReport(
        sub_agent_id="r4",
        parent_run_id="p",
        status=SubAgentStatus.OK,
        started_at="2026-07-22T00:00:00Z",
        summary="ok",
        confidence=1.5,
    )
    errors = r.validate_semantic()
    assert any("confidence" in e for e in errors)


# ---- 默认值 -----------------------------------------------


def test_context_policy_defaults():
    cp = ContextPolicy()
    assert cp.strategy == "passthrough"
    assert cp.required_fields == []
    assert cp.shared_keys == []
    assert cp.max_summary_tokens == 500


def test_model_policy_defaults():
    mp = ModelPolicy()
    assert mp.role == "execution"
    assert mp.task_type == "custom"
    assert mp.carries_sensitive_payload is False
    assert mp.preferred_backend is None


def test_spec_minimal():
    """只填必填字段也能构造成功"""
    s = SubAgentSpec(
        sub_agent_id="s",
        parent_run_id="p",
        task_type="plan",
        task_description="x",
    )
    assert s.depth == 1  # 默认
    assert s.context_policy.strategy == "passthrough"
    assert s.model_policy.role == "execution"
