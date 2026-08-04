"""Phase 14 V0 · FastAPI 5 端点 —— 图像处理后端。

端点：
  - POST /image/enhance —— 超分（mock + V1 ONNX）
  - POST /image/correct —— 矫正（mock + V1 OpenCV）
  - POST /image/ocr —— OCR（mock + V1 PaddleOCR）
  - GET  /image/tasks —— 任务列表
  - GET  /image/tasks/{task_id} —— 单任务详情
  - GET  /image/stats —— 统计（按 processing_type）

V0 全部走 mock 后端（不真做处理，仅走通完整链路 + 写审计）。
V1 接力时自动切换 ONNX / OpenCV / PaddleOCR（get_default_backend 检测）。
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.image_processing.correct import get_correct_backend, reset_correct_backend
from agent.image_processing.enhance import get_enhance_backend, reset_enhance_backend
from agent.image_processing.events import (
    EVT_IMG_PROCESSING_DONE,
    EVT_IMG_PROCESSING_ERROR,
    EVT_IMG_PROCESSING_STARTED,
    emit_event_sync,
)
from agent.image_processing.models import (
    CorrectionType,
    CorrectRequest,
    CorrectResponse,
    EnhanceAlgorithm,
    EnhanceRequest,
    EnhanceResponse,
    FileSizeExceededError,
    ImageProcessingError,
    OcrEngine,
    OcrRequest,
    OcrResponse,
    UnsupportedFormatError,
)
from agent.image_processing.ocr import get_ocr_backend, reset_ocr_backend
from agent.image_processing.storage import get_default_storage, reset_default_storage


router = APIRouter(prefix="/image", tags=["image-processing"])


# ---- Pydantic schemas ----------------------------------------------------

class EnhanceAPIRequest(BaseModel):
    input_path: str
    output_path: str
    algorithm: EnhanceAlgorithm = EnhanceAlgorithm.MOCK_X2
    tile_size: int = Field(default=512, ge=64, le=1024)
    device: str = Field(default="auto", pattern="^(cpu|cuda|auto)$")


class CorrectAPIRequest(BaseModel):
    input_path: str
    output_path: str
    correction_type: CorrectionType = CorrectionType.MOCK
    auto_detect: bool = True


class OcrAPIRequest(BaseModel):
    input_path: str
    engine: OcrEngine = OcrEngine.MOCK
    languages: tuple[str, ...] = ("ch", "en")
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    device: str = Field(default="auto", pattern="^(cpu|cuda|auto)$")


class TaskResponse(BaseModel):
    task_id: str
    processing_type: str
    backend: str
    input_path: str
    output_path: str | None
    input_size: int
    output_size: int
    elapsed_ms: int
    ok: bool
    error: str | None = None
    ocr_text: str | None = None
    ocr_confidence: float | None = None
    ocr_block_count: int | None = None
    meta: dict = Field(default_factory=dict)
    ts: str


# ---- 5 端点 --------------------------------------------------------------

@router.post("/enhance", response_model=EnhanceResponse)
async def enhance(req: EnhanceAPIRequest) -> EnhanceResponse:
    """超分（V0 mock；V1 ONNX Real-ESRGAN 接力）。"""
    task_id = uuid.uuid4().hex
    backend = get_enhance_backend()
    enhance_req = EnhanceRequest(
        input_path=req.input_path,
        output_path=req.output_path,
        algorithm=req.algorithm,
        tile_size=req.tile_size,
        device=req.device,
    )

    emit_event_sync(EVT_IMG_PROCESSING_STARTED, {
        "kind": EVT_IMG_PROCESSING_STARTED,
        "task_id": task_id,
        "processing_type": "enhance",
        "input_path": req.input_path,
        "algorithm": req.algorithm.value,
    })

    try:
        result = await backend.enhance(enhance_req)
    except (UnsupportedFormatError, FileSizeExceededError) as exc:
        emit_event_sync(EVT_IMG_PROCESSING_ERROR, {
            "kind": EVT_IMG_PROCESSING_ERROR,
            "task_id": task_id,
            "processing_type": "enhance",
            "error": str(exc),
        })
        raise HTTPException(status_code=400, detail=str(exc))
    except ImageProcessingError as exc:
        emit_event_sync(EVT_IMG_PROCESSING_ERROR, {
            "kind": EVT_IMG_PROCESSING_ERROR,
            "task_id": task_id,
            "processing_type": "enhance",
            "error": str(exc),
        })
        raise HTTPException(status_code=422, detail=str(exc))

    # 写 storage（best-effort，不阻塞响应）
    try:
        storage = get_default_storage()
        await storage.insert_task(
            task_id=task_id,
            processing_type="enhance",
            backend=result.backend,
            input_path=req.input_path,
            output_path=result.output_path,
            input_size=result.input_size_bytes,
            output_size=result.output_size_bytes,
            elapsed_ms=result.elapsed_ms,
            ok=result.ok,
            error=result.error,
            ocr_text=None,
            ocr_confidence=None,
            ocr_block_count=None,
            meta={
                **result.meta,
                "algorithm": req.algorithm.value,
                "device": req.device,
            },
        )
    except Exception:
        pass

    emit_event_sync(EVT_IMG_PROCESSING_DONE, {
        "kind": EVT_IMG_PROCESSING_DONE,
        "task_id": task_id,
        "processing_type": "enhance",
        "ok": result.ok,
        "elapsed_ms": result.elapsed_ms,
    })
    return result


@router.post("/correct", response_model=CorrectResponse)
async def correct(req: CorrectAPIRequest) -> CorrectResponse:
    """矫正（V0 mock；V1 OpenCV 接力）。"""
    task_id = uuid.uuid4().hex
    backend = get_correct_backend()
    correct_req = CorrectRequest(
        input_path=req.input_path,
        output_path=req.output_path,
        correction_type=req.correction_type,
        auto_detect=req.auto_detect,
    )

    emit_event_sync(EVT_IMG_PROCESSING_STARTED, {
        "kind": EVT_IMG_PROCESSING_STARTED,
        "task_id": task_id,
        "processing_type": "correct",
        "input_path": req.input_path,
        "correction_type": req.correction_type.value,
    })

    try:
        result = await backend.correct(correct_req)
    except (UnsupportedFormatError, FileSizeExceededError) as exc:
        emit_event_sync(EVT_IMG_PROCESSING_ERROR, {
            "kind": EVT_IMG_PROCESSING_ERROR,
            "task_id": task_id,
            "processing_type": "correct",
            "error": str(exc),
        })
        raise HTTPException(status_code=400, detail=str(exc))
    except ImageProcessingError as exc:
        emit_event_sync(EVT_IMG_PROCESSING_ERROR, {
            "kind": EVT_IMG_PROCESSING_ERROR,
            "task_id": task_id,
            "processing_type": "correct",
            "error": str(exc),
        })
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        storage = get_default_storage()
        await storage.insert_task(
            task_id=task_id,
            processing_type="correct",
            backend=result.backend,
            input_path=req.input_path,
            output_path=result.output_path,
            input_size=0,
            output_size=0,
            elapsed_ms=result.elapsed_ms,
            ok=result.ok,
            error=result.error,
            ocr_text=None,
            ocr_confidence=None,
            ocr_block_count=None,
            meta={**result.meta, "correction_type": req.correction_type.value},
        )
    except Exception:
        pass

    emit_event_sync(EVT_IMG_PROCESSING_DONE, {
        "kind": EVT_IMG_PROCESSING_DONE,
        "task_id": task_id,
        "processing_type": "correct",
        "ok": result.ok,
        "elapsed_ms": result.elapsed_ms,
    })
    return result


@router.post("/ocr", response_model=OcrResponse)
async def ocr(req: OcrAPIRequest) -> OcrResponse:
    """OCR（V0 mock；V1 PaddleOCR 接力）。"""
    task_id = uuid.uuid4().hex
    backend = get_ocr_backend()
    ocr_req = OcrRequest(
        input_path=req.input_path,
        engine=req.engine,
        languages=req.languages,
        confidence_threshold=req.confidence_threshold,
        device=req.device,
    )

    emit_event_sync(EVT_IMG_PROCESSING_STARTED, {
        "kind": EVT_IMG_PROCESSING_STARTED,
        "task_id": task_id,
        "processing_type": "ocr",
        "input_path": req.input_path,
        "languages": list(req.languages),
    })

    try:
        result = await backend.ocr(ocr_req)
    except (UnsupportedFormatError, FileSizeExceededError) as exc:
        emit_event_sync(EVT_IMG_PROCESSING_ERROR, {
            "kind": EVT_IMG_PROCESSING_ERROR,
            "task_id": task_id,
            "processing_type": "ocr",
            "error": str(exc),
        })
        raise HTTPException(status_code=400, detail=str(exc))
    except ImageProcessingError as exc:
        emit_event_sync(EVT_IMG_PROCESSING_ERROR, {
            "kind": EVT_IMG_PROCESSING_ERROR,
            "task_id": task_id,
            "processing_type": "ocr",
            "error": str(exc),
        })
        raise HTTPException(status_code=422, detail=str(exc))

    try:
        storage = get_default_storage()
        await storage.insert_task(
            task_id=task_id,
            processing_type="ocr",
            backend=result.engine,
            input_path=req.input_path,
            output_path=None,
            input_size=0,
            output_size=0,
            elapsed_ms=result.elapsed_ms,
            ok=result.ok,
            error=result.error,
            ocr_text=result.text,
            ocr_confidence=result.confidence,
            ocr_block_count=len(result.blocks),
            meta={**result.meta, "languages": list(req.languages)},
        )
    except Exception:
        pass

    emit_event_sync(EVT_IMG_PROCESSING_DONE, {
        "kind": EVT_IMG_PROCESSING_DONE,
        "task_id": task_id,
        "processing_type": "ocr",
        "ok": result.ok,
        "elapsed_ms": result.elapsed_ms,
    })
    return result


@router.get("/tasks", response_model=list[TaskResponse])
async def list_tasks(
    processing_type: str | None = None,
    ok: bool | None = None,
    limit: int = 50,
) -> list[TaskResponse]:
    storage = get_default_storage()
    tasks = await storage.list_tasks(
        processing_type=processing_type,
        ok=ok,
        limit=min(limit, 500),
    )
    return [TaskResponse(**t) for t in tasks]


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str) -> TaskResponse:
    storage = get_default_storage()
    task = await storage.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"task not found: {task_id}")
    return TaskResponse(**task)


@router.get("/stats")
async def stats() -> dict[str, Any]:
    storage = get_default_storage()
    return await storage.get_stats()