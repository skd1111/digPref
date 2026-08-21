# services/agent/tests/test_doc_review_fiscal_rules.py
"""财税规则库（FiscalTaxRuleProvider）：加载/切块、风险维度过滤、相关性挑选。"""

import pytest
from agent.config import settings
from agent.doc_review.fiscal_rules import (
    _CACHE,
    FiscalTaxRuleProvider,
    load_candidates,
)
from agent.doc_review.models import RiskType
from agent.doc_review.rules import build_default_rule_provider

_REG_MD = """# 增值税法-摘要

## 1. 进项抵扣
餐饮服务购进的进项税额不得从销项税额中抵扣。虚开增值税专用发票属于违法行为。

## 2. 三流一致
货物流、资金流、发票流应当一致，不一致可能被认定为虚开。
"""

_CASE_MD = """# 虚开案例集

## 案例A 资金回流
受票方资金回流至开票方关联账户，被认定为虚开增值税专用发票。
"""


@pytest.fixture()
def fiscal_dir(tmp_path, monkeypatch):
    """临时财税素材库：regulations 2 章节 + cases 1 章节。"""
    base = tmp_path / "fiscal-tax"
    (base / "regulations").mkdir(parents=True)
    (base / "cases").mkdir(parents=True)
    (base / "regulations" / "增值税法-摘要.md").write_text(_REG_MD, encoding="utf-8")
    (base / "cases" / "虚开案例集.md").write_text(_CASE_MD, encoding="utf-8")
    monkeypatch.setattr(settings, "doc_review_fiscal_dir", str(base))
    _CACHE.clear()
    yield base
    _CACHE.clear()


def test_load_candidates_splits_sections(fiscal_dir):
    candidates = load_candidates()
    assert len(candidates) == 3
    assert all(c.tokens for c in candidates)


def test_missing_dir_returns_empty(tmp_path, monkeypatch):
    # 绝对路径不存在且无 _MEIPASS → best-effort 空列表（绝对路径不触发仓库根兑底）
    monkeypatch.setattr(settings, "doc_review_fiscal_dir", str(tmp_path / "no-such-dir"))
    _CACHE.clear()
    assert load_candidates() == []


async def test_missing_dir_provider_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "doc_review_fiscal_dir", str(tmp_path / "no-such-dir"))
    _CACHE.clear()
    provider = FiscalTaxRuleProvider()
    rules = await provider.get_rules(
        doc_category="other", risk_type=RiskType.COMPLIANCE, sample_text="虚开发票"
    )
    assert rules == []


async def test_risk_type_filtering(fiscal_dir):
    provider = FiscalTaxRuleProvider()
    compliance = await provider.get_rules(
        doc_category="other",
        risk_type=RiskType.COMPLIANCE,
        sample_text="虚开增值税专用发票资金回流进项抵扣",
    )
    financial = await provider.get_rules(
        doc_category="other",
        risk_type=RiskType.FINANCIAL,
        sample_text="虚开增值税专用发票资金回流进项抵扣",
    )
    # cases 只归 compliance；regulations 两个维度都有
    assert len(compliance) >= len(financial)
    assert all(r.risk_type == RiskType.COMPLIANCE for r in compliance)
    assert all(r.risk_type == RiskType.FINANCIAL for r in financial)


async def test_relevance_picks_matching_section(fiscal_dir):
    provider = FiscalTaxRuleProvider()
    rules = await provider.get_rules(
        doc_category="other",
        risk_type=RiskType.FINANCIAL,
        sample_text="公司本月用餐饮服务发票抵扣了进项税额，请审核是否合规",
    )
    assert rules
    assert any("餐饮" in r.content for r in rules)
    assert all(r.rule_id and r.source for r in rules)


async def test_irrelevant_document_gets_no_rules(fiscal_dir):
    provider = FiscalTaxRuleProvider()
    rules = await provider.get_rules(
        doc_category="other",
        risk_type=RiskType.COMPLIANCE,
        sample_text="今天天气很好，我们去公园散步了",
    )
    assert rules == []


async def test_max_rules_cap(fiscal_dir, monkeypatch):
    monkeypatch.setattr(settings, "doc_review_fiscal_max_rules", 1)
    provider = FiscalTaxRuleProvider()
    rules = await provider.get_rules(
        doc_category="other",
        risk_type=RiskType.COMPLIANCE,
        sample_text="虚开发票资金回流进项抵扣三流一致",
    )
    assert len(rules) == 1


async def test_default_provider_is_fiscal():
    assert isinstance(build_default_rule_provider(), FiscalTaxRuleProvider)


# ============================================================
# 混合检索：语义通道（fake embedding 替身，验证零关键词重叠也能命中）
# ============================================================

_SEM_REG_MD = """# 生态补偿办法-摘要

## 1. 海洋生态补偿
海洋生态修复补偿金应当专款专用，不得挪作他用。

## 2. 陆地交通规范
陆地交通工具采购应当履行公开招标程序。
"""


