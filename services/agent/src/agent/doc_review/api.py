"""文档风险合规审核 —— FastAPI 路由。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from agent.config import settings
from agent.doc_review.analyzer import analyze_document, build_analysis_result
from agent.doc_review.classifier import classify_document
from agent.doc_review.events import (
    EVT_DOC_REVIEW_CLASSIFIED,
    EVT_DOC_REVIEW_FAILED,
    EVT_DOC_REVIEW_FINDINGS_READY,
    EVT_DOC_REVIEW_STARTED,
    emit_event_sync,
)
from agent.doc_review.models import ParsedDocument, generate_id
from agent.doc_review.parser import DocParseError, parse_document
from agent.doc_review.rules import build_default_rule_provider
from agent.doc_review.storage import DocReviewStorage, get_default_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/doc-review", tags=["doc-review"])

_background_tasks: set[asyncio.Task[Any]] = set()

# 分析进度（run_id → 0..1）：内存态，供 /status 轮询；Agent 重启归零不影响主流程
_run_progress: dict[str, float] = {}


def _attach_kb_refs(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为每条 finding 附加知识库引用 kb_refs（grep 式匹配，best-effort）。

    带耗时日志：用于对比"模型返回慢 vs 知识库 grep 慢"。
    """
    if not findings:
        return findings
    t0 = time.perf_counter()
    refs_total = 0
    try:
        from agent.doc_review.knowledge import find_kb_refs

        for item in findings:
            try:
                refs = find_kb_refs(
                    risk_type=str(item.get("risk_type", "")),
                    title=str(item.get("title", "")),
                    description=str(item.get("description", "") or ""),
                    evidence_text=str(item.get("evidence_text", "") or ""),
                )
                item["kb_refs"] = [r.model_dump() for r in refs]
                refs_total += len(refs)
            except Exception as exc:
                logger.warning("doc_review kb_refs attach failed: %s", exc)
                item["kb_refs"] = []
    except Exception as exc:
        logger.warning("doc_review kb module unavailable: %s", exc)
        for item in findings:
            item["kb_refs"] = []
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "doc_review kb_refs attached: findings=%d refs=%d elapsed=%.1fms",
        len(findings),
        refs_total,
        elapsed_ms,
    )
    return findings


class RegisterRequest(BaseModel):
    file_path: str = Field(min_length=1, max_length=4096)


@router.post("/documents")
async def register_document(req: RegisterRequest) -> dict[str, Any]:
    try:
        parsed = parse_document(req.file_path)
    except DocParseError as exc:
        if str(exc).startswith("不支持的格式"):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    storage = get_default_storage()
    await storage.insert_document(parsed)
    try:
        from agent.audit.store import audit

        await audit(
            "doc_review.document_imported",
            {"doc_id": parsed.doc_id, "file_name": parsed.file_name, "format": parsed.format.value},
        )
    except Exception:
        logger.warning("doc_review audit write failed", exc_info=True)
    return {"doc_id": parsed.doc_id, "file_name": parsed.file_name, "page_count": parsed.page_count}


@router.get("/documents")
async def list_documents() -> list[dict[str, Any]]:
    return await get_default_storage().list_documents()


@router.get("/documents/{doc_id}")
async def get_document(doc_id: str) -> dict[str, Any]:
    storage = get_default_storage()
    doc = await storage.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"doc not found: {doc_id}")
    latest = await storage.latest_run(doc_id)
    doc["status"] = latest["status"] if latest else "none"
    doc["overall_risk_level"] = latest["overall_risk_level"] if latest else None
    doc["doc_category"] = latest["doc_category"] if latest else None
    doc["risk_types"] = latest["risk_types"] if latest else []
    doc["summary"] = latest["summary"] if latest else None
    doc["findings"] = _attach_kb_refs(await storage.list_findings(doc_id))
    return doc


