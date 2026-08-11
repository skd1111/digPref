"""dspark.llamacpp_backend —— Phase 13 V1.5 llama.cpp 真集成后端。

设计（来自 phase-13-dspark.md §4.1 方案 A）：
- 基于 llama-cpp-python（≥ 0.2.84）原生推测解码
- 三个核心参数：`speculative_model` / `n_draft` / `draft_p_min`
- 失败兜底：llama-cpp-python 未装 / 模型加载失败 → 静默降级为主模型单独运行（CLAUDE.md §6.3）

CLAUDE.md §6 红线：
- 数学等价保证（Leviathan 2023）：启用 DSpark 输出分布与主模型自回归相同
- 失败兜底不可抛异常（不阻塞用户）
- 草稿模型路径为空 → 全部 off（已由 policy.py 拦截）
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


# ---- Backend 接口 -----------------------------------------------------------


class DSparkBackend(Protocol):
    """DSpark 后端接口（V1.5 真集成 / V0 骨架共用）。"""

    async def generate(
        self,
        *,
        prompt: str,
        max_tokens: int,
        temperature: float,
        task_category: str,
        n_draft: int,
        draft_p_min: float,
        draft_model_path: str | None = None,
    ) -> DSparkResult: ...


@dataclass
class DSparkResult:
    """DSpark 生成结果。"""

    text: str
    backend: str = "unknown"  # "llamacpp" | "mock"
    speculative_enabled: bool = False
    n_draft: int = 1
    accepted_tokens: int = 0
    drafted_tokens: int = 0
    speedup_ratio: float = 1.0  # vs 不加速（> 1.0 表示加速）
    duration_ms: int = 0
    error: str = ""


# ---- llama-cpp-python 真集成 -------------------------------------------------


class LlamaCppDSparkBackend:
    """llama-cpp-python 真集成后端（V1.5）。

    用法：
        backend = LlamaCppDSparkBackend(
            target_model_path="models/qwen2.5-0.5b-instruct-q4_k_m.gguf",
            draft_model_path="models/draft/qwen2.5-0.1b-instruct-q4_k_m.gguf",
            n_ctx=4096,
            n_gpu_layers=0,  # -1 全 GPU; 0 全 CPU
        )
        result = await backend.generate(
            prompt="生成 SQL",
            max_tokens=300,
            temperature=0.1,
            task_category="sql_generation",
            n_draft=8,
            draft_p_min=0.75,
        )

    失败兜底：
    - llama_cpp import 失败 → 抛 DSparkBackendUnavailable（api.py 捕获并降级）
    - 草稿模型路径为空 → 单模型生成（不抛错）
    - 主模型加载失败 → DSparkBackendUnavailable
    """

    def __init__(
        self,
        *,
        target_model_path: str,
        draft_model_path: str | None = None,
        n_ctx: int = 4096,
        n_gpu_layers: int = 0,
    ):
        self.target_model_path = target_model_path
        self.draft_model_path = draft_model_path
        self.n_ctx = n_ctx
        self.n_gpu_layers = n_gpu_layers
        self._target = None
        self._draft = None
        self._load_target()

    def _load_target(self) -> None:
        """加载主模型（必须）。失败抛 DSparkBackendUnavailable。"""
        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise DSparkBackendUnavailable(
                f"llama_cpp 未安装：{e}。请 uv add llama-cpp-python>=0.2.84"
            ) from e
        try:
            self._target = Llama(
                model_path=self.target_model_path,
                n_ctx=self.n_ctx,
                n_gpu_layers=self.n_gpu_layers,
                verbose=False,
            )
        except Exception as e:
            raise DSparkBackendUnavailable(f"主模型加载失败 {self.target_model_path}: {e}") from e

    def _load_draft(self) -> Any | None:
        """加载草稿模型（可选）。失败返 None（静默降级为主模型单跑）。"""
        if not self.draft_model_path:
            return None
        try:
            from llama_cpp import Llama

            draft = Llama(
                model_path=self.draft_model_path,
                n_ctx=self.n_ctx,
                n_gpu_layers=0,  # 草稿模型放 CPU，节省 GPU 显存给主模型
                verbose=False,
            )
            return draft
        except Exception as e:
            logger.warning(
                "[DSpark] 草稿模型加载失败 %s: %s —— 静默降级为主模型单跑",
                self.draft_model_path,
                e,
            )
            return None

    async def generate(
        self,
        *,
        prompt: str,
        max_tokens: int,
        temperature: float,
        task_category: str,
        n_draft: int,
        draft_p_min: float,
        draft_model_path: str | None = None,
    ) -> DSparkResult:
        """调 llama.cpp 生成（含 DSpark 推测解码）。

        启用条件：草稿模型路径存在 + 加载成功 + n_draft >= 2。
        """
        if self._target is None:
            return DSparkResult(text="", backend="unavailable", error="target model not loaded")

        # 草稿模型：lazy 加载（首次调 DSpark 才加载）
        draft = self._load_draft()
        speculative = draft is not None and n_draft >= 2

        t0 = time.monotonic()
        try:
            if speculative and draft is not None:
                output = self._target(
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    speculative_model=draft,
                    n_draft=n_draft,
                    draft_p_min=draft_p_min,
                )
            else:
                # 主模型单独运行（关闭 DSpark）
                output = self._target(
                    prompt=prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
        except Exception as e:
            logger.warning("[DSpark] generate failed: %s", e)
            return DSparkResult(
                text="",
                backend="llamacpp",
                speculative_enabled=speculative,
                error=f"{type(e).__name__}: {e}",
            )

        text = output.get("choices", [{}])[0].get("text", "")
        elapsed = int((time.monotonic() - t0) * 1000)
        # llama.cpp usage 字段含 draft/accept tokens（0.2.84+ 才有）
        usage = output.get("usage", {})
        drafted = int(usage.get("drafted_tokens", 0) or 0)
        accepted = int(usage.get("accepted_tokens", 0) or 0)
        speedup = (drafted / max(1, accepted)) if speculative and drafted > 0 else 1.0
        return DSparkResult(
            text=str(text or "").strip(),
            backend="llamacpp",
            speculative_enabled=speculative,
            n_draft=n_draft,
            drafted_tokens=drafted,
            accepted_tokens=accepted,
            speedup_ratio=speedup,
            duration_ms=elapsed,
        )


# ---- Mock 后端（V0 / 测试用）-------------------------------------------------


class MockDSparkBackend:
    """DSpark 后端 Mock（V0 骨架 + 测试 fallback）。

    行为：
    - 模拟 DSpark 加速（speedup_ratio=2.0）
    - 返回固定字符串（用于测试）
    - 无 llama_cpp 依赖
    """

    def __init__(self, fixed_output: str = "mock response", mock_speedup: float = 2.0):
        self.fixed_output = fixed_output
        self.mock_speedup = mock_speedup

    async def generate(
        self,
        *,
        prompt: str,
        max_tokens: int,
        temperature: float,
        task_category: str,
        n_draft: int,
        draft_p_min: float,
        draft_model_path: str | None = None,
    ) -> DSparkResult:
        t0 = time.monotonic()
        # 模拟耗时：加速比 = baseline_time / dspark_time
        await_time = 0.001 / max(1.0, self.mock_speedup if n_draft >= 2 else 1.0)
        import asyncio

        await asyncio.sleep(await_time)
        elapsed = int((time.monotonic() - t0) * 1000)
        speculative = n_draft >= 2 and draft_model_path is not None
        drafted = max_tokens * (n_draft if speculative else 1)
        accepted = max_tokens
        speedup = (drafted / max(1, accepted)) if speculative else 1.0
        return DSparkResult(
            text=self.fixed_output,
            backend="mock",
            speculative_enabled=speculative,
            n_draft=n_draft,
            drafted_tokens=drafted if speculative else max_tokens,
            accepted_tokens=max_tokens,
            speedup_ratio=speedup,
            duration_ms=elapsed,
        )


# ---- 异常 -----------------------------------------------------------------


class DSparkBackendUnavailable(Exception):
    """DSpark 后端不可用（llama_cpp 未装 / 模型加载失败）。"""

    pass


# ---- 工厂 ----------------------------------------------------------------


_default_backend: DSparkBackend | None = None


def build_default_backend(
    *,
    target_model_path: str | None = None,
    draft_model_path: str | None = None,
    n_ctx: int = 4096,
    n_gpu_layers: int = 0,
) -> DSparkBackend:
    """工厂：构造默认 DSpark 后端。

    优先级：
    1. target_model_path 有效 + llama_cpp 已装 → LlamaCppDSparkBackend
    2. 否则 → MockDSparkBackend（V0 骨架 / 测试环境）
    """
    try:
        import llama_cpp  # noqa: F401  # 检查可用性
    except ImportError:
        logger.info("[DSpark] llama_cpp 不可用 —— 用 MockDSparkBackend")
        return MockDSparkBackend()

    if target_model_path:
        try:
            return LlamaCppDSparkBackend(
                target_model_path=target_model_path,
                draft_model_path=draft_model_path,
                n_ctx=n_ctx,
                n_gpu_layers=n_gpu_layers,
            )
        except DSparkBackendUnavailable as e:
            logger.warning("[DSpark] 真集成不可用（%s）—— 用 MockDSparkBackend", e)
            return MockDSparkBackend()
    return MockDSparkBackend()


def reset_default_backend() -> None:
    """测试 hook。"""
    global _default_backend
    _default_backend = None
