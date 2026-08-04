"""Phase 18 黄金集回归：路由分类 + 混合拆解打标。

验收线（spec §7）：
- 关键词/先验层对有明确期望的用例 100% 正确（确定性层，不设容忍）；
  整体黄金集准确率目标 ≥ 90%，当前 V1 集合全部断言。
- 混合拆解 framework 标签序列 100% 与期望一致（V1 集合）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.dual.policy import tag_plan_with_policy
from agent.dual.router import ModeRouter

FIXTURES = Path(__file__).parent / "fixtures"


def _routing_cases() -> list[tuple[str, str | None]]:
    data = json.loads((FIXTURES / "phase18_routing_golden.json").read_text(encoding="utf-8"))
    return [(c["prompt"], c["expected"]) for c in data["cases"]]


def _mixed_cases() -> list[dict]:
    data = json.loads((FIXTURES / "phase18_mixed_golden.json").read_text(encoding="utf-8"))
    return data["cases"]


@pytest.mark.parametrize("prompt,expected", _routing_cases())
def test_routing_golden(prompt: str, expected: str | None):
    """关键词层判定必须与黄金集一致（expected=None → 关键词层不强制，交给先验）。"""
    router = ModeRouter(llm=None)
    got = router.keyword_route(prompt)
    if expected is None:
        # 无明确期望：关键词层不应武断命中（可为 None 或任意值时放宽为仅校验类型）
        assert got in ("coding", "work", "mixed", None)
    else:
        assert got == expected, f"prompt={prompt!r} expected={expected} got={got}"


def test_routing_golden_accuracy_report():
    """整体准确率统计（有明确期望的用例）：V1 要求 ≥ 90%。"""
    cases = [(p, e) for p, e in _routing_cases() if e is not None]
    router = ModeRouter(llm=None)
    correct = sum(1 for p, e in cases if router.keyword_route(p) == e)
    acc = correct / len(cases)
    assert acc >= 0.90, f"routing accuracy {acc:.1%} < 90% ({correct}/{len(cases)})"


@pytest.mark.parametrize("case", _mixed_cases())
def test_mixed_decomposition_tags(case: dict):
    policies = tag_plan_with_policy(case["plan"], routing=case["routing"])
    got = [p["framework"] for p in policies]
    assert got == case["expected"], (
        f"routing={case['routing']} expected={case['expected']} got={got}"
    )


def test_mixed_decomposition_accuracy_report():
    cases = _mixed_cases()
    ok = 0
    for case in cases:
        policies = tag_plan_with_policy(case["plan"], routing=case["routing"])
        if [p["framework"] for p in policies] == case["expected"]:
            ok += 1
    acc = ok / len(cases)
    assert acc >= 0.85, f"mixed decomposition accuracy {acc:.1%} < 85%"
