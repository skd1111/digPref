"""OnnxEmbeddingClient —— 进程内向量模型（2026-08-31）。

bge-small-zh-v1.5 ONNX 量化版直接跑在 Agent 进程内（onnxruntime CPU，
无 CUDA / 无子进程 / 无端口）：意图语义路由、Few-Shot 检索、KB 向量化
共用同一实例。单条推理 10-30ms 级，满足实时意图路由需求。

设计约束：
    - 懒加载单例：首次 embed 才建 session（约 1-2s），此后常驻；
    - 模型文件缺失 / 依赖缺失 → 客户端不可用，调用方静默回退（与
      LocalEmbeddingClient 的零向量契约一致）；
    - CLS pooling + L2 归一化（BGE 官方推理方式）；
    - 同步推理经 asyncio.to_thread 转异步，不阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import logging
import math
import sys
import threading
from pathlib import Path
from typing import Any

from agent.config import settings

logger = logging.getLogger("agent.llm.onnx_embedding")

_MAX_SEQ_LEN = 512  # bge-small-zh-v1.5 位置编码上限
_PAD_TOKEN = "[PAD]"


def _resolve_model_dir(raw: str) -> Path:
    """模型目录解析：显式配置（cwd 相对）> PyInstaller _MEIPASS > 仓库根推导。

    与 knowledge-base / config/biz_dict 同策略（spec datas 已将
    model/bge-small-zh-v1.5-onnx 打进 _MEIPASS）；打包后 exe 可能在任意
    工作目录启动，多级回退缺失时返原路径（客户端自行标记不可用降级）。
    """
    base = Path(raw)
    if base.is_dir():
        return base
    if not base.is_absolute():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bundled = Path(meipass) / raw
            if bundled.is_dir():
                return bundled
        try:
            # onnx_embedding.py → llm → agent → src → agent → services → 仓库根
            repo_root = Path(__file__).resolve().parents[5]
            derived = repo_root / raw
            if derived.is_dir():
                return derived
        except IndexError:
            pass
    return base


class OnnxEmbeddingClient:
    """进程内 ONNX 向量客户端——接口与 LocalEmbeddingClient 对齐。

    embed / embed_batch 失败返零向量（调用方以 any(vec) 判定降级），
    与 HTTP 客户端的容错契约一致，语义路由等上层无需分支处理。
    """

    def __init__(self, model_dir: str | None = None) -> None:
        self._model_dir = _resolve_model_dir(model_dir or settings.local_embedding_onnx_dir)
        self.model = settings.local_embedding_model or "bge-small-zh-v1.5"
        self.dimensions = settings.local_embedding_dim
        self._session: Any | None = None
        self._tokenizer: Any | None = None
        self._lock = threading.Lock()
        self._unavailable = False

    # ---- 加载 ---------------------------------------------------------------

    def _model_file(self) -> Path | None:
        onnx_dir = self._model_dir / "onnx"
        for name in ("model_quantized.onnx", "model.onnx"):
            p = onnx_dir / name
            if p.is_file():
                return p
        return None

    def _ensure_loaded(self) -> bool:
        """懒加载 tokenizer + session（线程安全）；不可用返 False。"""
        if self._unavailable:
            return False
        if self._session is not None and self._tokenizer is not None:
            return True
        with self._lock:
            if self._session is not None and self._tokenizer is not None:
                return True
            if self._unavailable:
                return False
            model_file = self._model_file()
            tokenizer_file = self._model_dir / "tokenizer.json"
            if model_file is None or not tokenizer_file.is_file():
                logger.info(
                    "onnx_embedding: model files missing under %s → unavailable",
                    self._model_dir,
                )
                self._unavailable = True
                return False
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
                self._tokenizer.enable_truncation(max_length=_MAX_SEQ_LEN)
                opts = ort.SessionOptions()
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    str(model_file), sess_options=opts, providers=["CPUExecutionProvider"]
                )
            except Exception as exc:
                logger.warning("onnx_embedding: load failed: %s", exc)
                self._session = None
                self._tokenizer = None
                self._unavailable = True
                return False
        logger.info("onnx_embedding: loaded %s", model_file.name)
        return True

    # ---- 推理（同步，跑在线程池）---------------------------------------------

    def _pad_id(self) -> int:
        pad = self._tokenizer.token_to_id(_PAD_TOKEN) if self._tokenizer else None
        return pad if pad is not None else 0

    def _encode_batch_sync(self, texts: list[str]) -> list[list[float]]:
        """批量推理：动态补齐到批内最大长（省 CPU）→ CLS pooling → L2。"""
        if not self._ensure_loaded() or not texts:
            return [[0.0] * self.dimensions] * len(texts)
        tokenizer, session = self._tokenizer, self._session
        if tokenizer is None or session is None:  # 理论上不可达（并发防护）
            return [[0.0] * self.dimensions] * len(texts)
        encodings = [tokenizer.encode(t) for t in texts]
        max_len = max((len(e.ids) for e in encodings), default=0) or 1
        pad = self._pad_id()
        input_ids, attention_mask, token_type_ids = [], [], []
        for enc in encodings:
            ids = list(enc.ids)
            pad_len = max_len - len(ids)
            input_ids.append(ids + [pad] * pad_len)
            attention_mask.append([1] * len(ids) + [0] * pad_len)
            token_type_ids.append(list(enc.type_ids) + [0] * pad_len)
        feeds = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        }
        # int64 喂给量化模型：ORT 内部按图签名转换，显式转 int32 反而可能不匹配
        import numpy as np

        feeds_np = {k: np.asarray(v, dtype=np.int64) for k, v in feeds.items()}
        try:
            (last_hidden,) = session.run(["last_hidden_state"], feeds_np)
        except Exception as exc:
            logger.warning("onnx_embedding: run failed: %s", exc)
            return [[0.0] * self.dimensions] * len(texts)
        vectors: list[list[float]] = []
        for row in last_hidden[:, 0, :]:  # CLS pooling
            vec = [float(x) for x in row]
            norm = math.sqrt(sum(x * x for x in vec))
            vectors.append([x / norm for x in vec] if norm > 0 else vec)
        return vectors

    # ---- Public API（异步，与 LocalEmbeddingClient 对齐）----------------------

    async def embed(self, text: str) -> list[float]:
        try:
            results = await asyncio.to_thread(self._encode_batch_sync, [text])
            return results[0] if results else [0.0] * self.dimensions
        except Exception as exc:
            logger.debug("onnx_embedding: embed failed: %s", exc)
            return [0.0] * self.dimensions

    async def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        results: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            try:
                results.extend(await asyncio.to_thread(self._encode_batch_sync, batch))
            except Exception as exc:
                logger.debug("onnx_embedding: embed_batch failed at %d: %s", i, exc)
                results.extend([[0.0] * self.dimensions] * len(batch))
        return results

    async def health_check(self) -> bool:
        """模型文件就绪即可用（进程内加载，无网络依赖）。"""
        return await asyncio.to_thread(self._ensure_loaded)


# ---- 单例 --------------------------------------------------------------------

_default_client: OnnxEmbeddingClient | None = None


def get_onnx_embedding_client() -> OnnxEmbeddingClient:
    global _default_client
    if _default_client is None:
        _default_client = OnnxEmbeddingClient()
    return _default_client


def reset_onnx_embedding_client() -> None:
    """测试隔离用（切换模型目录后重建）。"""
    global _default_client
    _default_client = None
