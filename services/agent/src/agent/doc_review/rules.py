"""规则扩展口子：V0 Noop，V1 接财税法规素材库（FiscalTaxRuleProvider）。"""

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
    async def get_rules(
        self, *, doc_category: str, risk_type: RiskType, sample_text: str = ""
    ) -> list[PolicyRule]: ...

    async def search(self, sample_text: str) -> dict[RiskType, list[PolicyRule]]: ...


class NoopRuleProvider:
    """空实现：模型自主判断（财税素材目录缺失时的退化形态）。"""

    async def get_rules(
        self, *, doc_category: str, risk_type: RiskType, sample_text: str = ""
    ) -> list[PolicyRule]:
        return []

    async def search(self, sample_text: str) -> dict[RiskType, list[PolicyRule]]:
        return {}


def build_default_rule_provider() -> RuleProvider:
    """默认 provider：财税规则库（素材目录不存在时自行退化为返回空）。"""
    from agent.doc_review.fiscal_rules import FiscalTaxRuleProvider

    return FiscalTaxRuleProvider()
