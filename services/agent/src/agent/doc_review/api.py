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
from agent.doc_review.storage import DocReviewStorage, get_default_storage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/doc-review", tags=["doc-review"])

_background_tasks: set[asyncio.Task[Any]] = set()

# 分析进度（run_id → 0..1）：内存态，供 /status 轮询；Agent 重启归零不影响主流程
_run_progress: dict[str, float] = {}


async def _attach_kb_refs(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """为每条 finding 附加「知识库依据」kb_refs —— 来源为**用户上传的本地 RAG 知识库**。

    对每条 finding 用其 title/description/evidence_text 做一次混合检索，命中转成
    kb_refs（source/heading/excerpt/matched_terms/file_path），file_path 指回上传源文件
    供前端点击预览。未启用 RAG / 库空 / 检索异常 → kb_refs=[]（best-effort，不阻断）。
    """
    for item in findings:
        item.setdefault("kb_refs", [])
    if not findings or not settings.rag_enabled:
        return findings
    t0 = time.perf_counter()
    refs_total = 0
    try:
        from agent.knowledge.retriever import get_default_retriever
        from agent.knowledge.storage import get_default_storage as get_kb_storage

        retriever = get_default_retriever()
        kb_storage = get_kb_storage()
        # 每条 finding 各做一次混合检索。串行会随 finding 数线性放大（实测 16 条≈32s），
        # 读取详情的 GET 会超过前端 Rust client 超时、被误报成「分析失败」；改为受限并发
        # （reranker 走 asyncio.to_thread、检索不阻塞事件循环），把总耗时压到接近单次检索。
        sem = asyncio.Semaphore(max(1, settings.doc_review_kb_refs_concurrency))

        async def _attach_one(item: dict[str, Any]) -> int:
            query = " ".join(
                str(item.get(k, "") or "") for k in ("title", "description", "evidence_text")
            ).strip()
            if not query:
                return 0
            async with sem:
                try:
                    ctx = await retriever.retrieve(query, top_k=3)
                except Exception:
                    logger.debug("doc_review kb_refs rag retrieve failed", exc_info=True)
                    return 0
            refs: list[dict[str, Any]] = []
            for r in ctx.results:
                meta = getattr(r.chunk, "metadata", {}) or {}
                refs.append(
                    {
                        "source": str(r.doc_title or r.citation or ""),
                        "heading": str(meta.get("heading_path", "") or ""),
                        "excerpt": str(meta.get("child_content") or r.chunk.content or "")[:200],
                        "matched_terms": list(meta.get("matched", []) or [])[:6],
                        "file_path": kb_storage.resolve_file_path(r.chunk.doc_id),
                    }
                )
            item["kb_refs"] = refs
            return len(refs)

        # gather 保留提交顺序，且每条 item 就地写入自己的 kb_refs —— finding↔依据不会错位
        refs_total = sum(await asyncio.gather(*(_attach_one(it) for it in findings)))
    except Exception as exc:
        logger.warning("doc_review kb_refs (rag) attach failed: %s", exc)
    logger.info(
        "doc_review kb_refs (rag) attached: findings=%d refs=%d elapsed=%.1fms",
        len(findings),
        refs_total,
        (time.perf_counter() - t0) * 1000,
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
    doc["findings"] = await _attach_kb_refs(await storage.list_findings(doc_id))
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
        # 依据统一走用户上传的 RAG 知识库：rag_context 注入分析提示词 + kb_refs 读取时附加。
        # 不再用内置财税规则库注入 {{rules}}（analyzer 对空规则渲染“（无，模型自主判断）”），
        # 也移除基于财税检索的风险类型自动补全。
        rules: list[Any] = []
        risk_types = [rt.value for rt in classification.risk_types]
        # 本地知识库混合检索（审核专家 RAG）：召回参考资料注入分析提示词（best-effort）
        rag_context = ""
        if settings.rag_enabled:
            try:
                from agent.knowledge.retriever import get_default_retriever

                rag_query = parsed.full_text[: settings.doc_review_classify_max_chars]
                rag_ctx = await get_default_retriever().retrieve(
                    rag_query, top_k=settings.rag_top_k
                )
                rag_context = rag_ctx.formatted_prompt or ""
                logger.info(
                    "doc_review rag retrieve doc_id=%s hits=%d chars=%d",
                    doc_id,
                    len(rag_ctx.results),
                    len(rag_context),
                )
            except Exception:
                logger.debug("doc_review rag retrieve skipped", exc_info=True)
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
            rag_context=rag_context,
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
    entries = await _attach_kb_refs(entries)
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
    findings = await _attach_kb_refs(await storage.list_findings(doc_id))
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
