"""Phase 15 V1 · 真实 Vite 二进制端到端 smoke。

需要本机 Node.js ≥ 18 + npm registry 可达（首次运行会 npm install
vite 与框架插件，约 1-2 分钟）；环境不满足时整模块 skip（无网 CI 不红）。

覆盖（实现文档 §4.4）：
  - Vue 3 SFC：启动 → HTTP ready → 修改 <template> → hmr update 日志 + 转换模块含新内容
  - React JSX：修改组件 → Fast Refresh 转换模块更新
  - 纯 HTML：无 package.json 也能启动（全局 PATH vite）
  - 多会话并发：2 个会话不同端口互不干扰
  - 端口避让：base 端口被外部占用 → 自动选下一个
  - Node/Vite 不可用：明确 PreviewError（优雅降级）
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _node_major() -> int | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        out = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.match(r"v(\d+)", out.stdout.strip())
    return int(m.group(1)) if m else None


_NODE_MAJOR = _node_major()
pytestmark = pytest.mark.skipif(
    _NODE_MAJOR is None or _NODE_MAJOR < 18,
    reason="Phase 15 V1 e2e 需要本机 Node.js ≥ 18",
)

VUE_MARKER = "Hello EAIDE"
VUE_MARKER_HMR = "Hello HMR E2E"
REACT_MARKER = "Hello React"
REACT_MARKER_HMR = "Hello React E2E"


# ---- fixture：一次性安装真实依赖（session 级共享，避免重复 npm install）---


def _npm_install(root: Path) -> None:
    npm = shutil.which("npm")
    if not npm:
        pytest.skip("npm 不可用，跳过 Phase 15 V1 e2e")
    try:
        proc = subprocess.run(
            [npm, "install", "--no-audit", "--no-fund", "--loglevel=error"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"npm install 执行失败: {exc}")
    if proc.returncode != 0:
        pytest.skip(f"npm install 失败（无网络或 registry 不可达？）: {proc.stderr[:300]}")


@pytest.fixture(scope="session")
def vue_project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("vue-proj")
    (root / "src").mkdir()
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "e2e-vue",
                "private": True,
                "type": "module",
                "dependencies": {"vue": "^3.4.0"},
                "devDependencies": {
                    "vite": "^5.4.0",
                    "@vitejs/plugin-vue": "^5.0.0",
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "index.html").write_text(
        '<!doctype html><html><head><meta charset="UTF-8"/>'
        '<title>e2e-vue</title></head><body><div id="app"></div>'
        '<script type="module" src="/src/main.js"></script></body></html>',
        encoding="utf-8",
    )
    (root / "src" / "main.js").write_text(
        "import { createApp } from 'vue';\n"
        "import App from './App.vue';\n\n"
        "createApp(App).mount('#app');\n",
        encoding="utf-8",
    )
    (root / "src" / "App.vue").write_text(
        "<template>\n"
        f'  <h1 id="msg">{VUE_MARKER}</h1>\n'
        "</template>\n\n"
        "<script>\n"
        "export default { name: 'App' };\n"
        "</script>\n",
        encoding="utf-8",
    )
    _npm_install(root)
    return root


@pytest.fixture(scope="session")
def react_project(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("react-proj")
    (root / "src").mkdir()
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "e2e-react",
                "private": True,
                "type": "module",
                "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"},
                "devDependencies": {
                    "vite": "^5.4.0",
                    "@vitejs/plugin-react": "^4.3.1",
                },
            }
        ),
        encoding="utf-8",
    )
    (root / "index.html").write_text(
        '<!doctype html><html><head><meta charset="UTF-8"/>'
        '<title>e2e-react</title></head><body><div id="root"></div>'
        '<script type="module" src="/src/main.jsx"></script></body></html>',
        encoding="utf-8",
    )
    (root / "src" / "main.jsx").write_text(
        "import React from 'react';\n"
        "import { createRoot } from 'react-dom/client';\n"
        "import App from './App.jsx';\n\n"
        "createRoot(document.getElementById('root')).render(<App />);\n",
        encoding="utf-8",
    )
    (root / "src" / "App.jsx").write_text(
        f'export default function App() {{\n  return <h1 id="msg">{REACT_MARKER}</h1>;\n}}\n',
        encoding="utf-8",
    )
    _npm_install(root)
    return root


# ---- 测试辅助 --------------------------------------------------------------


@pytest.fixture
def allow_preview_path(monkeypatch):
    """把指定目录加入预览白名单（path_policy 安全校验）。"""

    def _allow(*paths: Path) -> None:
        from agent.config import settings

        monkeypatch.setattr(settings, "preview_allowed_paths", [str(p) for p in paths])

    return _allow


def _make_manager(tmp_path: Path, base_port: int):
    """构造真实子进程的 SessionManager（独立端口段 + 独立 preview.db）。"""
    from agent.preview.port_allocator import PortAllocator
    from agent.preview.session_manager import SessionManager
    from agent.preview.storage import PreviewStorage
    from agent.preview.vite_manager import VitePreviewManager

    return SessionManager(
        vite=VitePreviewManager(),
        allocator=PortAllocator(base_port, base_port + 7),
        storage=PreviewStorage(str(tmp_path / "preview.db")),
    )


async def _wait_http(url: str, timeout: float = 30.0) -> str:
    """轮询等待 Vite 服务就绪并返回响应体。"""
    import urllib.request

    def _get() -> str:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.read().decode("utf-8", errors="replace")

    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return await asyncio.to_thread(_get)
        except Exception as exc:
            last_exc = exc
            await asyncio.sleep(0.3)
    raise AssertionError(f"Vite 服务未在 {timeout}s 内就绪: {url} ({last_exc})")


async def _wait_log_contains(manager, session_id: str, keyword: str, timeout: float = 15.0) -> str:
    """轮询 Vite 子进程日志直到出现关键字（HMR / error）。"""
    deadline = time.monotonic() + timeout
    logs = ""
    while time.monotonic() < deadline:
        logs = "\n".join(manager._vite.recent_logs(session_id, 500))
        if keyword.lower() in logs.lower():
            return logs
        await asyncio.sleep(0.2)
    raise AssertionError(f"{timeout}s 内日志未出现 {keyword!r}，末尾：{logs[-500:]}")


# ---- Vue 3 SFC：启动 + HMR -------------------------------------------------


class TestVueSfcHmr:
    @pytest.mark.asyncio
    async def test_vue_start_modify_hmr(self, tmp_path, vue_project, allow_preview_path):
        from agent.preview.models import StartPreviewRequest

        allow_preview_path(vue_project)
        mgr = _make_manager(tmp_path, 5331)
        session = None
        app_vue = vue_project / "src" / "App.vue"
        original = app_vue.read_text(encoding="utf-8")
        try:
            session = await mgr.start(StartPreviewRequest(project_path=str(vue_project)))
            assert session.framework.value == "vue"
            assert session.status.value == "running"

            # HTTP 就绪：首页 + SFC 转换模块
            html = await _wait_http(session.url)
            assert "e2e-vue" in html
            before = await _wait_http(f"{session.url}/src/App.vue")
            assert VUE_MARKER in before

            # 修改 <template> → Vite watcher → hmr update
            app_vue.write_text(original.replace(VUE_MARKER, VUE_MARKER_HMR), encoding="utf-8")
            await _wait_log_contains(mgr, session.id, "hmr update", timeout=15.0)
            after = await _wait_http(f"{session.url}/src/App.vue")
            assert VUE_MARKER_HMR in after
        finally:
            app_vue.write_text(original, encoding="utf-8")
            if session is not None:
                await mgr.stop(session.id)
            assert mgr._allocator.used_slots() == 0

    @pytest.mark.asyncio
    async def test_react_fast_refresh(self, tmp_path, react_project, allow_preview_path):
        from agent.preview.models import StartPreviewRequest

        allow_preview_path(react_project)
        mgr = _make_manager(tmp_path, 5341)
        session = None
        app_jsx = react_project / "src" / "App.jsx"
        original = app_jsx.read_text(encoding="utf-8")
        try:
            session = await mgr.start(StartPreviewRequest(project_path=str(react_project)))
            assert session.framework.value == "react"

            html = await _wait_http(session.url)
            assert "e2e-react" in html
            before = await _wait_http(f"{session.url}/src/App.jsx")
            assert REACT_MARKER in before

            app_jsx.write_text(original.replace(REACT_MARKER, REACT_MARKER_HMR), encoding="utf-8")
            await _wait_log_contains(mgr, session.id, "hmr update", timeout=15.0)
            after = await _wait_http(f"{session.url}/src/App.jsx")
            assert REACT_MARKER_HMR in after
        finally:
            app_jsx.write_text(original, encoding="utf-8")
            if session is not None:
                await mgr.stop(session.id)
            assert mgr._allocator.used_slots() == 0


# ---- 纯 HTML（无 package.json）---------------------------------------------


def _use_vendor_vite(monkeypatch, vendor_project: Path) -> None:
    """把已装 vite 的项目 .bin 加进 PATH（纯 HTML 项目走全局 vite 解析）。"""
    vendor_bin = vendor_project / "node_modules" / ".bin"
    monkeypatch.setenv("PATH", f"{vendor_bin}{os.pathsep}{os.environ.get('PATH', '')}")


class TestHtmlStatic:
    @pytest.mark.asyncio
    async def test_html_no_package_json(
        self, tmp_path, vue_project, allow_preview_path, monkeypatch
    ):
        from agent.preview.models import StartPreviewRequest

        proj = tmp_path / "static-site"
        proj.mkdir()
        (proj / "index.html").write_text(
            "<html><body>HELLO-STATIC-E2E</body></html>", encoding="utf-8"
        )
        allow_preview_path(proj)
        _use_vendor_vite(monkeypatch, vue_project)

        mgr = _make_manager(tmp_path, 5351)
        session = await mgr.start(StartPreviewRequest(project_path=str(proj)))
        try:
            assert session.framework.value == "html"
            html = await _wait_http(session.url)
            assert "HELLO-STATIC-E2E" in html
        finally:
            await mgr.stop(session.id)


# ---- 多会话并发 -------------------------------------------------------------


class TestMultiSession:
    @pytest.mark.asyncio
    async def test_two_sessions_different_ports(
        self, tmp_path, vue_project, allow_preview_path, monkeypatch
    ):
        from agent.preview.models import StartPreviewRequest

        proj_a = tmp_path / "site-a"
        proj_b = tmp_path / "site-b"
        for p, marker in ((proj_a, "SITE-A-E2E"), (proj_b, "SITE-B-E2E")):
            p.mkdir()
            (p / "index.html").write_text(f"<html><body>{marker}</body></html>", encoding="utf-8")
        allow_preview_path(proj_a, proj_b)
        _use_vendor_vite(monkeypatch, vue_project)

        mgr = _make_manager(tmp_path, 5361)
        s_a = await mgr.start(StartPreviewRequest(project_path=str(proj_a)))
        s_b = await mgr.start(StartPreviewRequest(project_path=str(proj_b)))
        try:
            assert s_a.port != s_b.port
            assert "SITE-A-E2E" in await _wait_http(s_a.url)
            assert "SITE-B-E2E" in await _wait_http(s_b.url)
            assert len(mgr.list_active()) == 2
        finally:
            await mgr.stop(s_a.id)
            await mgr.stop(s_b.id)
            assert mgr._allocator.used_slots() == 0


# ---- 端口避让 + 优雅降级 ----------------------------------------------------


class TestPortAvoidanceAndDegradation:
    @pytest.mark.asyncio
    async def test_external_port_occupied_skipped(
        self, tmp_path, vue_project, allow_preview_path, monkeypatch
    ):
        from agent.preview.models import StartPreviewRequest

        proj = tmp_path / "avoid-site"
        proj.mkdir()
        (proj / "index.html").write_text("<html><body>AVOID-E2E</body></html>", encoding="utf-8")
        allow_preview_path(proj)
        _use_vendor_vite(monkeypatch, vue_project)

        # 外部进程占用 base 端口（5371）→ 会话应自动选 5372
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 5371))
        blocker.listen(1)
        mgr = _make_manager(tmp_path, 5371)
        session = None
        try:
            session = await mgr.start(StartPreviewRequest(project_path=str(proj)))
            # 被阻塞端口必须被跳过（其他端口可能被外部/残留进程占用，只断言避让行为）
            assert session.port != 5371
            assert "AVOID-E2E" in await _wait_http(session.url)
        finally:
            if session is not None:
                await mgr.stop(session.id)
            blocker.close()

    @pytest.mark.asyncio
    async def test_vite_unavailable_clear_error(self, tmp_path, monkeypatch, allow_preview_path):
        import agent.preview.vite_manager as vm
        from agent.preview.models import StartPreviewRequest
        from agent.preview.session_manager import PreviewError

        proj = tmp_path / "no-vite-proj"
        proj.mkdir()
        (proj / "index.html").write_text("<html></html>", encoding="utf-8")
        allow_preview_path(proj)
        # node / vite 都找不到 → ViteUnavailableError → PreviewError（优雅降级）
        monkeypatch.setattr(vm.shutil, "which", lambda _name: None)

        mgr = _make_manager(tmp_path, 5381)
        with pytest.raises(PreviewError, match="Vite"):
            await mgr.start(StartPreviewRequest(project_path=str(proj)))
        assert mgr._allocator.used_slots() == 0
        assert mgr.list_active() == []
