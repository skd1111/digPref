"""Phase 14 V0 · 单元测试 + 集成测试（30+ 用例）。

覆盖:
  - models: 数据类 / 枚举 / 工具函数（get_image_format / is_supported_format / check_file_size）
  - enhance: MockEnhancementBackend 完整链路（增强复制 + scale 模拟）
  - correct: MockCorrectionBackend 完整链路
  - ocr: MockOcrBackend 完整链路（空文本 + mock_note）
  - storage: image_processing_tasks 表 CRUD + stats
  - events: emit + consume + flush
  - api: 5 端点（enhance / correct / ocr / list_tasks / get_task / stats）
  - SSE 三处同步: stream.py::_CHANNEL_BY_KIND 含 3 通道
  - _LOCAL_ONLY_TASKS: image_processing_summary 注入
"""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest


# ---- models 测试 ----------------------------------------------------------

class TestModels:
    """数据类 / 枚举 / 工具函数测试。"""

    def test_supported_formats_count(self):
        from agent.image_processing.models import SUPPORTED_FORMATS
        # png / jpg / jpeg / webp / bmp / tif / tiff = 7
        assert len(SUPPORTED_FORMATS) == 7

    def test_max_image_bytes(self):
        from agent.image_processing.models import MAX_IMAGE_BYTES
        assert MAX_IMAGE_BYTES == 50 * 1024 * 1024

    def test_get_image_format_png(self):
        from agent.image_processing.models import get_image_format
        assert get_image_format("/tmp/a.png") == "png"
        assert get_image_format("/tmp/a.PNG") == "png"  # 大小写不敏感

    def test_get_image_format_jpg_normalized(self):
        from agent.image_processing.models import get_image_format
        # jpg → jpeg 归一化
        assert get_image_format("/tmp/a.jpg") == "jpeg"
        assert get_image_format("/tmp/a.jpeg") == "jpeg"

    def test_get_image_format_no_extension(self):
        from agent.image_processing.models import get_image_format
        assert get_image_format("/tmp/a") is None

    def test_is_supported_format(self):
        from agent.image_processing.models import is_supported_format
        assert is_supported_format("/tmp/a.png") is True
        assert is_supported_format("/tmp/a.webp") is True
        assert is_supported_format("/tmp/a.exe") is False
        assert is_supported_format("/tmp/a") is False

    def test_check_file_size_ok(self, tmp_path):
        from agent.image_processing.models import check_file_size
        f = tmp_path / "ok.png"
        f.write_bytes(b"x" * 100)
        assert check_file_size(f) == 100

    def test_check_file_size_missing(self, tmp_path):
        from agent.image_processing.models import check_file_size
        with pytest.raises(FileNotFoundError):
            check_file_size(tmp_path / "missing.png")

    def test_check_file_size_exceeded(self, tmp_path):
        from agent.image_processing.models import FileSizeExceededError, check_file_size
        f = tmp_path / "big.png"
        f.write_bytes(b"x" * 100)
        with pytest.raises(FileSizeExceededError):
            check_file_size(f, limit=50)

    def test_unsupported_format_error(self):
        from agent.image_processing.models import (
            UnsupportedFormatError,
        )
        e = UnsupportedFormatError("exe", frozenset({"png", "jpg"}))
        assert e.fmt == "exe"
        assert "exe" in str(e)
        assert "png" in str(e)

    def test_enhance_request_defaults(self):
        from agent.image_processing.models import EnhanceAlgorithm, EnhanceRequest
        r = EnhanceRequest(input_path="/tmp/a.png", output_path="/tmp/b.png")
        assert r.algorithm == EnhanceAlgorithm.MOCK_X2
        assert r.tile_size == 512
        assert r.device == "auto"

    def test_ocr_request_languages_default(self):
        from agent.image_processing.models import OcrRequest
        r = OcrRequest(input_path="/tmp/a.png")
        assert r.languages == ("ch", "en")
        assert r.confidence_threshold == 0.5


# ---- enhance 测试 ---------------------------------------------------------

