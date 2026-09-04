"""规则模型 + 扩展口子（Noop）。

2026-09-04：审核依据统一改走用户上传的 RAG 知识库，内置财税规则库（FiscalTaxRuleProvider）
已下线；本模块仅保留 PolicyRule 数据模型（analyzer 的 {{rules}} 槽位类型）与 Noop 口子。
"""

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
    """空实现：模型自主判断（内置财税规则库下线后的默认形态）。"""

    async def get_rules(
        self, *, doc_category: str, risk_type: RiskType, sample_text: str = ""
    ) -> list[PolicyRule]:
        return []

    async def search(self, sample_text: str) -> dict[RiskType, list[PolicyRule]]:
        return {}


def build_default_rule_provider() -> RuleProvider:
    """默认 provider：Noop（依据已改走上传 RAG，不再注入内置财税规则）。"""
    return NoopRuleProvider()
