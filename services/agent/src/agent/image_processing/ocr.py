"""Phase 14 · 端侧文字识别（RapidOCR ONNXRuntime 真实后端 + V0 mock 兑底）。

实现：
  - RapidOcrBackend：rapidocr_onnxruntime 驱动 PP-OCRv4 mobile ONNX（端侧、纯本地、
    数据不出域）；模型~15MB 随包分发，复用项目已有的 onnxruntime。
  - recognize_image_sync / ocr_pdf_to_pages：可复用同步入口（图片 / 扫描件 PDF），
    供聊天工具与 doc_review 扫描件回退共用（PDF 栅格化走 pypdfium2）。
  - MockOcrBackend：依赖缺失时的兑底（返空文本），功能不中断。

CLAUDE.md 红线：
  - **OCR 仅做文字识别**（输入图片 → 输出纯文本 + 块坐标 + 置信度）
  - **不做**版面分析 / 表格还原 / 段落结构化
  - 数据不出域（端侧推理，不经云端 LLM）
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from agent.image_processing.models import (
    BackendUnavailableError,
    OcrBackend,
    OcrRequest,
    OcrResponse,
    check_file_size,
    is_supported_format,
)

logger = logging.getLogger(__name__)


class MockOcrBackend:
    """V0 mock OCR 后端 —— 返空文本 + 空 blocks。"""

    name: str = "mock"

    async def ocr(self, request: OcrRequest) -> OcrResponse:
        started = time.monotonic()
        try:
            check_file_size(request.input_path)
            if not is_supported_format(request.input_path):
                from agent.image_processing.models import UnsupportedFormatError

                raise UnsupportedFormatError(
                    fmt=Path(request.input_path).suffix.lstrip("."),
                    supported=frozenset({"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}),
                )

            elapsed_ms = int((time.monotonic() - started) * 1000)
            return OcrResponse(
                ok=True,
                text="",
                blocks=[],
                confidence=0.0,
                elapsed_ms=elapsed_ms,
                engine=self.name,
                meta={
                    "languages": list(request.languages),
                    "mock_note": "V0 mock: empty text; V1 will integrate PaddleOCR",
                },
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return OcrResponse(
                ok=False,
                elapsed_ms=elapsed_ms,
                engine=self.name,
                error=f"{type(exc).__name__}: {exc}",
            )


# ---- RapidOCR 端侧引擎（可复用同步核心）--------------------------------------

_RAPIDOCR_ENGINE: Any | None = None
_RAPIDOCR_TRIED: bool = False


def _get_rapidocr() -> Any | None:
    """惰性初始化并缓存 RapidOCR 引擎；rapidocr_onnxruntime 未安装时返 None。"""
    global _RAPIDOCR_ENGINE, _RAPIDOCR_TRIED
    if _RAPIDOCR_TRIED:
        return _RAPIDOCR_ENGINE
    _RAPIDOCR_TRIED = True
    try:
        from rapidocr_onnxruntime import RapidOCR

        _RAPIDOCR_ENGINE = RapidOCR()
        logger.info("RapidOCR engine initialized (端侧 ONNX)")
    except Exception as exc:  # ImportError / 模型缺失 / onnxruntime 问题
        logger.info("RapidOCR unavailable, OCR 退化 mock: %s", exc)
        _RAPIDOCR_ENGINE = None
    return _RAPIDOCR_ENGINE


def rapidocr_available() -> bool:
    """RapidOCR 是否可用（供调用方先探测再决定是否走 OCR 回退）。"""
    return _get_rapidocr() is not None


def reset_ocr_engine() -> None:
    """重置缓存的 RapidOCR 引擎（测试用）。"""
    global _RAPIDOCR_ENGINE, _RAPIDOCR_TRIED
    _RAPIDOCR_ENGINE = None
    _RAPIDOCR_TRIED = False


def recognize_image_sync(img: Any) -> tuple[str, list[dict[str, Any]], float]:
    """同步 OCR 单张图片（path/bytes/np.ndarray/PIL.Image）→ (全文, blocks, 平均置信度)。

    RapidOCR 不可用时抛 BackendUnavailableError（调用方据此降级）。
    blocks: [{text, bbox, confidence}]；全文按识别行以 \n 拼接。
    """
    engine = _get_rapidocr()
    if engine is None:
        raise BackendUnavailableError("RapidOCR not installed")
    result, _elapse = engine(img)
    texts: list[str] = []
    blocks: list[dict[str, Any]] = []
    confs: list[float] = []
    for item in result or []:
        # rapidocr 行格式：[box(4点), text, score]
        try:
            box, text, score = item[0], str(item[1]), float(item[2])
        except (IndexError, TypeError, ValueError):
            continue
        if not text.strip():
            continue
        texts.append(text)
        confs.append(score)
        blocks.append({"text": text, "bbox": box, "confidence": score})
    full = "\n".join(texts)
    avg = sum(confs) / len(confs) if confs else 0.0
    return full, blocks, avg


def ocr_pdf_to_pages(
    pdf_path: str | Path,
    *,
    scale: float = 2.0,
    max_pages: int = 0,
) -> list[tuple[int, list[str]]]:
    """扫描件 PDF → 逐页栅格化（pypdfium2）+ RapidOCR 识别 → [(page_no, [block_text, ...])]。

    供 doc_review 扫描件回退与聊天工具共用；依赖缺失抛 BackendUnavailableError。
    每页按识别行切块（保留页码，供上层定位/高亮）。
    """
    if _get_rapidocr() is None:
        raise BackendUnavailableError("RapidOCR not installed")
    try:
        import numpy as np
        import pypdfium2 as pdfium
    except Exception as exc:
        raise BackendUnavailableError(f"pdf 栅格化依赖缺失: {exc}") from exc

    doc = pdfium.PdfDocument(str(pdf_path))
    pages: list[tuple[int, list[str]]] = []
    try:
        n = len(doc)
        limit = n if not max_pages or max_pages <= 0 else min(n, int(max_pages))
        for i in range(limit):
            page = doc[i]
            bitmap = page.render(scale=float(scale))
            pil_img = bitmap.to_pil().convert("RGB")
            arr = np.asarray(pil_img)
            text, _blocks, _conf = recognize_image_sync(arr)
            blocks = [ln.strip() for ln in text.splitlines() if ln.strip()]
            if blocks:
                pages.append((i + 1, blocks))
    finally:
        doc.close()
    return pages


class RapidOcrBackend:
    """RapidOCR（ONNXRuntime）端侧真实后端——聊天工具/端点走此。"""

    name: str = "rapidocr"

    async def ocr(self, request: OcrRequest) -> OcrResponse:
        started = time.monotonic()
        try:
            check_file_size(request.input_path)
            if not is_supported_format(request.input_path):
                from agent.image_processing.models import UnsupportedFormatError

                raise UnsupportedFormatError(
                    fmt=Path(request.input_path).suffix.lstrip("."),
                    supported=frozenset({"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}),
                )
            if _get_rapidocr() is None:
                raise BackendUnavailableError("RapidOCR not installed")
            # 端侧推理为 CPU 密集同步调用 → 放线程池，不阻事件循环
            text, blocks, conf = await asyncio.to_thread(recognize_image_sync, request.input_path)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return OcrResponse(
                ok=True,
                text=text,
                blocks=blocks,
                confidence=conf,
                elapsed_ms=elapsed_ms,
                engine=self.name,
                meta={"languages": list(request.languages), "line_count": len(blocks)},
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return OcrResponse(
                ok=False,
                elapsed_ms=elapsed_ms,
                engine=self.name,
                error=f"{type(exc).__name__}: {exc}",
            )


class PaddleOcrBackend:
    """V1 PaddleOCR 后端（占位）。"""

    name: str = "paddleocr"

    def __init__(self) -> None:
        raise BackendUnavailableError(
            "PaddleOcrBackend not implemented in V0; V1 will integrate paddleocr (ch/en/japan/korean)"
        )

    async def ocr(self, request: OcrRequest) -> OcrResponse:
        raise NotImplementedError("V1 will implement PaddleOCR text recognition")


def get_default_backend() -> OcrBackend:
    """优先 RapidOCR（端侧真实）；依赖缺失时退化 mock（功能不中断）。"""
    if rapidocr_available():
        return RapidOcrBackend()
    return MockOcrBackend()


_DEFAULT_BACKEND: OcrBackend | None = None


def get_ocr_backend() -> OcrBackend:
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = get_default_backend()
    return _DEFAULT_BACKEND


def reset_ocr_backend() -> None:
    global _DEFAULT_BACKEND
    _DEFAULT_BACKEND = None
