# services/agent/tests/test_doc_review_knowledge.py
"""doc_review · knowledge（知识库 grep 引用）单元测试。"""

from __future__ import annotations

import pytest
from agent.doc_review import knowledge
from agent.doc_review.knowledge import KbCitation, find_kb_refs


@pytest.fixture()
def kb_dir(tmp_path):
    """构造最小知识库：合规风险文件 + 案例库。"""
    (tmp_path / "01-合规风险.md").write_text(
        """# 01 合规风险

## 一、合同格式条款 / 霸王条款

### 1.2 条款红线清单

| 风险点名称 | 法律依据 |
| 单方最终解释权 | 《合同行政监督管理办法》第8条第7项；"本公司对本协议享有最终解释权"属违规表述 |

### 1.3 机器可识别信号

最终解释权、一律免责、单方变更解除权属于高危信号。

## 二、招投标合规

串通投标、围标行为违反《招标投标法》。
""",
        encoding="utf-8",
    )
    (tmp_path / "90-案例库.md").write_text(
        """# 90 案例库

## [合规风险] 案例 2：浙江省市场监管局通报18条霸王条款

- 案由：医疗美容、运动健身等行业的格式条款违规，含最终解释权条款
- 处罚结果：行政约谈；清除霸王条款179条
""",
        encoding="utf-8",
    )
    knowledge._CACHE.clear()
    yield tmp_path
    knowledge._CACHE.clear()


def test_find_kb_refs_matches_relevant_sections(kb_dir):
    refs = find_kb_refs(
        risk_type="compliance",
        title="单方最终解释权条款",
        description="合同约定本公司对本协议享有最终解释权",
        evidence_text="本公司对本协议享有最终解释权",
        kb_dir=str(kb_dir),
    )
    assert len(refs) >= 1
    assert all(isinstance(r, KbCitation) for r in refs)
    # 风险清单命中：来源应提取命中处附近的真实法规名（不是文件名）
    kb_hits = [r for r in refs if "合同行政监督管理办法" in r.source]
    assert kb_hits, f"expected 法规名 citation, got {refs}"
    assert any("最终解释权" in r.excerpt for r in kb_hits)
    # 命中词可能来自 title/description/evidence 任一处，在全部引用内检查
    assert any("解释权" in t for r in refs for t in r.matched_terms)
    # 任何引用的来源都不允许是内部文件名（xxx.md）
    assert all(not r.source.endswith(".md") for r in refs)


def test_find_kb_refs_case_book_included(kb_dir):
    refs = find_kb_refs(
        risk_type="compliance",
        title="霸王条款最终解释权",
        evidence_text="格式条款违规含最终解释权，属于霸王条款",
        kb_dir=str(kb_dir),
        max_refs=3,
    )
    sources = {r.source for r in refs}
    # 来源为真实法规名或友好模块名（案例库无《》时用"风险案例库"兜底）
    assert any(
        "合同行政监督管理办法" in s or s in ("风险案例库", "合规风险红线清单") for s in sources
    )
    assert all(not s.endswith(".md") for s in sources)


def test_find_kb_refs_missing_dir_returns_empty(tmp_path):
    refs = find_kb_refs(
        risk_type="compliance",
        title="最终解释权",
        kb_dir=str(tmp_path / "not-exist"),
    )
    assert refs == []


def test_find_kb_refs_empty_text_returns_empty(kb_dir):
    refs = find_kb_refs(risk_type="compliance", title="", kb_dir=str(kb_dir))
    assert refs == []


def test_risk_type_file_mapping_isolated(kb_dir):
    # financial 映射到 04-资金风险.md（本 fixture 不存在）→ 仅剩案例库；
    # 案例库内容为合规案例，financial 查询词不命中 → 空；
    # 若命中则来源为友好模块名"风险案例库"（不暴露文件名）
    refs = find_kb_refs(
        risk_type="financial",
        title="背靠背付款条款中小企业款项支付",
        kb_dir=str(kb_dir),
    )
    assert all(r.source == "风险案例库" for r in refs)


def test_section_split_keeps_heading_path(kb_dir):
    sections = knowledge._load_sections(kb_dir, ["01-合规风险.md"])["01-合规风险.md"]
    headings = [s.heading for s in sections]
    assert any("1.2 条款红线清单" in h for h in headings)
    # 标题路径应包含上级 ## 标题
    assert any(h.startswith("一、合同格式条款") for h in headings)


def test_resolve_kb_dir_falls_back_to_meipass(tmp_path, monkeypatch):
    """cwd 无知识库时回退 PyInstaller _MEIPASS 内置副本。"""
    bundled = tmp_path / "bundle" / "knowledge-base"
    bundled.mkdir(parents=True)
    monkeypatch.setattr(knowledge.sys, "_MEIPASS", str(tmp_path / "bundle"), raising=False)
    monkeypatch.chdir(tmp_path)  # cwd 下无 knowledge-base
    resolved = knowledge._resolve_kb_dir(None)
    assert resolved == bundled


def test_resolve_kb_dir_prefers_cwd(kb_dir, monkeypatch, tmp_path):
    """工作目录存在知识库时优先使用，不回退 _MEIPASS。"""
    monkeypatch.chdir(kb_dir.parent)
    monkeypatch.setattr(knowledge.sys, "_MEIPASS", str(tmp_path / "nope"), raising=False)
    settings_dir = kb_dir.name
    monkeypatch.setattr(knowledge.settings, "doc_review_kb_dir", settings_dir)
    resolved = knowledge._resolve_kb_dir(None)
    assert resolved.resolve() == kb_dir.resolve()