class TestMockEnhancement:
    """MockEnhancementBackend 测试。"""

    @pytest.mark.asyncio
    async def test_mock_enhance_success(self, tmp_path: Path):
        from agent.image_processing.enhance import (
            MockEnhancementBackend,
            reset_enhance_backend,
        )
        from agent.image_processing.models import EnhanceAlgorithm, EnhanceRequest

        reset_enhance_backend()
        in_p = tmp_path / "in.png"
        out_p = tmp_path / "out.png"
        in_p.write_bytes(b"fake png data" * 100)

        backend = MockEnhancementBackend()
        req = EnhanceRequest(
            input_path=str(in_p),
            output_path=str(out_p),
            algorithm=EnhanceAlgorithm.MOCK_X2,
        )
        result = await backend.enhance(req)
        assert result.ok
        assert result.backend == "mock"
        assert result.device == "cpu"
        assert result.input_size_bytes > 0
        assert result.output_size_bytes == result.input_size_bytes  # V0 mock 复制
        assert result.meta["scale"] == 2
        assert out_p.exists()

    @pytest.mark.asyncio
    async def test_mock_enhance_same_path_rejected(self, tmp_path):
        from agent.image_processing.enhance import MockEnhancementBackend
        from agent.image_processing.models import EnhanceRequest

        in_p = tmp_path / "in.png"
        in_p.write_bytes(b"data")
        backend = MockEnhancementBackend()
        req = EnhanceRequest(input_path=str(in_p), output_path=str(in_p))
        result = await backend.enhance(req)
        assert not result.ok
        assert "output_path_same_as_input" in result.error

    @pytest.mark.asyncio
    async def test_mock_enhance_unsupported_format(self, tmp_path):
        from agent.image_processing.enhance import MockEnhancementBackend
        from agent.image_processing.models import EnhanceRequest

        in_p = tmp_path / "in.exe"
        in_p.write_bytes(b"data")
        backend = MockEnhancementBackend()
        req = EnhanceRequest(input_path=str(in_p), output_path=str(tmp_path / "out.png"))
        result = await backend.enhance(req)
        assert not result.ok
        assert "unsupported image format" in result.error

    @pytest.mark.asyncio
    async def test_onnx_backend_v0_unavailable(self):
        from agent.image_processing.enhance import ONNXEnhancementBackend
        with pytest.raises(Exception) as exc_info:
            ONNXEnhancementBackend()
        assert "not implemented" in str(exc_info.value).lower()

    def test_get_enhance_backend_default_mock(self):
        from agent.image_processing.enhance import (
            get_enhance_backend,
            reset_enhance_backend,
        )
        reset_enhance_backend()
        b = get_enhance_backend()
        assert b.name == "mock"


# ---- correct 测试 ---------------------------------------------------------

class TestMockCorrection:
    """MockCorrectionBackend 测试。"""

    @pytest.mark.asyncio
    async def test_mock_correct_success(self, tmp_path):
        from agent.image_processing.correct import (
            MockCorrectionBackend,
            reset_correct_backend,
        )
        from agent.image_processing.models import CorrectionType, CorrectRequest

        reset_correct_backend()
        in_p = tmp_path / "in.jpg"
        out_p = tmp_path / "out.jpg"
        in_p.write_bytes(b"data")
        backend = MockCorrectionBackend()
        req = CorrectRequest(
            input_path=str(in_p),
            output_path=str(out_p),
            correction_type=CorrectionType.DESKEW,
            auto_detect=False,
        )
        result = await backend.correct(req)
        assert result.ok
        assert result.correction_applied == "deskew"
        assert result.meta["auto_detect"] is False
        assert out_p.exists()


# ---- ocr 测试 -------------------------------------------------------------

class TestMockOcr:
    """MockOcrBackend 测试。"""

    @pytest.mark.asyncio
    async def test_mock_ocr_returns_empty_text(self, tmp_path):
        from agent.image_processing.ocr import MockOcrBackend, reset_ocr_backend
        from agent.image_processing.models import OcrRequest

        reset_ocr_backend()
        in_p = tmp_path / "in.png"
        in_p.write_bytes(b"data")
        backend = MockOcrBackend()
        req = OcrRequest(input_path=str(in_p))
        result = await backend.ocr(req)
        assert result.ok
        assert result.text == ""
        assert result.blocks == []
        assert result.confidence == 0.0
        assert result.engine == "mock"


# ---- storage 测试 --------------------------------------------------------

