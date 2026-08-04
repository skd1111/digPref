"""Phase 18 提示词融合：策略分阶段（Code→验证→Work→确认）+ 结构化执行报告。"""
from __future__ import annotations

from agent.dual.policy import ExecutionPolicy, build_policy, decomposition_stages
from agent.dual.report import build_dual_report


def test_policy_stage_follows_framework():
    assert build_policy("coding", "full").stage == "code"
    assert build_policy("work", "full").stage == "work"


def test_decomposition_stages_mixed_order():
    """HYBRID：Code 子任务 → verify → Work 子任务 → confirm。"""
    plan = [
        {"server": "builtin", "name": "write_file", "description": "写个导出脚本"},
        {"server": "database", "name": "run_sql", "description": "在数据库里跑报表"},
        {"server": "rest", "name": "request", "description": "发送通知"},
    ]
    stages = decomposition_stages(plan, routing="mixed")
    assert stages == [
        {"index": 0, "stage": "code"},
        {"index": 1, "stage": "verify"},
        {"index": 1, "stage": "work"},
        {"index": 2, "stage": "work"},
        {"index": -1, "stage": "confirm"},
    ]


def test_decomposition_stages_coding_appends_verify():
    plan = [{"server": "builtin", "name": "write_file", "description": "改代码"}]
    stages = decomposition_stages(plan, routing="coding")
    assert stages[-1] == {"index": -1, "stage": "verify"}
    assert stages[0] == {"index": 0, "stage": "code"}


def test_decomposition_stages_work_appends_confirm():
    plan = [{"server": "database", "name": "run_sql", "description": "查询订单"}]
    stages = decomposition_stages(plan, routing="work")
    assert stages[0] == {"index": 0, "stage": "work"}
    assert stages[-1] == {"index": -1, "stage": "confirm"}


def test_decomposition_stages_empty():
    assert decomposition_stages([], routing="mixed") == []


# ---- 结构化执行报告 ----

def _base_state(**over) -> dict:
    st = {
        "routing": None,
        "autonomy": "interactive",
        "repair_attempt": 0,
        "needs_human_intervention": False,
        "error_feedback": [],
        "execution_policies": [],
        "approval_decision": None,
    }
    st.update(over)
    return st


def test_report_none_without_routing():
    assert build_dual_report(_base_state()) is None


def test_report_coding_repair_success():
    st = _base_state(routing="coding", repair_attempt=2)
    report = build_dual_report(st)
    assert report is not None
    assert "CODE" in report
    assert "2" in report
    assert "修复" in report


def test_report_coding_repair_exhausted():
    st = _base_state(routing="coding", repair_attempt=3, needs_human_intervention=True,
                     error_feedback=[{"attempt": 3, "error": "语法错误", "files": ["a.py"]}])
    report = build_dual_report(st)
    assert report is not None
    assert "人工" in report or "未通过" in report


def test_report_coding_no_signal_skipped():
    """无修复/无验证信号 → 不伪造报告（不伪造结果红线）。"""
    assert build_dual_report(_base_state(routing="coding")) is None


def test_report_work_approval_status():
    st = _base_state(routing="work", approval_decision="approve")
    report = build_dual_report(st)
    assert report is not None
    assert "WORK" in report
    assert "批准" in report


def test_report_work_auto_mode_marked():
    st = _base_state(routing="work", autonomy="auto", approval_decision="approve")
    report = build_dual_report(st)
    assert report is not None
    assert "自动模式" in report


def test_report_mixed_contains_both():
    st = _base_state(routing="mixed", repair_attempt=1, approval_decision="reject")
    report = build_dual_report(st)
    assert report is not None
    assert "CODE" in report and "WORK" in report
    assert "拒绝" in report
