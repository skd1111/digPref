"""Phase 14 V0 · OpenCV 几何/色彩矫正骨架（V0 mock + V1 接力 OpenCV）。

V0 实现：
  - MockCorrectionBackend：返成功响应 + 不真做矫正（仅复制文件）
  - get_default_backend()：V0 默认 mock

V1 接力：
  - OpenCVCorrectionBackend：透视矫正（findContours + getPerspectiveTransform + warpPerspective）
  - 倾斜矫正（minAreaRect + rotation）
  - 去噪（fastNlMeansDenoisingColored）
  - 自动检测（auto_detect=True 时根据长宽比 / 边缘检测判断）

CLAUDE.md §phase-14 红线：
  - 仅做几何/色彩矫正，**不做**超分（超分走 enhance.py）
  - 数据不出域（OpenCV 端侧推理）
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from agent.image_processing.models import (
    BackendUnavailableError,
    CorrectionBackend,
    CorrectRequest,
    CorrectResponse,
    check_file_size,
    is_supported_format,
)


class MockCorrectionBackend:
    """V0 mock 矫正后端。"""

    name: str = "mock"

    async def correct(self, request: CorrectRequest) -> CorrectResponse:
        started = time.monotonic()
        try:
            check_file_size(request.input_path)
            if not is_supported_format(request.input_path):
                from agent.image_processing.models import UnsupportedFormatError

                raise UnsupportedFormatError(
                    fmt=Path(request.input_path).suffix.lstrip("."),
                    supported=frozenset({"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}),
                )

            in_p = Path(request.input_path).resolve()
            out_p = Path(request.output_path).resolve()
            if in_p == out_p:
                return CorrectResponse(
                    ok=False,
                    output_path=request.output_path,
                    backend=self.name,
                    error="output_path_same_as_input: must differ from input_path",
                )

            await asyncio.to_thread(self._mock_correct_sync, request)

            elapsed_ms = int((time.monotonic() - started) * 1000)
            return CorrectResponse(
                ok=True,
                output_path=request.output_path,
                correction_applied=request.correction_type.value,
                elapsed_ms=elapsed_ms,
                backend=self.name,
                meta={
                    "auto_detect": request.auto_detect,
                    "mock_note": "V0 mock: file copied, no actual correction",
                },
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return CorrectResponse(
                ok=False,
                output_path=request.output_path,
                elapsed_ms=elapsed_ms,
                backend=self.name,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _mock_correct_sync(self, request: CorrectRequest) -> None:
        out_p = Path(request.output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(request.input_path, request.output_path)


class OpenCVCorrectionBackend:
    """V1 OpenCV 矫正后端（占位）。"""

    name: str = "opencv"

    def __init__(self) -> None:
        raise BackendUnavailableError(
            "OpenCVCorrectionBackend not implemented in V0; V1 will integrate cv2 (透视/倾斜/去噪)"
        )

    async def correct(self, request: CorrectRequest) -> CorrectResponse:
        raise NotImplementedError("V1 will implement OpenCV correction")


def get_default_backend() -> CorrectionBackend:
    """V0 默认 mock。"""
    try:
        # V1 接力：import cv2 + 检查模型
        # import cv2  # V1
        # return OpenCVCorrectionBackend()
        pass
    except (ImportError, BackendUnavailableError):
        pass
    return MockCorrectionBackend()


_DEFAULT_BACKEND: CorrectionBackend | None = None


def get_correct_backend() -> CorrectionBackend:
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = get_default_backend()
    return _DEFAULT_BACKEND


def reset_correct_backend() -> None:
    global _DEFAULT_BACKEND
    _DEFAULT_BACKEND = None