@router.post("/documents/{doc_id}/analyze")
async def analyze(doc_id: str) -> dict[str, Any]:
    storage = get_default_storage()
    doc = await storage.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"doc not found: {doc_id}")
    latest = await storage.latest_run(doc_id)
    if latest and latest["status"] in ("queued", "classifying", "analyzing"):
        return {"run_id": latest["run_id"], "status": latest["status"]}
    run_id = generate_id()
    await storage.insert_run(run_id=run_id, doc_id=doc_id, status="queued")
    task = asyncio.create_task(_run_analysis(storage, doc_id, run_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"run_id": run_id, "status": "queued"}


async def _run_analysis(storage: DocReviewStorage, doc_id: str, run_id: str) -> None:
    emit_event_sync(
        EVT_DOC_REVIEW_STARTED, {"kind": EVT_DOC_REVIEW_STARTED, "doc_id": doc_id, "run_id": run_id}
    )
    _run_progress[run_id] = 0.02
    t_total = time.perf_counter()
    try:
        await storage.update_run(run_id, status="classifying")
        # 分类是单次大模型调用（可能长耗时），先把进度推到 5%，
        # 避免前端进度条在整个分类期间停在 2% 看似卡死
        _run_progress[run_id] = 0.05
        doc = await storage.get_document(doc_id)
        assert doc is not None
        parsed = ParsedDocument.model_validate(doc)
        logger.info(
            "doc_review run start doc_id=%s run_id=%s text_chars=%d pages=%d",
            doc_id,
            run_id,
            len(parsed.full_text),
            parsed.page_count,
        )
        t0 = time.perf_counter()
        classification = await classify_document(
            file_name=doc["file_name"],
            sample_text=parsed.full_text,
            max_chars=settings.doc_review_classify_max_chars,
        )
        logger.info(
            "doc_review classify done doc_id=%s elapsed=%.1fs category=%s risk_types=%s",
            doc_id,
            time.perf_counter() - t0,
            classification.doc_category.value,
            [rt.value for rt in classification.risk_types],
        )
        _run_progress[run_id] = 0.15
        risk_types = [rt.value for rt in classification.risk_types]
        emit_event_sync(
            EVT_DOC_REVIEW_CLASSIFIED,
            {
                "kind": EVT_DOC_REVIEW_CLASSIFIED,
                "doc_id": doc_id,
                "run_id": run_id,
                "doc_category": classification.doc_category.value,
                "risk_types": risk_types,
            },
        )
        # 检索驱动的类型自动判定：无需人工指定文档类型。
        # 一次混合检索拿全部维度的规则；命中但分类器漏勾的维度自动补进分析
        provider = build_default_rule_provider()
        rules_by_type = await provider.search(parsed.full_text)
        activated = [rt for rt in rules_by_type if rt not in classification.risk_types]
        if activated:
            classification.risk_types = [*classification.risk_types, *activated]
            logger.info(
                "doc_review risk_types auto-activated by retrieval doc_id=%s added=%s",
                doc_id,
                [rt.value for rt in activated],
            )
        rules = [rule for rt_rules in rules_by_type.values() for rule in rt_rules]
        risk_types = [rt.value for rt in classification.risk_types]
        await storage.update_run(
            run_id,
            status="analyzing",
            doc_category=classification.doc_category.value,
            risk_types=risk_types,
        )
        t0 = time.perf_counter()
        findings = await analyze_document(
            parsed=parsed,
            classification=classification,
            rules=rules,
            chunk_max_chars=settings.doc_review_chunk_max_chars,
            chunk_overlap=settings.doc_review_chunk_overlap,
            on_progress=lambda frac: _run_progress.__setitem__(run_id, 0.15 + 0.8 * frac),
        )
        logger.info(
            "doc_review analyze done doc_id=%s elapsed=%.1fs findings=%d",
            doc_id,
            time.perf_counter() - t0,
            len(findings),
        )
        model_name = settings.doc_review_model or settings.ollama_model
        result = build_analysis_result(
            doc_id=doc_id,
            classification=classification,
            findings=findings,
            model_name=model_name,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        await storage.insert_findings(
            run_id,
            doc_id,
            [
                {
                    "finding_id": f.finding_id,
                    "risk_type": f.risk_type.value,
                    "risk_level": f.risk_level.value,
                    "title": f.title,
                    "description": f.description,
                    "suggestion": f.suggestion,
                    "rule_ref": f.rule_ref,
                    "evidence_text": f.evidence_text,
                    "positions_json": json.dumps(
                        [p.model_dump() for p in f.positions], ensure_ascii=False
                    ),
                }
                for f in findings
            ],
        )
        await storage.update_run(
            run_id,
            status="done",
            overall_risk_level=result.overall_risk_level.value,
            summary=result.summary,
            model_provider="local",
            model_name=model_name,
        )
        emit_event_sync(
            EVT_DOC_REVIEW_FINDINGS_READY,
            {
                "kind": EVT_DOC_REVIEW_FINDINGS_READY,
                "doc_id": doc_id,
                "run_id": run_id,
                "overall_risk_level": result.overall_risk_level.value,
                "finding_count": len(findings),
            },
        )
        try:
            from agent.audit.store import audit

            await audit(
                "doc_review.analysis_done",
                {"doc_id": doc_id, "run_id": run_id, "finding_count": len(findings)},
            )
        except Exception:
            logger.warning("doc_review audit write failed", exc_info=True)
        _run_progress[run_id] = 1.0
        logger.info(
            "doc_review run done doc_id=%s run_id=%s findings=%d total_elapsed=%.1fs",
            doc_id,
            run_id,
            len(findings),
            time.perf_counter() - t_total,
        )
    except Exception as exc:
        logger.exception("doc_review analysis failed doc_id=%s", doc_id)
        await storage.update_run(run_id, status="failed", error=str(exc))
        _run_progress.pop(run_id, None)
        emit_event_sync(
            EVT_DOC_REVIEW_FAILED,
            {
                "kind": EVT_DOC_REVIEW_FAILED,
                "doc_id": doc_id,
                "run_id": run_id,
                "error": str(exc),
            },
        )


@router.get("/documents/{doc_id}/findings")
async def list_findings(doc_id: str, run_id: str | None = None) -> dict[str, Any]:
    storage = get_default_storage()
    if await storage.get_document(doc_id) is None:
        raise HTTPException(status_code=404, detail=f"doc not found: {doc_id}")
    if run_id is None:
        latest = await storage.latest_run(doc_id)
        if latest is None or latest["status"] != "done":
            return {"doc_id": doc_id, "findings": []}
        run_id = latest["run_id"]
    entries = await storage.list_findings(doc_id, run_id)
    entries.sort(
        key=lambda f: {"low": 0, "medium": 1, "high": 2, "critical": 3}[f["risk_level"]],
        reverse=True,
    )
    entries = _attach_kb_refs(entries)
    return {"doc_id": doc_id, "run_id": run_id, "count": len(entries), "findings": entries}


@router.get("/documents/{doc_id}/export")
async def export_word(doc_id: str, mode: str = "risks_only") -> Response:
    """导出审核结果为 Word：mode=full（全文+批注）| risks_only（结构化风险报告）。"""
    if mode not in ("full", "risks_only"):
        raise HTTPException(status_code=400, detail="mode 必须为 full 或 risks_only")
    storage = get_default_storage()
    doc = await storage.get_document(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"doc not found: {doc_id}")
    latest = await storage.latest_run(doc_id)
    if latest is None or latest["status"] != "done":
        raise HTTPException(status_code=409, detail="分析尚未完成，无法导出")
    findings = _attach_kb_refs(await storage.list_findings(doc_id))
    try:
        from agent.doc_review.exporter import build_export_docx

        content = build_export_docx(mode=mode, parsed=doc, findings=findings, run=latest)
    except Exception as exc:
        logger.exception("doc_review export failed doc_id=%s", doc_id)
        raise HTTPException(status_code=500, detail=f"导出失败: {exc}") from exc
    stem = str(doc.get("file_name", "审核报告")).rsplit(".", 1)[0]
    suffix = "全文批注" if mode == "full" else "风险报告"
    fname = f"{stem}_审核{suffix}.docx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": (f"attachment; filename*=UTF-8''{quote(fname)}")},
    )


@router.get("/documents/{doc_id}/status")
async def get_status(doc_id: str) -> dict[str, Any]:
    storage = get_default_storage()
    if await storage.get_document(doc_id) is None:
        raise HTTPException(status_code=404, detail=f"doc not found: {doc_id}")
    latest = await storage.latest_run(doc_id)
    if latest is None:
        return {"doc_id": doc_id, "status": "none"}
    progress = _run_progress.get(latest["run_id"])
    # 已完成但内存无记录（Agent 重启过）→ 直接报 100%
    if progress is None and latest["status"] == "done":
        progress = 1.0
    return {
        "doc_id": doc_id,
        "run_id": latest["run_id"],
        "status": latest["status"],
        "error": latest["error"],
        "progress": progress,
    }


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str) -> dict[str, Any]:
    storage = get_default_storage()
    if not await storage.delete_document(doc_id):
        raise HTTPException(status_code=404, detail=f"doc not found: {doc_id}")
    return {"doc_id": doc_id, "deleted": True}
