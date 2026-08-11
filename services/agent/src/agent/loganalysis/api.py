"""loganalysis.api —— Phase 2F+ V1 日志分析 FastAPI 路由。

路由：
- POST /loganalysis/extract         — 给 lines → ErrorBlock[]（无 LLM 调用）
- POST /loganalysis/root-cause       — 给 lines / 错误块 → RootCauseResponse
- POST /loganalysis/log-level-classify — 给 lines → LogLevelClassifyResponse
- GET  /loganalysis/cache/stats       — 3 张表统计
- DELETE /loganalysis/cache           — 清过期缓存

CLAUDE.md §6 安全红线：
- 所有 LLM 调用前必经过 scrub_error_blocks() 脱敏
- 缓存只存脱敏后的 payload（不允许原始 PII 进任何缓存）
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.llm.router import LMRouter
from agent.loganalysis import extractor, scrubber
from agent.loganalysis import router as llm_router
from agent.loganalysis.models import (
    AnalysisCacheEntry,
    ErrorBlock,
    LogLevelClassifyResponse,
    RootCauseRequest,
    gen_request_id,
)
from agent.loganalysis.storage import LogAnalysisStorage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/loganalysis", tags=["loganalysis"])


# ---- 单例 lazy -----------------------------------------------------------

_storage: LogAnalysisStorage | None = None


def _get_storage() -> LogAnalysisStorage:
    global _storage
    if _storage is None:
        from agent.loganalysis.storage import get_default_storage

        _storage = get_default_storage()
    return _storage


def reset_for_testing() -> None:
    global _storage
    _storage = None


def _get_llm() -> LMRouter:
    from agent.main import get_runtime

    return get_runtime().llm


# ---- Pydantic schema ------------------------------------------------------


class ErrorBlockPayload(BaseModel):
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
    header: str = Field(..., min_length=1)
    stack_trace: list[str] = Field(default_factory=list)
    level: str = "ERROR"
    fingerprint: str = ""


class ExtractRequest(BaseModel):
    lines: list[str] = Field(..., min_length=1, max_length=100_000)
    max_stack_lines: int = Field(default=50, ge=1, le=500)
    max_blocks: int = Field(default=200, ge=1, le=1000)


class ExtractResponse(BaseModel):
    blocks: list[dict[str, Any]]
    count: int


class RootCauseRequestPayload(BaseModel):
    file_path: str = Field(..., min_length=1, max_length=4096)
    file_fingerprint: str = Field(default="", max_length=128)
    blocks: list[ErrorBlockPayload] = Field(default_factory=list)
    lines: list[str] = Field(default_factory=list)
    context_window: int = Field(default=100, ge=0, le=1000)
    max_tokens: int = Field(default=3000, ge=200, le=8000)
    analysis_type: str = Field(default="log_root_cause")
    use_cache: bool = True


class RootCauseResponsePayload(BaseModel):
    summary: str
    error_count: int
    blocks_analyzed: int
    tokens_used: int
    model_used: str
    elapsed_ms: int
    backend: str
    blocks: list[dict[str, Any]]
    cache_hit: bool = False
    request_id: str = ""


class LogLevelClassifyRequest(BaseModel):
    lines: list[str] = Field(..., min_length=1, max_length=100_000)


class LogLevelClassifyResponsePayload(BaseModel):
    results: list[dict[str, Any]]
    elapsed_ms: int
    backend: str


class CacheStats(BaseModel):
    search_cache_rows: int
    tail_sessions_rows: int
    tail_sessions_active: int
    log_analysis_cache_rows: int


# ---- 端点 ---------------------------------------------------------------


@router.post("/extract", response_model=ExtractResponse)
async def loganalysis_extract(body: ExtractRequest):
    """从 lines 提取 ERROR 块（纯启发式，无 LLM 调用，不写缓存）。"""
    blocks = extractor.extract_error_blocks(
        body.lines,
        max_stack_lines=body.max_stack_lines,
        max_blocks=body.max_blocks,
    )
    return ExtractResponse(
        blocks=[b.to_dict() for b in blocks],
        count=len(blocks),
    )


@router.post("/root-cause", response_model=RootCauseResponsePayload)
async def loganalysis_root_cause(body: RootCauseRequestPayload):
    """根因分析。

    流程：
        1. 提取 ERROR 块（若 body.blocks 为空 + body.lines 有内容 → 自动提取）
        2. PII 脱敏
        3. 缓存查询（按 file_fingerprint + analysis_type + summary hash）
        4. 调 LLM（私有 / 兜底 mock）
        5. 写缓存 + 返响应
    """
    storage = _get_storage()
    llm = _get_llm()
    request_id = gen_request_id()

    # 1. ERROR 块来源：body.blocks 优先；否则从 body.lines 提取
    if body.blocks:
        blocks = [
            ErrorBlock(
                start_line=p.start_line,
                end_line=p.end_line,
                header=p.header,
                stack_trace=list(p.stack_trace),
                level=p.level,
                fingerprint=p.fingerprint,
            )
            for p in body.blocks
        ]
    elif body.lines:
        blocks = extractor.extract_error_blocks(
            body.lines,
            max_stack_lines=50,
            max_blocks=200,
        )
    else:
        raise HTTPException(400, "either blocks or lines must be provided")

    if not blocks:
        return RootCauseResponsePayload(
            summary="未检测到 ERROR 块；日志看起来正常。",
            error_count=0,
            blocks_analyzed=0,
            tokens_used=0,
            model_used="",
            elapsed_ms=0,
            backend="noop",
            blocks=[],
            cache_hit=False,
            request_id=request_id,
        )

    # 2. PII 脱敏（CLAUDE.md §6 红线 —— 原始 PII 永远不进 LLM / 缓存）
    scrubbed_blocks = scrubber.scrub_error_blocks(blocks)

    # 3. 缓存查询
    cache_lookup: AnalysisCacheEntry | None = None
    cache_hit = False
    if body.use_cache and body.file_fingerprint:
        cache_key = _make_cache_key(
            body.file_fingerprint,
            body.analysis_type,
            summary_input=scrubbed_blocks,
        )
        cache_lookup = storage.get_analysis_cache(cache_key)
        if cache_lookup is not None:
            cache_hit = True

    # 4. 调 LLM
    req = RootCauseRequest(
        file_path=body.file_path,
        error_blocks=scrubbed_blocks,
        context_window=body.context_window,
        context_window_lines=body.lines[-body.context_window :] if body.lines else [],
        max_tokens=body.max_tokens,
        analysis_type=body.analysis_type,
    )
    resp = await llm_router.analyze_root_cause(
        req,
        llm=llm,
        scrubbed_blocks=scrubbed_blocks,
        cache_lookup=cache_lookup,
    )

    # 5. 写缓存（仅未命中 + 缓存 key 可构造时）
    if not cache_hit and body.use_cache and body.file_fingerprint:
        try:
            entry = AnalysisCacheEntry.new(
                cache_key=_make_cache_key(
                    body.file_fingerprint,
                    body.analysis_type,
                    summary_input=scrubbed_blocks,
                ),
                file_path=body.file_path,
                file_fingerprint=body.file_fingerprint,
                analysis_type=body.analysis_type,
                payload_json=_safe_json_dumps(resp.to_dict()),
                ttl_sec=3600,
            )
            storage.upsert_analysis_cache(entry)
        except Exception as e:
            logger.warning("upsert analysis cache failed: %s", e)

    return RootCauseResponsePayload(
        summary=resp.summary,
        error_count=resp.error_count,
        blocks_analyzed=resp.blocks_analyzed,
        tokens_used=resp.tokens_used,
        model_used=resp.model_used,
        elapsed_ms=resp.elapsed_ms,
        backend=resp.backend,
        blocks=[b.to_dict() for b in resp.blocks],
        cache_hit=cache_hit,
        request_id=request_id,
    )


@router.post("/log-level-classify", response_model=LogLevelClassifyResponsePayload)
async def loganalysis_log_level_classify(body: LogLevelClassifyRequest):
    """批量识别日志级别（端侧模型优先，失败兜底正则）。"""
    llm = _get_llm()
    resp: LogLevelClassifyResponse = await llm_router.classify_log_levels(
        body.lines,
        llm=llm,
    )
    return LogLevelClassifyResponsePayload(
        results=[r.to_dict() for r in resp.results],
        elapsed_ms=resp.elapsed_ms,
        backend=resp.backend,
    )


@router.get("/cache/stats", response_model=CacheStats)
async def loganalysis_cache_stats():
    """3 张表的统计。"""
    return CacheStats(**_get_storage().get_stats())


@router.delete("/cache")
async def loganalysis_cache_cleanup():
    """清过期缓存（search_cache + log_analysis_cache）。"""
    storage = _get_storage()
    n1 = storage.cleanup_search_cache()
    n2 = storage.cleanup_analysis_cache()
    return {"search_cache_deleted": n1, "log_analysis_cache_deleted": n2}


# ---- 内部工具 -------------------------------------------------------------


def _make_cache_key(
    file_fingerprint: str,
    analysis_type: str,
    *,
    summary_input: list[ErrorBlock],
) -> str:
    """按 (file_fingerprint, analysis_type, blocks summary) 计算 cache_key。"""
    # 用 stack_trace 摘要做 hash 输入（避免 PII）
    digest = hashlib.sha256()
    digest.update(file_fingerprint.encode("utf-8"))
    digest.update(b"|")
    digest.update(analysis_type.encode("utf-8"))
    digest.update(b"|")
    for b in sorted(summary_input, key=lambda x: x.fingerprint):
        digest.update(b.fingerprint.encode("utf-8"))
        digest.update(b"|")
    return digest.hexdigest()


def _safe_json_dumps(obj: Any) -> str:
    """确保 PII 永不入缓存；万一有残留字符串，强制转码 + ensure_ascii。"""
    import json

    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        logger.warning("_safe_json_dumps failed: %s (returning empty object)", e)
        return "{}"
