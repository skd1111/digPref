"""test_ops_doc_audit.py —— 专家审核前置的系统合规审核桥接（2026-11-05）。

覆盖：纯文本切块偏移、分类+分块分析+证据定位、无文本/模型失败降级、
以及审核提示词参考的生成（无风险不注入）。
"""

from __future__ import annotations

import json

import pytest
from agent.config import settings
from agent.ops.doc_audit import _build_parsed, run_doc_audit

_FINDINGS = [
    {
        "risk_type": "legal",
        "risk_level": "critical",
        "title": "单方最终解释权",
        "description": "条款剥夺相对方解释权，存在被认定无效的风险",
        "suggestion": "删除该条或改为双方协商解释",
        "evidence_text": "甲方享有最终解释权",
    },
    {
        "risk_type": "financial",
        "risk_level": "medium",
        "title": "违约金过高",
        "description": "违约金比例明显高于实际损失",
        "suggestion": "调整至合理比例",
        "evidence_text": "违约金为合同金额的300%",
    },
]


def _fake_llm(findings: list[dict]):
    """按 doc_classify / doc_analyze 两类任务返回合法 JSON（不发网络请求）。"""

    async def _call(kind: str, _prompt: str) -> str:
        if kind == "doc_classify":
            return json.dumps(
                {
                    "doc_category": "contract",
                    "risk_types": ["legal", "financial"],
                    "reason": "合同文本",
                    "confidence": {"legal": 0.9},
                },
                ensure_ascii=False,
            )
        return json.dumps({"findings": findings}, ensure_ascii=False)

    return _call


def test_build_parsed_offsets_align_full_text():
    text = "第一行\n第二行\n\n第四行"
    parsed = _build_parsed("合同.txt", text)
    blocks = parsed.pages[0].blocks
    # full_text 必须与 blocks 以 \n 连接结果一致，locate_positions 才能定位
    assert parsed.full_text == text == "\n".join(b.text for b in blocks)
    assert blocks[0].start == 0
    assert blocks[1].start == len("第一行") + 1


@pytest.mark.asyncio
async def test_run_doc_audit_returns_findings_with_located_evidence(monkeypatch):
    """与审核模块同一套分析：风险点按等级排序，原文证据能定位回块。"""
    monkeypatch.setattr(settings, "rag_enabled", False)
    text = "甲方享有最终解释权\n违约金为合同金额的300%"
    report = await run_doc_audit("合同.txt", text, llm=_fake_llm(_FINDINGS))

    assert report["audited"] is True
    assert report["degraded"] is False
    assert report["doc_category"] == "contract"
    assert report["overall_risk_level"] == "critical"
    # critical 排在 medium 前
    assert [f["risk_level"] for f in report["findings"]] == ["critical", "medium"]
    first = report["findings"][0]
    assert first["evidence_text"] == "甲方享有最终解释权"
    assert first["positions"], "原文证据必须定位回块（弹窗要附原文依据）"
    assert first["positions"][0]["page_no"] == 1
    assert first["kb_refs"] == []


@pytest.mark.asyncio
async def test_run_doc_audit_empty_text_degrades(monkeypatch):
    monkeypatch.setattr(settings, "rag_enabled", False)
    report = await run_doc_audit("空.txt", "   \n ")
    assert report["audited"] is False
    assert report["degraded"] is True
    assert report["findings"] == []


@pytest.mark.asyncio
async def test_run_doc_audit_classify_failure_degrades(monkeypatch):
    """模型不可用只降级（不弹窗），绝不抛异常打断专家验收。"""
    monkeypatch.setattr(settings, "rag_enabled", False)

    async def _boom(_kind: str, _prompt: str) -> str:
        raise RuntimeError("llm down")

    report = await run_doc_audit("合同.txt", "正文", llm=_boom)
    assert report["audited"] is False
    assert report["degraded"] is True
    assert "合规分类失败" in report["error"]


def test_format_audit_hint_only_when_risky():
    from agent.ops.api import _format_audit_hint

    assert _format_audit_hint({"findings": []}) == ""
    hint = _format_audit_hint(
        {"overall_risk_level": "high", "findings": [{"risk_level": "high", "title": "单方解释权"}]}
    )
    assert "系统合规审核参考" in hint
    assert "单方解释权" in hint
