"""knowledge.reranker —— 进程内 ONNX Cross-Encoder 重排（bge-reranker）。

混合检索（FTS5 BM25 + 向量 RRF）召回 Top-N 后，用交叉编码器对 (query, chunk)
逐对深度打分重排取 Top-K —— 双塔向量缺乏 query/doc 交互，reranker 补上排序精度
（业界「检索后补救」首选，纯 CPU 几十毫秒级，契合桌面无 GPU 场景）。

设计约束（与 onnx_embedding 同源）：
    - 懒加载单例：首次 rerank 才建 session；模型文件缺失/依赖缺失 → 不可用，
      rerank() 返 None，调用方保持 RRF 原序（no-op 降级，功能不中断）；
    - 量化 ONNX 模型随安装包分发（local_reranker_onnx_dir）；
    - 同步推理经 asyncio.to_thread 转异步，不阻塞事件循环；
    - feed 按 session 实际输入名动态构造（兼容 XLM-R / BERT 系不同签名）。
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path
from typing import Any

from agent.config import settings

logger = logging.getLogger("agent.knowledge.reranker")

_MAX_SEQ_LEN = 512


def _resolve_model_dir(raw: str) -> Path:
    """模型目录解析：显式配置（cwd 相对）> PyInstaller _MEIPASS > 仓库根推导。

    与 onnx_embedding._resolve_model_dir 同策略（spec datas 打进 _MEIPASS）。
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
            # reranker.py -> knowledge -> agent -> src -> agent -> services -> 仓库根
            repo_root = Path(__file__).resolve().parents[5]
            derived = repo_root / raw
            if derived.is_dir():
                return derived
        except IndexError:
            pass
    return base


class OnnxRerankerClient:
    """进程内 ONNX 交叉编码器重排客户端。

    rerank(query, docs) -> 与 docs 等长的相关性分数列表；不可用返 None。
    """

    def __init__(self, model_dir: str | None = None) -> None:
        self._model_dir = _resolve_model_dir(model_dir or settings.local_reranker_onnx_dir)
        self.model = "bge-reranker"
        self._session: Any | None = None
        self._tokenizer: Any | None = None
        self._input_names: list[str] = []
        self._lock = threading.Lock()
        self._unavailable = False

    def _model_file(self) -> Path | None:
        onnx_dir = self._model_dir / "onnx"
        for name in ("model_quantized.onnx", "model.onnx"):
            p = onnx_dir / name
            if p.is_file():
                return p
        # 有些导出直接把 onnx 放模型根目录
        for name in ("model_quantized.onnx", "model.onnx"):
            p = self._model_dir / name
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
                    "reranker: model files missing under %s -> unavailable (rerank no-op)",
                    self._model_dir,
                )
                self._unavailable = True
                return False
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                self._tokenizer = Tokenizer.from_file(str(tokenizer_file))
                self._tokenizer.enable_truncation(max_length=_MAX_SEQ_LEN)
                try:
                    self._tokenizer.enable_padding()
                except Exception:  # pragma: no cover - 部分 tokenizer 无 pad 配置
                    pass
                opts = ort.SessionOptions()
                opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                self._session = ort.InferenceSession(
                    str(model_file), sess_options=opts, providers=["CPUExecutionProvider"]
                )
                self._input_names = [i.name for i in self._session.get_inputs()]
            except Exception as exc:
                logger.warning("reranker: load failed: %s", exc)
                self._session = None
                self._tokenizer = None
                self._unavailable = True
                return False
        logger.info("reranker: loaded %s inputs=%s", model_file.name, self._input_names)
        return True

    def _score_sync(self, query: str, docs: list[str]) -> list[float] | None:
        if not self._ensure_loaded() or not docs:
            return None
        tokenizer, session = self._tokenizer, self._session
        if tokenizer is None or session is None:  # pragma: no cover
            return None
        try:
            import numpy as np

            pairs = [[query, d] for d in docs]
            encodings = tokenizer.encode_batch(pairs)
            feeds: dict[str, Any] = {}
            ids = np.asarray([e.ids for e in encodings], dtype=np.int64)
            mask = np.asarray([e.attention_mask for e in encodings], dtype=np.int64)
            if "input_ids" in self._input_names:
                feeds["input_ids"] = ids
            if "attention_mask" in self._input_names:
                feeds["attention_mask"] = mask
            if "token_type_ids" in self._input_names:
                feeds["token_type_ids"] = np.asarray(
                    [e.type_ids for e in encodings], dtype=np.int64
                )
            outputs = session.run(None, feeds)
            logits = outputs[0]
            # 输出形如 [N, 1]（单 logit）或 [N, 2]（取正类）或 [N]
            arr = np.asarray(logits)
            if arr.ndim == 2:
                scores = arr[:, 0] if arr.shape[1] == 1 else arr[:, -1]
            else:
                scores = arr.reshape(-1)
            return [float(s) for s in scores]
        except Exception as exc:
            logger.warning("reranker: run failed: %s", exc)
            return None

    async def rerank(self, query: str, docs: list[str]) -> list[float] | None:
        """异步重排打分；不可用/异常返 None（调用方保持原序）。"""
        if not docs:
            return None
        try:
            return await asyncio.to_thread(self._score_sync, query, docs)
        except Exception as exc:  # pragma: no cover
            logger.debug("reranker: rerank failed: %s", exc)
            return None

    async def health_check(self) -> bool:
        return await asyncio.to_thread(self._ensure_loaded)

    def model_present(self) -> bool:
        """模型文件是否就绪（不触发加载，供状态上报）。"""
        return self._model_file() is not None and (self._model_dir / "tokenizer.json").is_file()

    @property
    def available(self) -> bool:
        return not self._unavailable


# ---- 单例 --------------------------------------------------------------------

_default_client: OnnxRerankerClient | None = None


def get_reranker_client() -> OnnxRerankerClient:
    global _default_client
    if _default_client is None:
        _default_client = OnnxRerankerClient()
    return _default_client


def reset_reranker_client() -> None:
    """测试隔离用（切换模型目录后重建）。"""
    global _default_client
    _default_client = None