class TestStorage:
    """image_processing_tasks 表 CRUD。"""

    @pytest.mark.asyncio
    async def test_insert_and_get_task(self, tmp_path, monkeypatch):
        from agent.image_processing.storage import (
            ImageProcessingStorage,
            reset_default_storage,
        )
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "img_proc.db"
        monkeypatch.setattr(settings, "image_processing_db_path", str(db_path))

        storage = ImageProcessingStorage()
        task_id = "abc123def456"
        await storage.insert_task(
            task_id=task_id,
            processing_type="enhance",
            backend="mock",
            input_path="/tmp/in.png",
            output_path="/tmp/out.png",
            input_size=100,
            output_size=100,
            elapsed_ms=10,
            ok=True,
            error=None,
            ocr_text=None,
            ocr_confidence=None,
            ocr_block_count=None,
            meta={"scale": 2},
        )
        task = await storage.get_task(task_id)
        assert task is not None
        assert task["task_id"] == task_id
        assert task["processing_type"] == "enhance"
        assert task["backend"] == "mock"
        assert task["ok"] == 1
        assert task["meta"]["scale"] == 2

    @pytest.mark.asyncio
    async def test_list_tasks_filter(self, tmp_path, monkeypatch):
        from agent.image_processing.storage import (
            ImageProcessingStorage,
            reset_default_storage,
        )
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "img_proc.db"
        monkeypatch.setattr(settings, "image_processing_db_path", str(db_path))

        storage = ImageProcessingStorage()
        for i, ok in enumerate([True, False, True]):
            await storage.insert_task(
                task_id=f"task_{i}",
                processing_type="ocr" if i == 0 else "enhance",
                backend="mock",
                input_path=f"/tmp/{i}.png",
                output_path=f"/tmp/{i}_out.png",
                input_size=100, output_size=100,
                elapsed_ms=10, ok=ok, error=None,
                ocr_text=None, ocr_confidence=None, ocr_block_count=None,
                meta={"i": i},
            )

        tasks = await storage.list_tasks(processing_type="ocr")
        assert len(tasks) == 1
        assert tasks[0]["processing_type"] == "ocr"

        tasks = await storage.list_tasks(ok=True)
        assert len(tasks) == 2

    @pytest.mark.asyncio
    async def test_get_stats(self, tmp_path, monkeypatch):
        from agent.image_processing.storage import (
            ImageProcessingStorage,
            reset_default_storage,
        )
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "img_proc.db"
        monkeypatch.setattr(settings, "image_processing_db_path", str(db_path))

        storage = ImageProcessingStorage()
        for i in range(3):
            await storage.insert_task(
                task_id=f"s_{i}",
                processing_type="enhance" if i < 2 else "ocr",
                backend="mock",
                input_path=f"/tmp/{i}.png", output_path=f"/tmp/{i}_o.png",
                input_size=10, output_size=10, elapsed_ms=1, ok=True,
                error=None, ocr_text=None, ocr_confidence=None, ocr_block_count=None,
                meta={},
            )
        stats = await storage.get_stats()
        assert stats["enhance"]["total"] == 2
        assert stats["enhance"]["ok"] == 2
        assert stats["ocr"]["total"] == 1


# ---- events 测试 ---------------------------------------------------------

class TestEvents:
    """image_processing events SSE emit 测试。"""

    @pytest.mark.asyncio
    async def test_emit_and_consume(self):
        from agent.image_processing.events import (
            EVT_IMG_PROCESSING_STARTED,
            consume_events,
            emit_event,
            flush_events,
        )
        await flush_events()
        await emit_event(EVT_IMG_PROCESSING_STARTED, {
            "kind": EVT_IMG_PROCESSING_STARTED,
            "task_id": "abc",
            "processing_type": "enhance",
        })
        events = await consume_events()
        assert len(events) == 1
        kind, payload = events[0]
        assert kind == EVT_IMG_PROCESSING_STARTED
        assert payload["task_id"] == "abc"

    @pytest.mark.asyncio
    async def test_flush(self):
        from agent.image_processing.events import consume_events, emit_event, flush_events
        await flush_events()
        await emit_event("x", {"a": 1})
        await emit_event("y", {"b": 2})
        dropped = await flush_events()
        assert dropped == 2
        events = await consume_events()
        assert events == []


# ---- API 端点测试 ----------------------------------------------------------

