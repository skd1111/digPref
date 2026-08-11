"""Multi-agent orchestration prompts (Phase 12 V2) - assets externalized to .md.

Prompt asset migration (2026-08-05):
    - SUBAGENT_ENABLEMENT_DECISION_PROMPT  <- llm/prompts/decompose.md
    - SUBAGENT_EXECUTION_PROMPT_TEMPLATE   <- llm/prompts/subagent_execution.md
    - DYNAMIC_TOOL_ORCHESTRATOR_PROMPT     <- llm/prompts/tool_orchestrate.md

Module-level constants are kept for import compatibility (router and
context_strategy). Non-engineers can edit the .md files directly; see
docs/prompt-templates.md.
"""

from __future__ import annotations

from agent.llm.prompts import load_prompt

SUBAGENT_ENABLEMENT_DECISION_PROMPT = load_prompt("decompose")
SUBAGENT_EXECUTION_PROMPT_TEMPLATE = load_prompt("subagent_execution")
DYNAMIC_TOOL_ORCHESTRATOR_PROMPT = load_prompt("tool_orchestrate")
