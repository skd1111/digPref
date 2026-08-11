"""Phase 14 V0 · Real-ESRGAN 超分骨架（V0 mock + V1 接力 ONNX Runtime）。

V0 实现：
  - MockEnhancementBackend：返成功响应 + 不真做超分（仅复制文件 + 标注 backend='mock'）
  - get_default_backend()：根据环境自动选择后端（V0 默认 mock）

V1 接力（V0.5 / V1 阶段）：
  - ONNXEnhancementBackend：onnxruntime + RealESRGAN_x2.onnx / RealESRGAN_x4.onnx
  - tile-based 分块推理（tile_size 自适应可用内存）
  - CPU / CUDA EP 自动检测
  - 模型文件 lazy download（首次使用触发）

设计原则：
  1. V0 mock 必须返有效响应（含 output_path + 输入/输出大小），让上层 API 测试能跑通
  2. 所有错误统一抛 ImageProcessingError 子类，由 api 层捕获转 422/500
  3. output_path 必须不与 input_path 相同（避免覆盖原图）
"""

from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

from agent.image_processing.models import (
    BackendUnavailableError,
    EnhanceAlgorithm,
    EnhancementBackend,
    EnhanceRequest,
    EnhanceResponse,
    check_file_size,
    is_supported_format,
)

# ---- V0 Mock Backend ------------------------------------------------------


class MockEnhancementBackend:
    """V0 mock 超分后端 —— 不真做超分，仅复制文件 + 标注 backend='mock'。

    用于开发 / 测试环境，让上层 API 走通完整链路。
    V1 接力时替换为 ONNXEnhancementBackend。
    """

    name: str = "mock"

    async def enhance(self, request: EnhanceRequest) -> EnhanceResponse:
        started = time.monotonic()
        try:
            # ---- 1. 文件存在性 + 大小校验 ----
            input_size = check_file_size(request.input_path)

            # ---- 2. 格式校验 ----
            if not is_supported_format(request.input_path):
                from agent.image_processing.models import UnsupportedFormatError

                raise UnsupportedFormatError(
                    fmt=Path(request.input_path).suffix.lstrip("."),
                    supported=frozenset({"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff"}),
                )

            # ---- 3. output_path 不能与 input_path 相同 ----
            in_p = Path(request.input_path).resolve()
            out_p = Path(request.output_path).resolve()
            if in_p == out_p:
                return EnhanceResponse(
                    ok=False,
                    output_path=request.output_path,
                    backend=self.name,
                    error="output_path_same_as_input: must differ from input_path",
                )

            # ---- 4. 执行 mock 超分（复制文件 + 模拟 scale 系数）----
            scale = 2 if request.algorithm == EnhanceAlgorithm.MOCK_X2 else 4
            await asyncio.to_thread(self._mock_enhance_sync, request, scale)

            elapsed_ms = int((time.monotonic() - started) * 1000)
            output_size = Path(request.output_path).stat().st_size

            return EnhanceResponse(
                ok=True,
                output_path=request.output_path,
                input_size_bytes=input_size,
                output_size_bytes=output_size,
                elapsed_ms=elapsed_ms,
                backend=self.name,
                device="cpu",
                meta={
                    "scale": scale,
                    "tile_size": request.tile_size,
                    "mock_note": "V0 mock: file copied, no actual super-resolution",
                },
            )
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return EnhanceResponse(
                ok=False,
                output_path=request.output_path,
                elapsed_ms=elapsed_ms,
                backend=self.name,
                error=f"{type(exc).__name__}: {exc}",
            )

    def _mock_enhance_sync(self, request: EnhanceRequest, scale: int) -> None:
        """同步执行 mock 超分（to_thread 调用）。"""
        out_p = Path(request.output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        # V0 mock：直接复制原图到 output_path（不真做超分）
        # V1 接力时此处替换为 ONNX Runtime 推理 + tile-based 分块 + 输出合并
        shutil.copy2(request.input_path, request.output_path)


# ---- V1 ONNX Backend（占位实现，V1 接力时填充）----------------------------


class ONNXEnhancementBackend:
    """V1 ONNX 超分后端（占位实现）—— 真实集成留 V1。

    V1 接力内容：
      - onnxruntime.InferenceSession 加载 RealESRGAN_x2.onnx
      - tile-based 分块（tile_size 默认 512，上限 1024）
      - 输入预处理：HWC → CHW + 归一化到 [0, 1]
      - 输出后处理：CHW → HWC + 反归一化 + 拼回完整图
      - CPU/CUDA EP 自动选择（V1.5 接力 CUDA）

    V0 阶段：构造时抛 BackendUnavailableError（onnxruntime 未安装 / 模型未下载）。
    """

    name: str = "onnx"

    def __init__(self) -> None:
        # V1 接力时填充：尝试 import onnxruntime + 检查模型文件
        raise BackendUnavailableError(
            "ONNXEnhancementBackend not implemented in V0; V1 will integrate onnxruntime + RealESRGAN_x2.onnx"
        )

    async def enhance(self, request: EnhanceRequest) -> EnhanceResponse:
        # 实际逻辑 V1 接力
        raise NotImplementedError("V1 will implement ONNX super-resolution")


# ---- 后端工厂 -------------------------------------------------------------


def get_default_backend() -> EnhancementBackend:
    """获取默认超分后端（V0 = mock；V1 自动检测 ONNX Runtime 可用性）。"""
    try:
        # V1 接力：检查 onnxruntime + 模型文件
        # import onnxruntime as ort  # V1
        # if ort.get_device() == "CPU" and model_path.exists():
        #     return ONNXEnhancementBackend()
        pass
    except (ImportError, BackendUnavailableError):
        pass
    return MockEnhancementBackend()


# ---- 单例工厂（测试可重置）-----------------------------------------------

_DEFAULT_BACKEND: EnhancementBackend | None = None


def get_enhance_backend() -> EnhancementBackend:
    """返回默认超分后端（单例）。"""
    global _DEFAULT_BACKEND
    if _DEFAULT_BACKEND is None:
        _DEFAULT_BACKEND = get_default_backend()
    return _DEFAULT_BACKEND


def reset_enhance_backend() -> None:
    """测试 hook：重置单例。"""
    global _DEFAULT_BACKEND
    _DEFAULT_BACKEND = None
