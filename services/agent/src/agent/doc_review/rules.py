"""规则扩展口子：V0 Noop，V1 接规则清单 / 制度文档。"""
from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from agent.doc_review.models import RiskType


class PolicyRule(BaseModel):
    rule_id: str
    source: str
    risk_type: RiskType
    content: str
    severity_hint: str | None = None


class RuleProvider(Protocol):
    async def get_rules(self, *, doc_category: str, risk_type: RiskType) -> list[PolicyRule]: ...


class NoopRuleProvider:
    """V0 空实现：模型自主判断。V1 改为从规则清单 / 制度文档加载。"""

    async def get_rules(self, *, doc_category: str, risk_type: RiskType) -> list[PolicyRule]:
        return []


def build_default_rule_provider() -> RuleProvider:
    return NoopRuleProvider()
