"""Phase 14 V0 · PaddleOCR 端侧文字识别骨架（V0 mock + V1 接力 PaddleOCR）。

V0 实现：
  - MockOcrBackend：返成功响应 + 空文本 + 空 blocks
  - get_default_backend()：V0 默认 mock

V1 接力：
  - PaddleOcrBackend：paddleocr.PaddleOCR(use_angle_cls=True, lang='ch')
  - 输出 blocks 含 bbox / text / confidence
  - 多语言支持（langs: ch / en / japan / korean）
  - CPU / GPU 自动检测（V1.5 接力 GPU）

CLAUDE.md §phase-14 红线：
  - **OCR 仅做文字识别**（输入图片 → 输出纯文本 + 块坐标 + 置信度）
  - **不做**版面分析 / 表格还原 / 段落结构化
  - 数据不出域（端侧推理）
"""

from __future__ import annotations

import time
from pathlib import Path

from agent.image_processing.models import (
    BackendUnavailableError,
    OcrBackend,
    OcrRequest,
    OcrResponse,
    check_file_size,
    is_supported_format,
)


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
    try:
        # V1 接力：import paddleocr
        # import paddleocr  # V1
        # return PaddleOcrBackend()
        pass
    except (ImportError, BackendUnavailableError):
        pass
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
