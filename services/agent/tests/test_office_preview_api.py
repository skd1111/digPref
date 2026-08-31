"""Office 预览端点测试（/office/preview，2026-08-25）。

OfficeCLI 缺失 / 非法路径用例无外部依赖必过；渲染成功用例
用 monkeypatch 假执行体模拟（写入 -o 目标文件），不依赖真实二进制。
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from agent.office_preview import api as preview_api

    preview_api._sessions.clear()
    app = FastAPI()
    app.include_router(preview_api.router)
    return TestClient(app)


@pytest.fixture
def office_file(tmp_path: Path) -> Path:
    p = tmp_path / "report.pptx"
    p.write_bytes(b"PK\x03\x04 placeholder")
    return p


def _fake_render(monkeypatch: pytest.MonkeyPatch, content: bytes) -> None:
    """假 run_officecli：把 content 写入命令行 -o 指定的输出文件。"""
    from agent.builtin.officecli_runtime import OfficeCliOutcome
    from agent.office_preview import api as preview_api

    def fake_run(args, *, as_json=True, timeout_sec=None):
        out_path = None
        for i, a in enumerate(args):
            if a == "-o" and i + 1 < len(args):
                out_path = args[i + 1]
        if out_path:
            Path(out_path).write_bytes(content)
        return OfficeCliOutcome(ok=True, data="", exit_code=0)

    monkeypatch.setattr(preview_api, "run_officecli", fake_run)
    monkeypatch.setattr(preview_api, "resolve_officecli_exe", lambda: "C:/fake.exe")


class TestValidation:
    def test_rejects_non_office_file(self, client, tmp_path: Path):
        txt = tmp_path / "a.txt"
        txt.write_text("hi", encoding="utf-8")
        r = client.post("/office/preview", json={"path": str(txt)})
        assert r.status_code == 400
        assert "not_an_office_file" in r.json()["detail"]

    def test_rejects_invalid_mode(self, client, office_file: Path, monkeypatch):
        monkeypatch.setattr("agent.office_preview.api.resolve_officecli_exe", lambda: "C:/fake.exe")
        r = client.post("/office/preview", json={"path": str(office_file), "mode": "pdf"})
        assert r.status_code == 400

    def test_not_installed_returns_503(self, client, office_file: Path, monkeypatch):
        monkeypatch.setattr("agent.office_preview.api.resolve_officecli_exe", lambda: None)
        r = client.post("/office/preview", json={"path": str(office_file)})
        assert r.status_code == 503
        assert "officecli_not_installed" in r.json()["detail"]


class TestHtmlPreview:
    def test_html_round_trip(self, client, office_file: Path, monkeypatch):
        _fake_render(monkeypatch, "<html><body><h1>Q4 报告</h1></body></html>".encode())
        r = client.post("/office/preview", json={"path": str(office_file), "mode": "html"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True and body["mode"] == "html"
        session_id = body["session_id"]
        assert body["html_url"] == f"/office/preview/html/{session_id}"

        # 渲染页可访问（资源内联，单文件）
        html = client.get(body["html_url"])
        assert html.status_code == 200
        assert "Q4 报告" in html.text

        # 会话列表可见
        sessions = client.get("/office/preview/sessions").json()["sessions"]
        assert any(s["session_id"] == session_id for s in sessions)

        # 停止后不可访问
        stop = client.post("/office/preview/stop", json={"session_id": session_id})
        assert stop.json()["stopped"] is True
        assert client.get(body["html_url"]).status_code == 404

    def test_render_failure_returns_422(self, client, office_file: Path, monkeypatch):
        from agent.builtin.officecli_runtime import OfficeCliOutcome
        from agent.office_preview import api as preview_api

        monkeypatch.setattr(preview_api, "resolve_officecli_exe", lambda: "C:/fake.exe")
        monkeypatch.setattr(
            preview_api,
            "run_officecli",
            lambda args, **kw: OfficeCliOutcome(
                ok=False, error="officecli_failed", message="corrupt file", exit_code=1
            ),
        )
        r = client.post("/office/preview", json={"path": str(office_file)})
        assert r.status_code == 422
        assert "corrupt file" in r.json()["detail"]


class TestScreenshotPreview:
    def test_screenshot_returns_base64(self, client, office_file: Path, monkeypatch):
        png = b"\x89PNG fake-bytes"
        _fake_render(monkeypatch, png)
        r = client.post(
            "/office/preview",
            json={"path": str(office_file), "mode": "screenshot", "page": 2},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["mode"] == "screenshot" and body["page"] == 2
        assert base64.b64decode(body["image_base64"]) == png


class TestSessionEviction:
    def test_evicts_oldest_when_full(self, client, office_file: Path, monkeypatch):
        _fake_render(monkeypatch, b"<html>x</html>")
        from agent.office_preview import api as preview_api

        first_id = None
        for i in range(preview_api._MAX_SESSIONS + 1):
            r = client.post("/office/preview", json={"path": str(office_file), "mode": "html"})
            assert r.status_code == 200
            if i == 0:
                first_id = r.json()["session_id"]
        # 最旧会话被淘汰
        sessions = client.get("/office/preview/sessions").json()["sessions"]
        assert all(s["session_id"] != first_id for s in sessions)
