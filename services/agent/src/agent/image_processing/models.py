"""Phase 14 V0 · 本地图像处理引擎 —— 数据模型。

V0 状态：
  - 5 个 dataclass（EnhanceRequest/Response / CorrectRequest/Response / OcrRequest/Response）
  - 6 个常量（后端类型 + 支持格式 + 大小上限）
  - 协议 Protocol（EnhancementBackend / CorrectionBackend / OcrBackend）
  - 错误类（ImageProcessingError / BackendUnavailableError / UnsupportedFormatError）

V1 接力（V0.5 / V1 阶段）：
  - 真实 ONNX Runtime 集成（增强 / 矫正）
  - PaddleOCR 端侧推理（OCR）
  - tile-based 大图分块
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

# ---- 常量 ---------------------------------------------------------------------

# 支持的图像格式（CLAUDE.md §phase-14 §10 红线）
SUPPORTED_FORMATS: frozenset[str] = frozenset(
    {
        "png",
        "jpg",
        "jpeg",
        "webp",
        "bmp",
        "tif",
        "tiff",
    }
)

# 文件大小上限（50MB，金融文档扫描件通常 5-30MB）
MAX_IMAGE_BYTES: int = 50 * 1024 * 1024

# tile-based 推理 tile_size 上限（避免 OOM，V1 接力）
MAX_TILE_SIZE: int = 1024


# ---- 任务类型枚举 ---------------------------------------------------------------


class ProcessingType(str, Enum):
    """处理任务类型。"""

    ENHANCE = "enhance"  # 超分辨率
    CORRECT = "correct"  # 几何/色彩矫正
    OCR = "ocr"  # 文字识别


class EnhanceAlgorithm(str, Enum):
    """超分算法。V0 占位 + V1 ONNX 接力。"""

    REALSERGAN_X2 = "realesrgan_x2"  # Real-ESRGAN 2x 通用（V1 接力）
    REALSERGAN_X4 = "realesrgan_x4"  # Real-ESRGAN 4x 通用（V1 接力）
    MOCK_X2 = "mock_x2"  # V0 mock（不真做超分，仅做最近邻上采样）


class CorrectionType(str, Enum):
    """矫正类型。V0 占位 + V1 OpenCV 接力。"""

    PERSPECTIVE = "perspective"  # 透视矫正（文档四角校正）
    DESKEW = "deskew"  # 倾斜矫正
    DENOISE = "denoise"  # 去噪
    MOCK = "mock"  # V0 mock


class OcrEngine(str, Enum):
    """OCR 引擎。V0 占位 + V1 PaddleOCR 接力。"""

    PADDLEOCR = "paddleocr"  # PaddleOCR 端侧（V1 接力）
    MOCK = "mock"  # V0 mock（返空字符串）


# ---- Request / Response 数据类 ----------------------------------------------


@dataclass
class EnhanceRequest:
    """超分请求。"""

    input_path: str
    output_path: str
    algorithm: EnhanceAlgorithm = EnhanceAlgorithm.MOCK_X2
    tile_size: int = 512  # V1 接力 ONNX 时按此分块
    device: Literal["cpu", "cuda", "auto"] = "auto"  # V1 接力 ONNX 时用


@dataclass
class EnhanceResponse:
    """超分响应。"""

    ok: bool
    output_path: str
    input_size_bytes: int = 0
    output_size_bytes: int = 0
    elapsed_ms: int = 0
    backend: str = "mock"  # V0 = mock, V1 = onnx/real
    device: str = "cpu"
    error: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class CorrectRequest:
    """矫正请求。"""

    input_path: str
    output_path: str
    correction_type: CorrectionType = CorrectionType.MOCK
    auto_detect: bool = True  # 自动检测需要矫正的类型（V1 接力）


@dataclass
class CorrectResponse:
    """矫正响应。"""

    ok: bool
    output_path: str
    correction_applied: str | None = None
    elapsed_ms: int = 0
    backend: str = "mock"
    error: str | None = None
    meta: dict = field(default_factory=dict)


@dataclass
class OcrRequest:
    """OCR 请求。"""

    input_path: str
    engine: OcrEngine = OcrEngine.MOCK
    languages: tuple[str, ...] = ("ch", "en")  # 默认中文 + 英文
    confidence_threshold: float = 0.5  # V1 接力 PaddleOCR 时用
    device: Literal["cpu", "cuda", "auto"] = "auto"


@dataclass
class OcrResponse:
    """OCR 响应。

    Attributes:
        ok: 是否成功。
        text: 识别出的全文（按行拼接）。
        blocks: 文本块列表（每块含 text / bbox / confidence）—— V1 接力。
        confidence: 平均置信度（V1 接力）。
        elapsed_ms: 耗时。
        engine: 实际使用的引擎。
        error: 错误码（None 表示成功）。
        meta: 元数据（page_count / char_count / langs）。
    """

    ok: bool
    text: str = ""
    blocks: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    elapsed_ms: int = 0
    engine: str = "mock"
    error: str | None = None
    meta: dict = field(default_factory=dict)


# ---- 后端协议（V1 真实实现时实现） ---------------------------------------------


@runtime_checkable
class EnhancementBackend(Protocol):
    """超分后端协议。V0 占位（mock）+ V1 接力 ONNX。"""

    name: str

    async def enhance(self, request: EnhanceRequest) -> EnhanceResponse: ...


@runtime_checkable
class CorrectionBackend(Protocol):
    """矫正后端协议。V0 占位（mock）+ V1 接力 OpenCV。"""

    name: str

    async def correct(self, request: CorrectRequest) -> CorrectResponse: ...


@runtime_checkable
class OcrBackend(Protocol):
    """OCR 后端协议。V0 占位（mock）+ V1 接力 PaddleOCR。"""

    name: str

    async def ocr(self, request: OcrRequest) -> OcrResponse: ...


# ---- 错误类型 ---------------------------------------------------------------


class ImageProcessingError(Exception):
    """图像处理通用错误。"""


class BackendUnavailableError(ImageProcessingError):
    """后端不可用（V1 ONNX Runtime 未安装 / PaddleOCR 未安装）。"""


class UnsupportedFormatError(ImageProcessingError):
    """不支持的图像格式。"""

    def __init__(self, fmt: str, supported: frozenset[str]) -> None:
        self.fmt = fmt
        self.supported = supported
        super().__init__(f"unsupported image format: '{fmt}' (supported: {sorted(supported)})")


class FileSizeExceededError(ImageProcessingError):
    """文件大小超限。"""

    def __init__(self, size: int, limit: int) -> None:
        self.size = size
        self.limit = limit
        super().__init__(
            f"image file size {size} bytes exceeds limit {limit} bytes ({size // (1024 * 1024)}MB > {limit // (1024 * 1024)}MB)"
        )


# ---- 工具函数 ---------------------------------------------------------------


def get_image_format(path: str | Path) -> str | None:
    """从文件路径推断图像格式（小写后缀）。"""
    p = Path(path)
    suffix = p.suffix.lower().lstrip(".")
    if not suffix:
        return None
    # jpg/jpeg 归一化为 jpeg
    if suffix == "jpg":
        return "jpeg"
    return suffix


def is_supported_format(path: str | Path) -> bool:
    """判断路径是否对应支持的图像格式。"""
    fmt = get_image_format(path)
    return fmt is not None and fmt in SUPPORTED_FORMATS


def check_file_size(path: str | Path, limit: int = MAX_IMAGE_BYTES) -> int:
    """检查文件大小是否超限。返文件大小（字节）。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"image file not found: {path}")
    size = p.stat().st_size
    if size > limit:
        raise FileSizeExceededError(size, limit)
    return size
