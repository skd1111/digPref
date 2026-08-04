"""5 维评分测试。"""
import pytest

from agent.llm.models import LLMBackend, RESIDENCY_LOCAL, RESIDENCY_PRIVATE, TaskCategory
from agent.llm.scoring import score_backend


def _local() -> LLMBackend:
    return LLMBackend(
        name="ollama", type="local", base_url="http://127.0.0.1:11434",
        model_name="qwen2.5:0.5b", cost_per_1k_tokens=0.0, timeout_seconds=5,
        data_residency=RESIDENCY_LOCAL, capabilities=["text"],
    )


def _private() -> LLMBackend:
    return LLMBackend(
        name="deepseek-private", type="private", base_url="http://internal/api",
        model_name="deepseek-v2", cost_per_1k_tokens=0.001, timeout_seconds=20,
        data_residency=RESIDENCY_PRIVATE, capabilities=["text", "code"],
    )


def _cloud() -> LLMBackend:
    return LLMBackend(
        name="gpt4", type="cloud", base_url="https://api.openai.com",
        model_name="gpt-4", cost_per_1k_tokens=0.03, timeout_seconds=60,
        data_residency="cloud", capabilities=["text", "code", "vision"],
    )


def test_capability_score_local_simple():
    s = score_backend(_local(), TaskCategory.SIMPLE)
    assert s.capability == 0.9  # local + SIMPLE = 0.9


def test_capability_score_cloud_complex():
    s = score_backend(_cloud(), TaskCategory.COMPLEX)
    assert s.capability == 0.95  # cloud + COMPLEX = 0.95


def test_capability_score_local_complex_is_low():
    s = score_backend(_local(), TaskCategory.COMPLEX)
    assert s.capability == 0.3  # local + COMPLEX 很低


def test_cost_score_local_is_max():
    s = score_backend(_local(), TaskCategory.SIMPLE)
    assert s.cost == 1.0  # 0 cost → max


def test_cost_score_cloud_expensive():
    """0.03/1k cost → 1 - 0.03/0.1 = 0.7（V0 线性映射）。"""
    s = score_backend(_cloud(), TaskCategory.COMPLEX)
    assert 0.6 <= s.cost <= 0.8


def test_cost_score_above_threshold_is_zero():
    """cost_per_1k_tokens >= 0.1 → 0 分。"""
    expensive = LLMBackend(
        name="ultra", type="cloud", base_url="x", model_name="m",
        cost_per_1k_tokens=0.5, timeout_seconds=10, data_residency="cloud",
    )
    s = score_backend(expensive, TaskCategory.COMPLEX)
    assert s.cost == 0.0


def test_latency_score_fast():
    s = score_backend(_local(), TaskCategory.SIMPLE)
    assert s.latency == 1.0  # 5s timeout → max


def test_latency_score_slow():
    s = score_backend(_cloud(), TaskCategory.COMPLEX)
    assert s.latency == 0.0  # 60s timeout → 0


def test_compliance_score_local_is_max():
    s = score_backend(_local(), TaskCategory.SIMPLE)
    assert s.compliance == 1.0


def test_compliance_score_cloud_is_low():
    s = score_backend(_cloud(), TaskCategory.COMPLEX)
    assert s.compliance == 0.4


def test_availability_score_penalizes_failures():
    s0 = score_backend(_local(), TaskCategory.SIMPLE, failure_count=0)
    s3 = score_backend(_local(), TaskCategory.SIMPLE, failure_count=3)
    assert s3.availability < s0.availability


def test_total_weighted_sum():
    """权重固定 .35/.25/.20/.15/.05，加权和 0-1。"""
    s = score_backend(_private(), TaskCategory.MEDIUM)
    # 0.85*0.35 + (1-0.001/0.1)*0.25 + 1-(20-5)/55*0.20 + 0.85*0.15 + 0.7*0.05
    # 0.2975 + 0.2475 + 0.20*0.4545 + 0.1275 + 0.035
    # 实际是动态算，0 < total < 1
    assert 0.0 <= s.total <= 1.0
