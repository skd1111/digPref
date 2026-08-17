"""chat 附加文件端点测试（POST /chat/attach-file，2026-08-14）。

文本/代码类直读（UTF-8 / GBK 回退）不依赖 markitdown，CI 必过；
docx/pdf 转换类用例在 markitdown 不可用时自动 skip。
"""

from __future__ import annotations

import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("EAIDE_DATA_ROOT", str(tmp_path))
    from agent.api import chat as chat_api

    app = FastAPI()
    app.include_router(chat_api.router)
    return TestClient(app)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class TestTextFiles:
    def test_text_file_utf8(self, client):
        r = client.post(
            "/chat/attach-file",
            json={"file_name": "a.py", "content_base64": _b64(b"def foo():\n    return 1")},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["mode"] == "text"
        assert "def foo" in body["content"]
        assert body["truncated"] is False

    def test_text_file_gbk_fallback(self, client):
        raw = "中文注释 GBK 编码".encode("gbk")
        r = client.post(
            "/chat/attach-file",
            json={"file_name": "b.txt", "content_base64": _b64(raw)},
        )
        body = r.json()
        assert body["ok"] is True
        assert "中文注释" in body["content"]

    def test_content_truncated_at_cap(self, client, monkeypatch):
        import agent.api.chat as chat_api

        monkeypatch.setattr(chat_api, "_ATTACH_MAX_CHARS", 100)
        r = client.post(
            "/chat/attach-file",
            json={"file_name": "big.md", "content_base64": _b64(("x" * 500).encode())},
        )
        body = r.json()
        assert body["ok"] is True
        assert len(body["content"]) == 100
        assert body["chars"] == 500
        assert body["truncated"] is True


class TestErrors:
    def test_bad_base64(self, client):
        r = client.post(
            "/chat/attach-file",
            json={"file_name": "a.txt", "content_base64": "!!!not-base64!!!"},
        )
        assert r.status_code == 400

    def test_too_large(self, client, monkeypatch):
        import agent.api.chat as chat_api

        monkeypatch.setattr(chat_api, "_ATTACH_MAX_BYTES", 8)
        r = client.post(
            "/chat/attach-file",
            json={"file_name": "a.txt", "content_base64": _b64(b"0123456789")},
        )
        assert r.status_code == 400
        assert "过大" in r.text

    def test_path_traversal_name_neutralized(self, client, tmp_path):
        r = client.post(
            "/chat/attach-file",
            json={"file_name": "../../evil.txt", "content_base64": _b64(b"hi")},
        )
        assert r.status_code == 200
        # 临时文件转换后即删，且不会写到 data_root 之外
        assert not (tmp_path.parent / "evil.txt").exists()


class TestMarkdownConversion:
    """docx/pdf 等二进制格式走 file_to_markdown；markitdown 不可用时 skip。"""

    @pytest.fixture(autouse=True)
    def _skip_without_markitdown(self):
        pytest.importorskip("markitdown")

    def test_html_converts_to_markdown(self, client):
        html = "<html><body><h1>标题</h1><p>正文内容</p></body></html>"
        r = client.post(
            "/chat/attach-file",
            json={"file_name": "page.html", "content_base64": _b64(html.encode("utf-8"))},
        )
        body = r.json()
        assert body["ok"] is True, body
        assert body["mode"] == "markdown"
        assert "正文内容" in body["content"]

    def test_corrupt_pdf_does_not_crash(self, client):
        r = client.post(
            "/chat/attach-file",
            json={"file_name": "broken.pdf", "content_base64": _b64(b"not a real pdf")},
        )
        # 红线：不抛 500；要么返错误（ok=False），要么宽容返空内容，都由前端提示
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body["content"], str)
        assert body["mode"] == "markdown"