class _FakeEmbedding:
    """替身 embedding：含正向标记词 → e0；其余文本按特征词映射到互相正交的基向量。

    保证「无关键词重叠且语义不相关」的文本对余弦为 0，避免假命中。
    """

    def __init__(self, markers: list[str]) -> None:
        self._markers = markers

    @staticmethod
    def _basis(idx: int) -> list[float]:
        return [1.0 if i == idx else 0.0 for i in range(8)]

    def _vec(self, text: str) -> list[float]:
        if any(m in text for m in self._markers):
            return self._basis(0)
        if "海洋" in text:
            return self._basis(1)
        if "陆地" in text:
            return self._basis(2)
        if "巡游" in text:
            return self._basis(3)
        return self._basis(4)

    async def embed(self, text: str) -> list[float]:
        return self._vec(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


@pytest.fixture()
def semantic_dir(tmp_path, monkeypatch):
    """语义通道专用素材库：章节文本与查询零 bigram 重叠。"""
    base = tmp_path / "fiscal-tax"
    (base / "regulations").mkdir(parents=True)
    (base / "regulations" / "生态补偿办法-摘要.md").write_text(_SEM_REG_MD, encoding="utf-8")
    monkeypatch.setattr(settings, "doc_review_fiscal_dir", str(base))
    _CACHE.clear()
    yield base
    _CACHE.clear()


async def test_semantic_channel_hits_without_keyword_overlap(semantic_dir):
    # 查询词与章节正文零关键词重叠，纯靠语义向量命中「海洋生态补偿」章节
    provider = FiscalTaxRuleProvider(embedding=_FakeEmbedding(["鲸", "海洋"]))
    rules = await provider.get_rules(
        doc_category="other", risk_type=RiskType.COMPLIANCE, sample_text="鲸鱼巡游业务审核"
    )
    assert len(rules) == 1
    assert "海洋生态补偿" in rules[0].source


async def test_semantic_irrelevant_gets_no_rules(semantic_dir):
    # 语义向量也不相关（无任何标记词）→ 与纯关键词退化同样返空
    provider = FiscalTaxRuleProvider(embedding=_FakeEmbedding(["不存在的关键概念"]))
    rules = await provider.get_rules(
        doc_category="other", risk_type=RiskType.COMPLIANCE, sample_text="鲸鱼巡游业务审核"
    )
    assert rules == []


async def test_search_groups_by_risk_type(fiscal_dir):
    provider = FiscalTaxRuleProvider()
    grouped = await provider.search("虚开增值税专用发票资金回流进项抵扣")
    assert RiskType.COMPLIANCE in grouped
    assert RiskType.FINANCIAL in grouped
    assert RiskType.LEGAL not in grouped  # 财税素材不覆盖 legal 维度
    assert all(r.risk_type == rt for rt, rules in grouped.items() for r in rules)


# ============================================================
# BM25 特性：IDF 区分度、归一化上界、TF 饱和
# ============================================================

_BM25_REG_MD = """# 测试法-摘要

## 1. 罕见条款
魁测情形的特别规定。

## 2. 常规条款
发票相关的一般规定。

## 3. 堆砌条款
发票发票发票发票发票发票发票发票。
"""


@pytest.fixture()
def bm25_dir(tmp_path, monkeypatch):
    """BM25 特性专用语料：「发票」两个章节都有（低 IDF），「魁测」仅 §1（高 IDF）。"""
    base = tmp_path / "fiscal-tax"
    (base / "regulations").mkdir(parents=True)
    (base / "regulations" / "测试法-摘要.md").write_text(_BM25_REG_MD, encoding="utf-8")
    monkeypatch.setattr(settings, "doc_review_fiscal_dir", str(base))
    _CACHE.clear()
    yield base
    _CACHE.clear()


async def test_bm25_rare_term_dominates_ranking(bm25_dir):
    # 查询同时含罕见词（魁测，仅 §1，高 IDF）与常见词（发票，两章节都有，低 IDF）：
    # 只命中罕见词的 §1 应排在只命中常见词的 §2/§3 之前（即便 §3 词频更高）
    provider = FiscalTaxRuleProvider()
    rules = await provider.get_rules(
        doc_category="other", risk_type=RiskType.COMPLIANCE, sample_text="魁测发票"
    )
    assert rules
    assert "罕见条款" in rules[0].source


def test_bm25_scores_bounded_and_tf_saturates(bm25_dir):
    from agent.doc_review.fiscal_rules import _bm25_scores, _load_corpus, _tokenize

    corpus, stats = _load_corpus()
    assert stats is not None and stats.n_docs == 3
    scores = _bm25_scores(set(_tokenize("发票")), corpus, stats)
    # 归一化到 [0,1]：任何章节得分不超 1，无命中为 0
    assert all(0.0 <= s <= 1.0 for s in scores)
    assert scores[0] == 0.0  # §1 不含发票
    assert scores[1] > 0.0 and scores[2] > 0.0
    # TF 饱和：§3 把「发票」重复 8 遍，得分只比 §2（1 遍）高一点而非 8 倍
    assert scores[2] > scores[1]
    assert scores[2] < 3.0 * scores[1]
