"""文档风险合规审核 —— FastAPI 路由。"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
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
        from agent.audit.store import audit  # type: ignore[import-untyped]

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
    doc["findings"] = await storage.list_findings(doc_id)
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
    try:
        await storage.update_run(run_id, status="classifying")
        doc = await storage.get_document(doc_id)
        assert doc is not None
        parsed = ParsedDocument.model_validate(doc)
        classification = await classify_document(
            file_name=doc["file_name"],
            sample_text=parsed.full_text,
            max_chars=settings.doc_review_classify_max_chars,
        )
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
        await storage.update_run(
            run_id,
            status="analyzing",
            doc_category=classification.doc_category.value,
            risk_types=risk_types,
        )
        provider = build_default_rule_provider()
        rules = []
        for risk_type in classification.risk_types:
            rules.extend(
                await provider.get_rules(
                    doc_category=classification.doc_category.value, risk_type=risk_type
                )
            )
        findings = await analyze_document(
            parsed=parsed,
            classification=classification,
            rules=rules,
            chunk_max_chars=settings.doc_review_chunk_max_chars,
            chunk_overlap=settings.doc_review_chunk_overlap,
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
    except Exception as exc:
        logger.exception("doc_review analysis failed doc_id=%s", doc_id)
        await storage.update_run(run_id, status="failed", error=str(exc))
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
    return {"doc_id": doc_id, "run_id": run_id, "count": len(entries), "findings": entries}


@router.get("/documents/{doc_id}/status")
async def get_status(doc_id: str) -> dict[str, Any]:
    storage = get_default_storage()
    if await storage.get_document(doc_id) is None:
        raise HTTPException(status_code=404, detail=f"doc not found: {doc_id}")
    latest = await storage.latest_run(doc_id)
    if latest is None:
        return {"doc_id": doc_id, "status": "none"}
    return {
        "doc_id": doc_id,
        "run_id": latest["run_id"],
        "status": latest["status"],
        "error": latest["error"],
    }


@router.delete("/documents/{doc_id}")
async def delete_document(doc_id: str) -> dict[str, Any]:
    storage = get_default_storage()
    if not await storage.delete_document(doc_id):
        raise HTTPException(status_code=404, detail=f"doc not found: {doc_id}")
    return {"doc_id": doc_id, "deleted": True}
