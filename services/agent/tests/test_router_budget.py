"""预算控制测试。"""

from agent.llm.budget import BudgetController
from agent.llm.models import LLMBackend


def _b(cost: float) -> LLMBackend:
    return LLMBackend(name="b", type="cloud", base_url="x", model_name="m", cost_per_1k_tokens=cost)


def test_local_free_passes():
    bc = BudgetController()
    assert bc.check(_b(0.0), 1000).allowed is True


def test_expensive_per_task_blocked():
    bc = BudgetController(per_task_limit=0.1)
    v = bc.check(_b(0.05), 1000)  # 0.05/1000 * 1000 = 0.05 OK
    assert v.allowed is True
    v = bc.check(_b(0.5), 1000)  # 0.5
    assert v.allowed is False
    assert "per-task" in v.reason


def test_daily_budget_accumulates():
    bc = BudgetController(daily_limit=1.0, per_task_limit=0.5)
    bc.record_spend(_b(0.1), 1000)  # 累计 0.1
    bc.record_spend(_b(0.1), 1000)  # 累计 0.2
    assert bc.daily_spent == pytest.approx(0.2, abs=0.01)


def test_daily_exceed_blocks():
    """超日预算后 check 拒绝。注意 check() **不**自己累计花销，调用方需显式 record_spend。"""
    bc = BudgetController(daily_limit=0.5, per_task_limit=0.3)
    # 直接模拟"已花 0.45"，下一次 check 加 0.1 → 0.55 > 0.5 应被拒
    bc.record_spend(_b(0.15), 1000)  # 0.15
    bc.record_spend(_b(0.15), 1000)  # 0.30
    bc.record_spend(_b(0.15), 1000)  # 0.45
    assert bc.daily_spent == pytest.approx(0.45, abs=0.01)
    v = bc.check(_b(0.1), 1000)  # 0.45 + 0.1 = 0.55 > 0.5 拒
    assert v.allowed is False
    assert "daily" in v.reason


def test_estimate_correct():
    bc = BudgetController()
    cost = bc.estimate(_b(0.01), 2000)  # 0.01 * 2 = 0.02
    assert abs(cost - 0.02) < 1e-6


# 避免 import error
import pytest
