"""运营模式材料的系统合规审核（专家审核前置，2026-11-05）。

复用 doc_review 模块的「分类 → 分块风险分析 → 证据定位 → 知识库依据」全链路：
  - 有风险点：随材料保存结构化报告，前端弹「审核报告」（风险点 + 原文证据 + 处理建议）
  - 无风险点：报告 findings=[]，前端不弹窗（仍落库，供重审追溯）

设计取舍：
  - 提示词 / 风险维度 / 去重 / 证据定位与「审核专家 · 文档审核」完全一致（同一套
    classify_document / analyze_document），不另写一套轻量规则；
  - 模型调用由调用方注入（运营侧传本地优先三级降级链），材料含 PII，本地可用绝不出域；
  - 任何失败 / 超时都降级为 degraded 报告（findings 为空 → 不弹窗），绝不阻断专家验收。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from agent.config import settings
from agent.doc_review.analyzer import analyze_document
from agent.doc_review.classifier import classify_document
from agent.doc_review.llm import LLMFunc
from agent.doc_review.models import (
    RISK_ORDER,
    Block,
    DocFormat,
    Page,
    ParsedDocument,
    generate_id,
)

logger = logging.getLogger(__name__)

# 单份材料最多保留的风险点（前端弹窗展示上限，防超长合同刷屏）
_MAX_FINDINGS = 50


def _build_parsed(file_name: str, text: str) -> ParsedDocument:
    """纯文本 → ParsedDocument（按行切块，保留全文偏移，供原文证据定位）。

    切块方式与 doc_review.parser._assemble 对齐：block.start/end 以 ``\\n`` 连接为
    full_text，locate_positions 才能把 evidence_text 准确定位回块。
    """
    lines = text.split("\n")
    blocks: list[Block] = []
    offset = 0
    for i, line in enumerate(lines):
        blocks.append(
            Block(block_id=f"p1b{i + 1}", text=line, start=offset, end=offset + len(line))
        )
        offset += len(line) + 1
    if not blocks:
        blocks = [Block(block_id="p1b1", text=text, start=0, end=len(text))]
    return ParsedDocument(
        doc_id=generate_id(),
        file_name=file_name,
        file_path="",
        format=DocFormat.TXT,
        page_count=1,
        pages=[Page(page_no=1, blocks=blocks)],
        full_text=text,
    )


def _empty_report(*, degraded: bool, error: str = "", basis: str = "document") -> dict[str, Any]:
    """无风险 / 降级报告（findings 为空 → 前端不弹窗）。"""
    return {
        "audited": False,
        "degraded": degraded,
        "error": error,
        "basis": basis,
        "doc_category": "",
        "risk_types": [],
        "overall_risk_level": "",
        "findings": [],
        "elapsed_ms": 0,
        "audited_at": int(time.time()),
    }


async def run_doc_audit(
    file_name: str,
    text: str,
    *,
    basis: str = "document",
    llm: LLMFunc | None = None,
) -> dict[str, Any]:
    """材料文本 → 系统合规审核报告（与「审核专家 · 文档审核」同一套分析逻辑）。

    返回结构（前端 OpsRiskReport 镜像）::

        {"audited": True, "degraded": False, "basis": "document",
         "doc_category": "contract", "risk_types": ["legal"], "overall_risk_level": "high",
         "findings": [{finding_id, risk_type, risk_level, title, description, suggestion,
                       rule_ref, evidence_text, positions, kb_refs}],
         "elapsed_ms": 1234, "audited_at": 1760000000, "error": ""}
    """
    started = time.perf_counter()
    if not settings.ops_doc_audit_enabled:
        return _empty_report(degraded=False, basis=basis)

    text = (text or "").strip()
    if not text:
        return _empty_report(degraded=True, error="材料无可审核文本", basis=basis)
    truncated = text[: settings.ops_doc_audit_max_chars]
    parsed = _build_parsed(file_name, truncated)

    # 1) 分类（文档类型 + 风险维度），与审核模块同一提示词
    try:
        classification = await classify_document(
            file_name=file_name,
            sample_text=truncated,
            max_chars=settings.doc_review_classify_max_chars,
            llm=llm,
        )
    except Exception as exc:
        logger.warning("[ops-audit] classify failed file=%s: %s", file_name, exc)
        return _empty_report(degraded=True, error=f"合规分类失败：{exc}", basis=basis)

    # 2) 知识库依据注入（best-effort，与 doc_review 的 RAG 召回一致）
    rag_context = ""
    if settings.rag_enabled:
        try:
            from agent.knowledge.retriever import get_default_retriever

            rag_ctx = await get_default_retriever().retrieve(
                truncated[: settings.doc_review_classify_max_chars],
                top_k=settings.rag_top_k,
            )
            rag_context = rag_ctx.formatted_prompt or ""
        except Exception:
            logger.debug("[ops-audit] rag retrieve skipped", exc_info=True)

    # 3) 风险维度 × 分块并发分析 + 证据定位
    try:
        findings = await analyze_document(
            parsed=parsed,
            classification=classification,
            rules=[],
            chunk_max_chars=settings.doc_review_chunk_max_chars,
            chunk_overlap=settings.doc_review_chunk_overlap,
            rag_context=rag_context,
            llm=llm,
        )
    except Exception as exc:
        logger.warning("[ops-audit] analyze failed file=%s: %s", file_name, exc)
        return _empty_report(degraded=True, error=f"合规分析失败：{exc}", basis=basis)

    dicts: list[dict[str, Any]] = [
        {
            "finding_id": f.finding_id,
            "risk_type": f.risk_type.value,
            "risk_level": f.risk_level.value,
            "title": f.title,
            "description": f.description,
            "suggestion": f.suggestion,
            "rule_ref": f.rule_ref,
            "evidence_text": f.evidence_text,
            "positions": [p.model_dump() for p in f.positions],
            "kb_refs": [],
        }
        for f in findings
    ]
    # 4) 知识库依据（best-effort；失败只影响「为什么有风险」的参考展示）
    try:
        from agent.doc_review.api import _attach_kb_refs

        await _attach_kb_refs(dicts)
    except Exception:
        logger.debug("[ops-audit] kb refs attach skipped", exc_info=True)

    dicts.sort(key=lambda f: RISK_ORDER.get(str(f.get("risk_level", "")), -1), reverse=True)
    overall = max(
        (str(f.get("risk_level", "")) for f in dicts),
        key=lambda lv: RISK_ORDER.get(lv, -1),
        default="",
    )
    report = {
        "audited": True,
        "degraded": False,
        "error": "",
        "basis": basis,
        "doc_category": classification.doc_category.value,
        "risk_types": [rt.value for rt in classification.risk_types],
        "overall_risk_level": overall,
        "findings": dicts[:_MAX_FINDINGS],
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "audited_at": int(time.time()),
    }
    logger.info(
        "[ops-audit] done file=%s category=%s findings=%d overall=%s elapsed_ms=%d",
        file_name,
        report["doc_category"],
        len(dicts),
        overall,
        report["elapsed_ms"],
    )
    return report
