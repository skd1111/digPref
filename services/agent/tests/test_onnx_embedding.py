"""进程内 ONNX 向量模型测试（2026-08-31）：路径多级回退 + 不可用降级。"""

from __future__ import annotations

import sys
from pathlib import Path

from agent.llm.onnx_embedding import OnnxEmbeddingClient, _resolve_model_dir

_MODEL_REL = "model/bge-small-zh-v1.5-onnx"


class TestResolveModelDir:
    def test_explicit_existing_dir_wins(self, tmp_path):
        d = tmp_path / "anywhere"
        d.mkdir()
        assert _resolve_model_dir(str(d)) == d

    def test_meipass_fallback_when_cwd_missing(self, tmp_path, monkeypatch):
        """打包形态：cwd 无模型 → 回退 _MEIPASS 内置副本（spec datas 落点）。"""
        meipass = tmp_path / "meipass"
        (meipass / _MODEL_REL).mkdir(parents=True)
        monkeypatch.setattr(sys, "_MEIPASS", str(meipass), raising=False)
        resolved = _resolve_model_dir(_MODEL_REL)
        assert resolved == meipass / _MODEL_REL

    def test_missing_everywhere_returns_base(self, tmp_path, monkeypatch):
        """全部缺失 → 返原路径（客户端标记不可用，静默降级）。"""
        monkeypatch.delattr(sys, "_MEIPASS", raising=False)
        resolved = _resolve_model_dir("definitely/missing/dir")
        assert resolved == Path("definitely/missing/dir")

    def test_absolute_missing_no_fallback(self, tmp_path):
        """绝对路径不参与 _MEIPASS / 仓库根回退。"""
        resolved = _resolve_model_dir(str(tmp_path / "nope"))
        assert resolved == tmp_path / "nope"


class TestUnavailableClient:
    async def test_missing_model_health_false_and_zero_vectors(self, tmp_path):
        client = OnnxEmbeddingClient(str(tmp_path / "missing"))
        assert await client.health_check() is False
        vec = await client.embed("查询订单表")
        assert len(vec) == client.dimensions
        assert not any(vec)  # 零向量契约 → 上层静默回退

    async def test_missing_model_batch_zero_vectors(self, tmp_path):
        client = OnnxEmbeddingClient(str(tmp_path / "missing"))
        vecs = await client.embed_batch(["a", "b", "c"])
        assert len(vecs) == 3
        assert all(not any(v) for v in vecs)
