# services/agent/tests/test_audit_doc_ranking.py
"""audit_doc.py（V2 简化路径）rank_chunks 的 BM25 升级回归。"""

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from audit_doc import load_regulations, rank_chunks  # type: ignore[import-not-found]

# audit_doc 的 _tokenize 会额外保留整串 token（与 fiscal_rules 不同），
# 未命中章节会以 0 分进入 top_k；断言按非零命中项写。

_REG_MD = """# 测试法-摘要

## 1. 罕见条款
魁测情形的特别规定。

## 2. 常规条款
发票相关的一般规定。

## 3. 堆砌条款
发票发票发票发票发票发票发票发票。
"""


@pytest.fixture()
def regs_dir(tmp_path):
    d = tmp_path / "regulations"
    d.mkdir()
    (d / "测试法-摘要.md").write_text(_REG_MD, encoding="utf-8")
    return d


def test_rare_term_ranks_first(regs_dir):
    regs = load_regulations(regs_dir)
    chunks = [c for r in regs for c in r.chunks]
    ranked = rank_chunks("魁测发票", chunks, top_k=3)
    assert ranked
    assert "罕见条款" in ranked[0][0].section_title


def test_scores_normalized_and_zero_for_unrelated(regs_dir):
    regs = load_regulations(regs_dir)
    chunks = [c for r in regs for c in r.chunks]
    ranked = rank_chunks("发票", chunks, top_k=3)
    hits = [s for _, s in ranked if s > 0]
    assert hits and all(0.0 < score <= 1.0 for score in hits)
    # 与语料零重叠 → 空结果（旧实现返全零 top_k，新实现直接返空）
    assert rank_chunks("鲸鱼巡游海洋", chunks, top_k=3) == []


def test_tf_saturation(regs_dir):
    regs = load_regulations(regs_dir)
    chunks = [c for r in regs for c in r.chunks]
    ranked = {c.section_title: s for c, s in rank_chunks("发票", chunks, top_k=3)}
    # 「发票」重复 8 遍的堆砌条款只比出现 1 遍的常规条款略高，而非 8 倍
    assert ranked["3. 堆砌条款"] > ranked["2. 常规条款"]
    assert ranked["3. 堆砌条款"] < 3.0 * ranked["2. 常规条款"]
