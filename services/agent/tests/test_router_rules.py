"""硬规则测试（CLAUDE.md §2 红线护栏）。"""
import pytest

from agent.llm.models import LLMBackend, RESIDENCY_CLOUD, RESIDENCY_LOCAL, RESIDENCY_PRIVATE, Sensitivity
from agent.llm.rules import apply_hard_rules, _LOCAL_ONLY_TASKS


def _b(name: str, residency: str, enabled: bool = True) -> LLMBackend:
    return LLMBackend(
        name=name, type=residency, base_url="x", model_name="m",
        data_residency=residency, enabled=enabled,
    )


def test_local_only_task_excludes_cloud_and_private():
    """_LOCAL_ONLY_TASKS（如 intent/repair）必须仅 local 可承接。"""
    candidates = [_b("ollama", RESIDENCY_LOCAL), _b("deepseek", RESIDENCY_PRIVATE), _b("gpt4", RESIDENCY_CLOUD)]
    passed = apply_hard_rules(candidates, task_kind="intent", sensitivity=Sensitivity.PUBLIC)
    assert [b.name for b in passed] == ["ollama"]


def test_pii_excludes_cloud():
    candidates = [_b("ollama", RESIDENCY_LOCAL), _b("deepseek", RESIDENCY_PRIVATE), _b("gpt4", RESIDENCY_CLOUD)]
    passed = apply_hard_rules(candidates, task_kind="plan", sensitivity=Sensitivity.PII)
    assert "gpt4" not in [b.name for b in passed]


def test_production_excludes_local_and_cloud():
    candidates = [_b("ollama", RESIDENCY_LOCAL), _b("deepseek", RESIDENCY_PRIVATE), _b("gpt4", RESIDENCY_CLOUD)]
    passed = apply_hard_rules(candidates, task_kind="plan", sensitivity=Sensitivity.PRODUCTION)
    assert [b.name for b in passed] == ["deepseek"]


def test_disabled_backend_excluded():
    candidates = [_b("ollama", RESIDENCY_LOCAL, enabled=False), _b("deepseek", RESIDENCY_PRIVATE)]
    passed = apply_hard_rules(candidates, task_kind="plan", sensitivity=Sensitivity.PUBLIC)
    assert "ollama" not in [b.name for b in passed]


def test_public_allows_everything():
    candidates = [_b("ollama", RESIDENCY_LOCAL), _b("deepseek", RESIDENCY_PRIVATE), _b("gpt4", RESIDENCY_CLOUD)]
    passed = apply_hard_rules(candidates, task_kind="plan", sensitivity=Sensitivity.PUBLIC)
    assert len(passed) == 3


def test_local_only_tasks_constant():
    """_LOCAL_ONLY_TASKS 包含 intent + repair（CLAUDE.md §2 红线）。"""
    assert "intent" in _LOCAL_ONLY_TASKS
    assert "repair" in _LOCAL_ONLY_TASKS
