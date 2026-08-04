# services/agent/tests/test_doc_review_rules.py
from agent.doc_review.models import RiskType
from agent.doc_review.rules import NoopRuleProvider, PolicyRule, build_default_rule_provider


async def test_noop_returns_empty():
    provider = build_default_rule_provider()
    rules = await provider.get_rules(doc_category="contract", risk_type=RiskType.LEGAL)
    assert rules == []


def test_policy_rule_validation():
    r = PolicyRule(rule_id="r1", source="制度A", risk_type="legal", content="违约金≤30%")
    assert r.risk_type == RiskType.LEGAL
    assert isinstance(NoopRuleProvider(), object)
