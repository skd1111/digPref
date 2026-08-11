"""test_prompt_assets -- prompt template loader and renderer (spec 4.4)."""

from __future__ import annotations

import pytest
from agent.llm.prompts import load_prompt, render_prompt


def test_load_prompt_rejects_traversal():
    with pytest.raises(ValueError):
        load_prompt("../../etc/passwd")
    with pytest.raises(ValueError):
        load_prompt("/etc/passwd")


def test_render_prompt_replaces_double_brace():
    out = render_prompt("你好 {{NAME}}", NAME="世界")
    assert out == "你好 世界"


def test_render_prompt_keeps_json_braces():
    template = 'output {"a": 1} and user {{USER}}'
    out = render_prompt(template, USER="u")
    assert '{"a": 1}' in out
    assert "u" in out


TEMPLATES = [
    "intent",
    "planner",
    "repair",
    "summarise",
    "system",
    "spark_reasoning",
    "spark_execution",
    "judge",
    "decompose",
    "subagent_execution",
    "tool_orchestrate",
    "biznav/extract",
    "codenav/infer",
    "codenav/explain",
    "approval/options",
    "loganalysis/root_cause",
    "skills/classify",
    "nl2sql/generate",
    "orchestrator/eval_judge",
]
LEGACY_TEMPLATES = frozenset(
    {
        "intent",
        "planner",
        "repair",
        "summarise",
        "system",
        "decompose",
        "subagent_execution",
        "tool_orchestrate",
    }
)
REQUIRED_HEADERS = ("## 角色", "## 任务", "## 输出格式", "## 硬性约束")


@pytest.mark.parametrize("name", TEMPLATES)
def test_prompt_template_exists_and_has_headers(name):
    text = load_prompt(name)
    assert text.strip(), f"{name} 为空"
    if name in LEGACY_TEMPLATES:
        return
    for header in REQUIRED_HEADERS:
        assert header in text, f"{name} 缺少段落 {header}"


def test_prompts_module_constants_match_files():
    import agent.prompts as p

    assert load_prompt("decompose") == p.SUBAGENT_ENABLEMENT_DECISION_PROMPT
    assert load_prompt("subagent_execution") == p.SUBAGENT_EXECUTION_PROMPT_TEMPLATE
    assert load_prompt("tool_orchestrate") == p.DYNAMIC_TOOL_ORCHESTRATOR_PROMPT


def test_load_prompt_subdir_works():
    assert "功能点" in load_prompt("biznav/extract")
