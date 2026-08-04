"""Phase 18 Auto-Repair 循环：预算控制 + 验证钩子。"""
from __future__ import annotations

from agent.dual.repair import (
    coding_budget,
    should_retry,
    validate_written_files,
)


def _coding_state(attempt: int = 0, budget: int = 3) -> dict:
    return {
        "routing": "coding",
        "execution_policies": [
            {"framework": "coding", "max_repair_attempts": budget,
             "validator_level": "full", "autonomy": "interactive"}
        ],
        "repair_attempt": attempt,
        "error_feedback": [],
    }


def test_coding_budget_from_policy():
    assert coding_budget(_coding_state(budget=3)) == 3
    assert coding_budget(_coding_state(budget=1)) == 1


def test_coding_budget_default_when_no_policy():
    assert coding_budget({"routing": "coding"}) == 3


def test_should_retry_under_budget():
    assert should_retry(_coding_state(attempt=1, budget=3)) is True


def test_should_retry_budget_exhausted():
    assert should_retry(_coding_state(attempt=3, budget=3)) is False


def test_hook_ignores_non_coding_routing():
    state = _coding_state()
    state["routing"] = "work"
    out = validate_written_files(state, pairs=[])
    assert out is None


def test_hook_passes_valid_python(tmp_path):
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n", encoding="utf-8")
    state = _coding_state()
    pairs = [(
        {"name": "write_file", "arguments": {"path": str(f), "content": "x = 1\n"}},
        {"id": "c1", "name": "write_file", "ok": True},
    )]
    out = validate_written_files(state, pairs=pairs)
    assert out is None  # 验证通过 → 无 repair 动作


def test_hook_flags_broken_python(tmp_path):
    f = tmp_path / "bad.py"
    f.write_text("def broken(:\n", encoding="utf-8")
    state = _coding_state(attempt=0, budget=3)
    pairs = [(
        {"name": "write_file", "arguments": {"path": str(f), "content": "def broken(:\n"}},
        {"id": "c1", "name": "write_file", "ok": True},
    )]
    out = validate_written_files(state, pairs=pairs)
    assert out is not None
    assert out["repair_attempt"] == 1
    assert out["error_feedback"] and "语法错误" in out["error_feedback"][0]["error"]
    # 合成验证失败结果喂回模型
    assert any(r.get("name") == "coding_validation" and not r["ok"] for r in out["extra_results"])
    assert out.get("needs_human_intervention") is not True


def test_hook_exhausts_budget_marks_human_intervention(tmp_path):
    f = tmp_path / "bad2.py"
    f.write_text("def broken(:\n", encoding="utf-8")
    state = _coding_state(attempt=2, budget=3)  # 本次失败后达到上限
    pairs = [(
        {"name": "write_file", "arguments": {"path": str(f), "content": "x"}},
        {"id": "c1", "name": "write_file", "ok": True},
    )]
    out = validate_written_files(state, pairs=pairs)
    assert out is not None
    assert out["repair_attempt"] == 3
    assert out["needs_human_intervention"] is True


def test_hook_ignores_failed_calls_and_non_write_tools(tmp_path):
    state = _coding_state()
    pairs = [
        ({"name": "write_file", "arguments": {"path": "x.py"}},
         {"id": "c1", "name": "write_file", "ok": False, "error": "denied"}),
        ({"name": "read_file", "arguments": {"path": "x.py"}},
         {"id": "c2", "name": "read_file", "ok": True}),
    ]
    assert validate_written_files(state, pairs=pairs) is None