class TestAPI:
    """FastAPI 端点测试。"""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from agent.config import settings
        from agent.image_processing.storage import reset_default_storage
        from agent.image_processing.enhance import reset_enhance_backend
        from agent.image_processing.correct import reset_correct_backend
        from agent.image_processing.ocr import reset_ocr_backend

        reset_default_storage()
        reset_enhance_backend()
        reset_correct_backend()
        reset_ocr_backend()

        db_path = tmp_path / "img_proc.db"
        monkeypatch.setattr(settings, "image_processing_db_path", str(db_path))

        from fastapi.testclient import TestClient
        from agent.main import app
        return TestClient(app)

    def test_enhance_endpoint(self, client, tmp_path):
        in_p = tmp_path / "in.png"
        out_p = tmp_path / "out.png"
        in_p.write_bytes(b"data" * 50)

        resp = client.post("/image/enhance", json={
            "input_path": str(in_p),
            "output_path": str(out_p),
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["backend"] == "mock"
        assert out_p.exists()

    def test_correct_endpoint(self, client, tmp_path):
        in_p = tmp_path / "in.jpg"
        out_p = tmp_path / "out.jpg"
        in_p.write_bytes(b"data" * 50)

        resp = client.post("/image/correct", json={
            "input_path": str(in_p),
            "output_path": str(out_p),
            "correction_type": "deskew",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["correction_applied"] == "deskew"

    def test_ocr_endpoint(self, client, tmp_path):
        in_p = tmp_path / "in.png"
        in_p.write_bytes(b"data" * 50)

        resp = client.post("/image/ocr", json={
            "input_path": str(in_p),
            "languages": ["ch"],
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["text"] == ""  # V0 mock

    def test_list_tasks_empty(self, client):
        resp = client.get("/image/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_tasks_after_enhance(self, client, tmp_path):
        in_p = tmp_path / "in.png"
        out_p = tmp_path / "out.png"
        in_p.write_bytes(b"data" * 50)
        client.post("/image/enhance", json={
            "input_path": str(in_p),
            "output_path": str(out_p),
        })
        resp = client.get("/image/tasks")
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["processing_type"] == "enhance"
        assert tasks[0]["ok"] is True

    def test_get_task_not_found(self, client):
        resp = client.get("/image/tasks/nonexistent")
        assert resp.status_code == 404

    def test_stats_endpoint(self, client, tmp_path):
        in_p = tmp_path / "in.png"
        out_p = tmp_path / "out.png"
        in_p.write_bytes(b"data" * 50)
        client.post("/image/enhance", json={
            "input_path": str(in_p),
            "output_path": str(out_p),
        })
        resp = client.get("/image/stats")
        stats = resp.json()
        assert "enhance" in stats
        assert stats["enhance"]["total"] >= 1

    def test_enhance_unsupported_format_rejected(self, client, tmp_path):
        """V0 mock 后端：遇到 unsupported format 不抛异常，而是返 ok=False + error 字段。

        V1 真实后端（ONNX/OpenCV/PaddleOCR）会让异常从 backend.enhance() 抛出，被 API 层
        转成 HTTP 422；此处测的是 mock 路径。
        """
        in_p = tmp_path / "in.exe"
        in_p.write_bytes(b"data")
        out_p = tmp_path / "out.png"
        resp = client.post("/image/enhance", json={
            "input_path": str(in_p),
            "output_path": str(out_p),
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "unsupported image format" in body["error"]


# ---- SSE + _LOCAL_ONLY_TASKS 测试 ----------------------------------------

class TestStreamAndRouter:
    """SSE 三处同步 + _LOCAL_ONLY_TASKS 注入。"""

    def test_stream_channel_by_kind_has_image(self):
        from agent.graph.stream import _CHANNEL_BY_KIND
        assert _CHANNEL_BY_KIND["image_processing_started"] == "agent://image_processing_started"
        assert _CHANNEL_BY_KIND["image_processing_done"] == "agent://image_processing_done"
        assert _CHANNEL_BY_KIND["image_processing_error"] == "agent://image_processing_error"

    def test_local_only_tasks_has_image_processing(self):
        from agent.llm.router import _LOCAL_ONLY_TASKS
        assert "image_processing_summary" in _LOCAL_ONLY_TASKS