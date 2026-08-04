"""Phase 14 V0 · 本地图像处理引擎 —— V0 公开 API。

设计哲学：
  - 数据不出域：超分 / 矫正 / OCR 三类模型总计 ~115MB 本地加载
  - V0 全 mock 后端（不真做处理，仅走通完整链路）
  - V1 接力：ONNX Runtime (Real-ESRGAN x2) + OpenCV (cv2) + PaddleOCR 端侧推理
  - 物理隔离：image_processing.db 与 audit / knowledge / biznav 等 10 个 db 独立

V0 公开 API（30+ 项）：
  - 数据类：EnhanceRequest / EnhanceResponse / CorrectRequest / CorrectResponse /
           OcrRequest / OcrResponse
  - 枚举：ProcessingType / EnhanceAlgorithm / CorrectionType / OcrEngine
  - 协议：EnhancementBackend / CorrectionBackend / OcrBackend（V1 真实实现）
  - 错误：ImageProcessingError / BackendUnavailableError / UnsupportedFormatError /
         FileSizeExceededError
  - 后端：get_enhance_backend / get_correct_backend / get_ocr_backend
  - 存储：get_default_storage / ImageProcessingStorage
  - API router（FastAPI）
"""
from __future__ import annotations

from agent.image_processing.api import router as image_api_router
from agent.image_processing.correct import (
    MockCorrectionBackend,
    OpenCVCorrectionBackend,
    get_correct_backend,
    get_default_backend as get_correct_default_backend,
    reset_correct_backend,
)
from agent.image_processing.enhance import (
    MockEnhancementBackend,
    ONNXEnhancementBackend,
    get_default_backend as get_enhance_default_backend,
    get_enhance_backend,
    reset_enhance_backend,
)
from agent.image_processing.events import (
    EVT_IMG_PROCESSING_DONE,
    EVT_IMG_PROCESSING_ERROR,
    EVT_IMG_PROCESSING_STARTED,
)
from agent.image_processing.models import (
    BackendUnavailableError,
    CorrectionBackend,
    CorrectionType,
    CorrectRequest,
    CorrectResponse,
    EnhanceAlgorithm,
    EnhancementBackend,
    EnhanceRequest,
    EnhanceResponse,
    FileSizeExceededError,
    ImageProcessingError,
    MAX_IMAGE_BYTES,
    MAX_TILE_SIZE,
    OcrBackend,
    OcrEngine,
    OcrRequest,
    OcrResponse,
    ProcessingType,
    SUPPORTED_FORMATS,
    UnsupportedFormatError,
    check_file_size,
    get_image_format,
    is_supported_format,
)
from agent.image_processing.ocr import (
    MockOcrBackend,
    PaddleOcrBackend,
    get_default_backend as get_ocr_default_backend,
    get_ocr_backend,
    reset_ocr_backend,
)
from agent.image_processing.storage import (
    ImageProcessingStorage,
    get_default_storage,
    reset_default_storage,
)


__all__ = [
    # 数据类
    "EnhanceRequest", "EnhanceResponse",
    "CorrectRequest", "CorrectResponse",
    "OcrRequest", "OcrResponse",
    # 枚举
    "ProcessingType", "EnhanceAlgorithm", "CorrectionType", "OcrEngine",
    # 协议
    "EnhancementBackend", "CorrectionBackend", "OcrBackend",
    # 错误
    "ImageProcessingError", "BackendUnavailableError",
    "UnsupportedFormatError", "FileSizeExceededError",
    # 常量
    "SUPPORTED_FORMATS", "MAX_IMAGE_BYTES", "MAX_TILE_SIZE",
    # 工具
    "get_image_format", "is_supported_format", "check_file_size",
    # 后端（mock 占位 + V1 真实）
    "MockEnhancementBackend", "ONNXEnhancementBackend",
    "MockCorrectionBackend", "OpenCVCorrectionBackend",
    "MockOcrBackend", "PaddleOcrBackend",
    # 后端工厂 + reset
    "get_enhance_backend", "get_correct_backend", "get_ocr_backend",
    "get_enhance_default_backend", "get_correct_default_backend", "get_ocr_default_backend",
    "reset_enhance_backend", "reset_correct_backend", "reset_ocr_backend",
    # 存储
    "ImageProcessingStorage", "get_default_storage", "reset_default_storage",
    # 事件常量
    "EVT_IMG_PROCESSING_STARTED", "EVT_IMG_PROCESSING_DONE", "EVT_IMG_PROCESSING_ERROR",
    # API router
    "image_api_router",
]