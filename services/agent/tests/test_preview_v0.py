"""Phase 15 V0 · 单元测试 + 集成测试（40+ 用例）。

覆盖:
  - models: 枚举 / 请求 / 会话模型 / now_ms
  - framework_detector: Vue3 / Vue2 / React / Svelte / HTML / 多框架 / 无 package.json / 包管理器
  - port_allocator: 位图分配 / 释放 / 全占 / 重置 / 外部占用
  - config_generator: Vue / React / Svelte / HTML / hmr clientPort
  - vite_manager: mock 子进程启停 / HMR 解析 / build_error 解析 / 崩溃事件 / env 白名单
  - session_manager: start / stop / reload / 崩溃重启 / 端口释放
  - install_manager: node_modules 存在跳过 / spawn 失败 / 成功
  - api: start / stop / sessions / info / reload / install / SSE 订阅
  - SSE 三处同步: stream.py::_CHANNEL_BY_KIND 含 3 通道
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))


def _write_package_json(root: Path, deps: dict, dev_deps: dict | None = None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    data: dict = {"name": "demo"}
    if deps:
        data["dependencies"] = deps
    if dev_deps:
        data["devDependencies"] = dev_deps
    (root / "package.json").write_text(json.dumps(data), encoding="utf-8")


def _allow_project_path(monkeypatch, tmp_path: Path) -> None:
    """测试辅助：把 tmp_path 加入预览白名单（避免默认 home 规则拒绝）。"""
    from agent.config import settings

    monkeypatch.setattr(settings, "preview_allowed_paths", [str(tmp_path)])


# ---- models ---------------------------------------------------------------


class TestModels:
    def test_framework_enum_values(self):
        from agent.preview.models import Framework

        assert Framework.VUE.value == "vue"
        assert Framework.REACT.value == "react"
        assert Framework.SVELTE.value == "svelte"
        assert Framework.HTML.value == "html"

    def test_status_enum_values(self):
        from agent.preview.models import PreviewStatus

        assert {s.value for s in PreviewStatus} == {
            "starting",
            "running",
            "installing",
            "stopped",
            "errored",
        }

    def test_device_mode_values(self):
        from agent.preview.models import DeviceMode

        assert DeviceMode.MOBILE.value == "mobile"
        assert DeviceMode.TABLET.value == "tablet"
        assert DeviceMode.CUSTOM.value == "custom"

    def test_start_request_defaults(self):
        from agent.preview.models import StartPreviewRequest

        req = StartPreviewRequest(project_path="/tmp/demo")
        assert req.entry_file is None
        assert req.framework is None
        assert req.port is None

    def test_preview_session_defaults(self):
        from agent.preview.models import PreviewSession, PreviewStatus

        s = PreviewSession(
            id="abc",
            project_path="/p",
            entry_file="",
            framework="vue",
            port=5173,
            url="http://127.0.0.1:5173",
            status=PreviewStatus.RUNNING,
            created_at=1,
            last_active_at=1,
        )
        assert s.install_progress == 0
        assert s.pid is None
        assert s.config_path is None

    def test_now_ms_positive(self):
        from agent.preview.models import now_ms

        assert now_ms() > 1_600_000_000_000

    def test_hmr_event_model(self):
        from agent.preview.models import HmrStatusEvent

        e = HmrStatusEvent(session_id="s1", status="connected", timestamp=1)
        assert e.status == "connected"

    def test_build_error_model(self):
        from agent.preview.models import BuildErrorEvent

        e = BuildErrorEvent(session_id="s1", error="boom", file="a.vue", timestamp=1)
        assert e.file == "a.vue"


# ---- framework_detector ---------------------------------------------------


class TestFrameworkDetector:
    def test_vue3(self, tmp_path):
        from agent.preview.framework_detector import detect_framework
        from agent.preview.models import Framework

        _write_package_json(tmp_path, {"vue": "^3.4.0"}, {"@vitejs/plugin-vue": "^5"})
        assert detect_framework(tmp_path) == Framework.VUE

    def test_vue2_major(self, tmp_path):
        from agent.preview.framework_detector import detect_framework, vue_major_version
        from agent.preview.models import Framework

        _write_package_json(tmp_path, {"vue": "^2.7.16"})
        assert detect_framework(tmp_path) == Framework.VUE
        assert vue_major_version(tmp_path) == 2

    def test_react18(self, tmp_path):
        from agent.preview.framework_detector import detect_framework
        from agent.preview.models import Framework

        _write_package_json(tmp_path, {"react": "^18.3.0", "react-dom": "^18.3.0"})
        assert detect_framework(tmp_path) == Framework.REACT

    def test_react_in_dev_deps(self, tmp_path):
        from agent.preview.framework_detector import detect_framework
        from agent.preview.models import Framework

        _write_package_json(tmp_path, {}, {"@vitejs/plugin-react": "^4", "react": "^18"})
        assert detect_framework(tmp_path) == Framework.REACT

    def test_svelte5(self, tmp_path):
        from agent.preview.framework_detector import detect_framework
        from agent.preview.models import Framework

        _write_package_json(tmp_path, {"svelte": "^5.0.0"})
        assert detect_framework(tmp_path) == Framework.SVELTE

    def test_plain_html_no_package_json(self, tmp_path):
        from agent.preview.framework_detector import detect_framework
        from agent.preview.models import Framework

        assert detect_framework(tmp_path) == Framework.HTML

    def test_multi_framework_vue_first(self, tmp_path):
        from agent.preview.framework_detector import detect_framework
        from agent.preview.models import Framework

        _write_package_json(
            tmp_path,
            {"vue": "^3", "react": "^18", "svelte": "^5"},
        )
        assert detect_framework(tmp_path) == Framework.VUE

    def test_broken_package_json_falls_back_html(self, tmp_path):
        from agent.preview.framework_detector import detect_framework
        from agent.preview.models import Framework

        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "package.json").write_text("{broken", encoding="utf-8")
        assert detect_framework(tmp_path) == Framework.HTML

    def test_find_project_root_upwards(self, tmp_path):
        from agent.preview.framework_detector import find_project_root

        _write_package_json(tmp_path / "a" / "b" / "c", {"react": "^18"})
        root = find_project_root(tmp_path / "a" / "b" / "c" / "src" / "App.tsx")
        assert root == (tmp_path / "a" / "b" / "c").resolve()

    def test_find_project_root_missing(self, tmp_path):
        from agent.preview.framework_detector import find_project_root

        # 在隔离的临时目录内向上找，不会碰用户 home 的真实 package.json
        deep = tmp_path / "x" / "y" / "z"
        deep.mkdir(parents=True)
        assert find_project_root(deep, max_levels=2) is None

    def test_package_manager_detection(self, tmp_path):
        from agent.preview.framework_detector import get_package_manager

        _write_package_json(tmp_path, {})
        assert get_package_manager(tmp_path) == "npm"
        (tmp_path / "pnpm-lock.yaml").touch()
        assert get_package_manager(tmp_path) == "pnpm"
        (tmp_path / "pnpm-lock.yaml").unlink()
        (tmp_path / "yarn.lock").touch()
        assert get_package_manager(tmp_path) == "yarn"
        (tmp_path / "yarn.lock").unlink()
        (tmp_path / "bun.lockb").touch()
        assert get_package_manager(tmp_path) == "bun"

    def test_is_previewable_file(self):
        from agent.preview.framework_detector import is_previewable_file

        assert is_previewable_file("src/App.vue")
        assert is_previewable_file("x.tsx")
        assert is_previewable_file("index.html")
        assert not is_previewable_file("main.py")


# ---- port_allocator -------------------------------------------------------


class TestPortAllocator:
    def test_allocate_release(self):
        from agent.preview.port_allocator import PortAllocator

        a = PortAllocator(5173, 5176)
        p1 = a.allocate()
        p2 = a.allocate()
        assert p1 == 5173
        assert p2 == 5174
        assert a.used_slots() == 2
        assert a.release(p1) is True
        assert a.used_slots() == 1
        assert a.release(p1) is False  # 二次释放 False

    def test_preferred_port(self):
        from agent.preview.port_allocator import PortAllocator

        a = PortAllocator(5173, 5176)
        assert a.allocate(preferred=5175) == 5175
        # preferred 已被占 → 跳过，找下一个空闲
        assert a.allocate(preferred=5175) == 5173

    def test_out_of_range_release(self):
        from agent.preview.port_allocator import PortAllocator

        a = PortAllocator(5173, 5176)
        assert a.release(9999) is False
        assert a.release(5172) is False

    def test_all_occupied(self):
        from agent.preview.port_allocator import PortAllocator

        a = PortAllocator(5173, 5173)  # 单槽位
        assert a.allocate() == 5173
        assert a.allocate() is None  # 全占

    def test_reset(self):
        from agent.preview.port_allocator import PortAllocator

        a = PortAllocator(5173, 5176)
        a.allocate()
        a.allocate()
        a.reset()
        assert a.used_slots() == 0

    def test_allocated_ports(self):
        from agent.preview.port_allocator import PortAllocator

        a = PortAllocator(5173, 5176)
        a.allocate(preferred=5174)
        assert a.allocated_ports() == [5174]
        assert a.is_allocated(5174)


# ---- config_generator -----------------------------------------------------


class TestConfigGenerator:
    def test_vue3_config(self, tmp_path):
        from agent.preview.config_generator import generate
        from agent.preview.models import Framework

        path = generate(tmp_path, Framework.VUE, 5173)
        text = Path(path).read_text(encoding="utf-8")
        assert "@vitejs/plugin-vue" in text
        assert "port: 5173" in text
        assert "strictPort: true" in text

    def test_react_config(self, tmp_path):
        from agent.preview.config_generator import generate
        from agent.preview.models import Framework

        text = Path(generate(tmp_path, Framework.REACT, 5174)).read_text(encoding="utf-8")
        assert "@vitejs/plugin-react" in text

    def test_svelte_config(self, tmp_path):
        from agent.preview.config_generator import generate
        from agent.preview.models import Framework

        text = Path(generate(tmp_path, Framework.SVELTE, 5175)).read_text(encoding="utf-8")
        assert "@sveltejs/vite-plugin-svelte" in text

    def test_html_config_no_plugin(self, tmp_path):
        from agent.preview.config_generator import generate
        from agent.preview.models import Framework

        text = Path(generate(tmp_path, Framework.HTML, 5176)).read_text(encoding="utf-8")
        assert "plugins" in text
        assert "@vitejs/plugin-vue" not in text

    def test_hmr_client_port(self, tmp_path, monkeypatch):
        from agent.preview.config_generator import generate
        from agent.preview.models import Framework

        monkeypatch.setenv("EAIDE_AGENT_PORT", "8765")
        text = Path(generate(tmp_path, Framework.HTML, 5173)).read_text(encoding="utf-8")
        assert "clientPort: 8765" in text

    def test_vue2_plugin(self, tmp_path):
        from agent.preview.config_generator import generate
        from agent.preview.framework_detector import vue_major_version
        from agent.preview.models import Framework

        _write_package_json(tmp_path, {"vue": "^2.7.16"})
        assert vue_major_version(tmp_path) == 2
        text = Path(generate(tmp_path, Framework.VUE, 5173)).read_text(encoding="utf-8")
        assert "vite-plugin-vue2" in text


# ---- vite_manager（mock 子进程）------------------------------------------


class FakeProcess:
    """模拟 asyncio.subprocess.Process。"""

    def __init__(
        self, lines: list[str] | None = None, exit_code: int = 0, pid: int = 4242, **kwargs
    ):
        self.pid = pid
        self.returncode: int | None = None
        self._lines = lines or []
        self._exit_code = exit_code
        self._started = False
        self.cwd = "/tmp/demo"
        self._stdout = asyncio.StreamReader()
        self._stderr = asyncio.StreamReader()

    async def _feed(self) -> None:
        self._started = True
        for line in self._lines:
            self._stdout.feed_data(line.encode("utf-8") + b"\n")
            await asyncio.sleep(0)
        self._stdout.feed_eof()
        self._stderr.feed_eof()

    @property
    def stdout(self):
        return self._stdout

    @property
    def stderr(self):
        return self._stderr

    async def wait(self):
        if not self._started:
            await self._feed()
        self.returncode = self._exit_code
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = 9


def _fake_spawner(factory):
    """返回一个注入用 spawner（每次 start 调用都生成新 FakeProcess）。"""

    def spawn(args, env, cwd, **kwargs):
        proc = factory()
        if not hasattr(proc, "cwd"):
            proc.cwd = cwd
        return proc

    return spawn


class TestViteManager:
    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        from agent.preview.vite_manager import VitePreviewManager

        mgr = VitePreviewManager(spawner=_fake_spawner(FakeProcess))
        pid = await mgr.start(
            session_id="s1",
            project_path="/tmp/demo",
            config_path="/tmp/demo/.eaide-vite.config.mjs",
            port=5173,
        )
        assert pid == 4242
        assert mgr.is_running("s1") is True
        assert await mgr.stop("s1") is True
        assert await mgr.stop("s1") is False  # 幂等

    @pytest.mark.asyncio
    async def test_hmr_connected_parsed(self):
        from agent.preview import events as pe
        from agent.preview.vite_manager import VitePreviewManager

        await pe.flush_events()
        mgr = VitePreviewManager(
            spawner=_fake_spawner(
                lambda: FakeProcess(lines=["VITE v5.4 ready", "hmr update /src/App.vue"])
            )
        )
        await mgr.start(
            session_id="s1", project_path="/tmp/demo", config_path="/tmp/demo/cfg.mjs", port=5173
        )
        await asyncio.sleep(0.05)
        consumed = await pe.consume_events()
        kinds = {k for k, _ in consumed}
        assert pe.EVT_PREVIEW_HMR_CONNECTED in kinds
        await mgr.stop("s1")

    @pytest.mark.asyncio
    async def test_build_error_parsed(self):
        from agent.preview import events as pe
        from agent.preview.vite_manager import VitePreviewManager

        await pe.flush_events()
        mgr = VitePreviewManager(
            spawner=_fake_spawner(
                lambda: FakeProcess(
                    lines=["[vite] error while transforming /src/App.vue: SyntaxError"]
                )
            )
        )
        await mgr.start(
            session_id="s2", project_path="/tmp/demo", config_path="/tmp/demo/cfg.mjs", port=5174
        )
        await asyncio.sleep(0.05)
        consumed = await pe.consume_events()
        error_events = [p for k, p in consumed if k == pe.EVT_PREVIEW_BUILD_ERROR]
        assert error_events
        assert "App.vue" in error_events[0]["error"]
        await mgr.stop("s2")

    @pytest.mark.asyncio
    async def test_crash_emits_error_and_disconnected(self):
        from agent.preview import events as pe
        from agent.preview.vite_manager import VitePreviewManager

        await pe.flush_events()
        mgr = VitePreviewManager(spawner=_fake_spawner(lambda: FakeProcess(lines=[], exit_code=1)))
        await mgr.start(
            session_id="s3", project_path="/tmp/demo", config_path="/tmp/demo/cfg.mjs", port=5175
        )
        await asyncio.sleep(0.15)
        consumed = await pe.consume_events()
        kinds = {k for k, _ in consumed}
        assert pe.EVT_PREVIEW_BUILD_ERROR in kinds
        assert pe.EVT_PREVIEW_HMR_DISCONNECTED in kinds
        assert mgr.is_running("s3") is False

    def test_sanitized_env_no_secrets(self, monkeypatch):
        from agent.preview.vite_manager import _sanitized_env

        monkeypatch.setenv("EAIDE_PRIVATE_LLM_API_KEY", "super-secret")
        monkeypatch.setenv("EAIDE_PRIVATE_LLM_BASE_URL", "http://secret")
        monkeypatch.setenv("PATH", "C:\\bin")
        env = _sanitized_env()
        assert "EAIDE_PRIVATE_LLM_API_KEY" not in env
        assert "EAIDE_PRIVATE_LLM_BASE_URL" not in env
        assert "secret" not in json.dumps(env)
        assert env.get("PATH") == "C:\\bin"

    def test_resolve_vite_command_missing(self, tmp_path):
        from agent.preview.vite_manager import ViteUnavailableError, _resolve_vite_command

        with pytest.raises(ViteUnavailableError):
            _resolve_vite_command(tmp_path)

    def test_resolve_vite_command_local_bin(self, tmp_path, monkeypatch):
        import agent.preview.vite_manager as vm

        monkeypatch.setattr(
            vm.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None
        )
        (tmp_path / "node_modules" / "vite" / "bin").mkdir(parents=True)
        (tmp_path / "node_modules" / "vite" / "bin" / "vite.js").write_text("", encoding="utf-8")
        cmd = vm._resolve_vite_command(tmp_path)
        # 返回 argv 列表（node + vite.js 两个元素），不是复合字符串
        assert len(cmd) == 2
        assert cmd[-1].endswith("vite.js")

    def test_extract_file(self):
        from agent.preview.vite_manager import _extract_file

        assert "App.vue" in (_extract_file("[vite] error C:/p/App.vue:12:3 boom") or "")


# ---- session_manager ------------------------------------------------------


class TestPathPolicy:
    def test_validate_allowed(self, tmp_path, monkeypatch):
        from agent.preview.path_policy import validate_project_path

        _allow_project_path(monkeypatch, tmp_path)
        (tmp_path / "proj").mkdir()
        result = validate_project_path(tmp_path / "proj")
        assert result == (tmp_path / "proj").resolve()

    def test_validate_outside_rejected(self, tmp_path, monkeypatch):
        from agent.preview.path_policy import (
            PreviewPathNotAllowedError,
            validate_project_path,
        )

        _allow_project_path(monkeypatch, tmp_path / "allowed")
        (tmp_path / "allowed").mkdir()
        (tmp_path / "other").mkdir()
        with pytest.raises(PreviewPathNotAllowedError):
            validate_project_path(tmp_path / "other")

    def test_prefix_bypass_blocked(self, tmp_path, monkeypatch):
        """/allowed2 不应命中 /allowed 前缀白名单。"""
        from agent.preview.path_policy import (
            PreviewPathNotAllowedError,
            validate_project_path,
        )

        _allow_project_path(monkeypatch, tmp_path / "allowed")
        (tmp_path / "allowed").mkdir()
        (tmp_path / "allowed2").mkdir()
        with pytest.raises(PreviewPathNotAllowedError):
            validate_project_path(tmp_path / "allowed2")

    def test_configured_roots_override_defaults(self, tmp_path, monkeypatch):
        from agent.preview.path_policy import resolve_allowed_roots

        _allow_project_path(monkeypatch, tmp_path)
        roots = resolve_allowed_roots()
        assert roots == [(tmp_path).resolve()]

    def test_is_allowed_helper(self, tmp_path):
        from agent.preview.path_policy import is_allowed

        (tmp_path / "a").mkdir()
        # 白名单语义是「路径前缀」，不存在但落在根内也允许（存在性由 start 阶段校验）
        assert is_allowed(tmp_path / "a", roots=[tmp_path]) is True
        assert is_allowed(tmp_path / "future-proj", roots=[tmp_path]) is True
        assert is_allowed(tmp_path.parent / "outside", roots=[tmp_path]) is False


class TestSessionManager:
    @pytest.mark.asyncio
    async def test_start_session_flow(self, tmp_path, monkeypatch):
        from agent.config import settings
        from agent.preview.port_allocator import PortAllocator
        from agent.preview.session_manager import SessionManager
        from agent.preview.storage import PreviewStorage
        from agent.preview.vite_manager import VitePreviewManager

        _allow_project_path(monkeypatch, tmp_path)
        monkeypatch.setattr(settings, "preview_db_path", str(tmp_path / "preview.db"))
        _write_package_json(tmp_path, {"vue": "^3.4"})
        mgr = SessionManager(
            vite=VitePreviewManager(spawner=_fake_spawner(FakeProcess)),
            allocator=PortAllocator(5173, 5176),
            storage=PreviewStorage(str(tmp_path / "preview.db")),
        )
        session = await mgr.start(StartRequest(tmp_path))
        assert session.framework.value == "vue"
        assert session.port == 5173
        assert session.status.value == "running"
        assert session.url == "http://127.0.0.1:5173"
        assert mgr.get(session.id) is not None
        assert len(mgr.list_active()) == 1
        # 持久化落库
        stored = await PreviewStorage(str(tmp_path / "preview.db")).get_session(session.id)
        assert stored is not None
        assert stored["status"] == "running"
        await mgr.stop(session.id)
        assert mgr.list_active() == []

    @pytest.mark.asyncio
    async def test_vite_unavailable_rolls_back(self, tmp_path, monkeypatch):
        from agent.config import settings
        from agent.preview.port_allocator import PortAllocator
        from agent.preview.session_manager import PreviewError, SessionManager
        from agent.preview.storage import PreviewStorage
        from agent.preview.vite_manager import VitePreviewManager, ViteUnavailableError

        _allow_project_path(monkeypatch, tmp_path)
        monkeypatch.setattr(settings, "preview_db_path", str(tmp_path / "preview.db"))
        _write_package_json(tmp_path, {"react": "^18"})

        def broken_spawner(args, env, cwd, **kwargs):
            raise ViteUnavailableError("no node")

        allocator = PortAllocator(5173, 5176)
        mgr = SessionManager(
            vite=VitePreviewManager(spawner=broken_spawner),
            allocator=allocator,
            storage=PreviewStorage(str(tmp_path / "preview.db")),
        )
        with pytest.raises(PreviewError):
            await mgr.start(StartRequest(tmp_path))
        # 端口已回滚
        assert allocator.used_slots() == 0
        assert mgr.list_active() == []

    @pytest.mark.asyncio
    async def test_stop_nonexistent(self):
        from agent.preview.session_manager import PreviewError, SessionManager

        mgr = SessionManager()
        with pytest.raises(PreviewError):
            await mgr.stop("nope")

    @pytest.mark.asyncio
    async def test_manual_port(self, tmp_path, monkeypatch):
        from agent.config import settings
        from agent.preview.port_allocator import PortAllocator
        from agent.preview.session_manager import SessionManager
        from agent.preview.storage import PreviewStorage
        from agent.preview.vite_manager import VitePreviewManager

        _allow_project_path(monkeypatch, tmp_path)
        monkeypatch.setattr(settings, "preview_db_path", str(tmp_path / "preview.db"))
        _write_package_json(tmp_path, {})
        mgr = SessionManager(
            vite=VitePreviewManager(spawner=_fake_spawner(FakeProcess)),
            allocator=PortAllocator(5173, 5176),
            storage=PreviewStorage(str(tmp_path / "preview.db")),
        )
        session = await mgr.start(StartRequest(tmp_path, port=5175))
        assert session.port == 5175
        await mgr.stop(session.id)

    @pytest.mark.asyncio
    async def test_reload_stopped_session_fails(self, tmp_path, monkeypatch):
        from agent.config import settings
        from agent.preview.port_allocator import PortAllocator
        from agent.preview.session_manager import PreviewError, SessionManager
        from agent.preview.storage import PreviewStorage
        from agent.preview.vite_manager import VitePreviewManager

        _allow_project_path(monkeypatch, tmp_path)
        monkeypatch.setattr(settings, "preview_db_path", str(tmp_path / "preview.db"))
        _write_package_json(tmp_path, {})
        mgr = SessionManager(
            vite=VitePreviewManager(spawner=_fake_spawner(FakeProcess)),
            allocator=PortAllocator(5173, 5176),
            storage=PreviewStorage(str(tmp_path / "preview.db")),
        )
        session = await mgr.start(StartRequest(tmp_path))
        await mgr.stop(session.id)
        with pytest.raises(PreviewError):
            await mgr.reload(session.id)

    @pytest.mark.asyncio
    async def test_restart_crashed(self, tmp_path, monkeypatch):
        from agent.config import settings
        from agent.preview.port_allocator import PortAllocator
        from agent.preview.session_manager import SessionManager
        from agent.preview.storage import PreviewStorage
        from agent.preview.vite_manager import VitePreviewManager

        _allow_project_path(monkeypatch, tmp_path)
        monkeypatch.setattr(settings, "preview_db_path", str(tmp_path / "preview.db"))
        _write_package_json(tmp_path, {})
        procs = [FakeProcess(lines=[], exit_code=1), FakeProcess()]

        def factory():
            return procs.pop(0) if procs else FakeProcess()

        mgr = SessionManager(
            vite=VitePreviewManager(spawner=_fake_spawner(factory)),
            allocator=PortAllocator(5173, 5176),
            storage=PreviewStorage(str(tmp_path / "preview.db")),
        )
        session = await mgr.start(StartRequest(tmp_path))
        await asyncio.sleep(0.15)  # 让 watcher 检测到崩溃
        ok = await mgr.restart_crashed(session.id)
        assert ok is True
        assert mgr.get(session.id).status.value == "running"
        await mgr.stop(session.id)

    def test_inactive_timeout_config(self):
        from agent.preview.session_manager import INACTIVE_TIMEOUT_SEC, SessionManager

        mgr = SessionManager(inactive_timeout_sec=42)
        assert mgr._inactive_timeout_sec == 42
        assert INACTIVE_TIMEOUT_SEC == 1800

    @pytest.mark.asyncio
    async def test_path_outside_whitelist_rejected(self, tmp_path, monkeypatch):
        """白名单外的项目路径 → 拒绝启动（安全验收项 §7.3）。"""
        from agent.preview.port_allocator import PortAllocator
        from agent.preview.session_manager import PreviewError, SessionManager
        from agent.preview.storage import PreviewStorage
        from agent.preview.vite_manager import VitePreviewManager

        # 白名单只含一个无关目录；tmp_path 项目在名单外
        _allow_project_path(monkeypatch, tmp_path / "allowed")
        (tmp_path / "allowed").mkdir(exist_ok=True)
        _write_package_json(tmp_path, {"vue": "^3.4"})
        allocator = PortAllocator(5173, 5176)
        mgr = SessionManager(
            vite=VitePreviewManager(spawner=_fake_spawner(FakeProcess)),
            allocator=allocator,
            storage=PreviewStorage(str(tmp_path / "preview.db")),
        )
        with pytest.raises(PreviewError, match="白名单"):
            await mgr.start(StartRequest(tmp_path))
        # 端口未分配即回滚
        assert allocator.used_slots() == 0
        assert mgr.list_active() == []


class TestPreviewAllowedRoots:
    """BUGFIX #175：白名单拒绝后用户确认加入 → 持久化 + 运行时生效。"""

    @pytest.fixture(autouse=True)
    def _clean_runtime_roots(self):
        from agent.preview import path_policy

        path_policy.clear_runtime_roots()
        yield
        path_policy.clear_runtime_roots()

    @pytest.mark.asyncio
    async def test_allow_path_true_persists_root_and_starts(self, tmp_path, monkeypatch):
        from agent.config import settings
        from agent.preview import path_policy
        from agent.preview.port_allocator import PortAllocator
        from agent.preview.session_manager import SessionManager
        from agent.preview.storage import PreviewStorage
        from agent.preview.vite_manager import VitePreviewManager

        # 白名单只含无关目录 → tmp_path 在名单外；带 allow_path 重试应放行
        _allow_project_path(monkeypatch, tmp_path / "allowed")
        (tmp_path / "allowed").mkdir(exist_ok=True)
        monkeypatch.setattr(settings, "preview_db_path", str(tmp_path / "preview.db"))
        _write_package_json(tmp_path, {"vue": "^3.4"})
        storage = PreviewStorage(str(tmp_path / "preview.db"))
        mgr = SessionManager(
            vite=VitePreviewManager(spawner=_fake_spawner(FakeProcess)),
            allocator=PortAllocator(5173, 5176),
            storage=storage,
        )
        session = await mgr.start(StartRequest(tmp_path, allow_path=True))
        assert session.status.value == "running"
        # 持久化落库 + 运行时白名单生效（同会话内再启动不再需要 allow_path）
        roots = await storage.list_allowed_roots()
        assert [Path(r).resolve() for r in roots] == [tmp_path.resolve()]
        session2 = await mgr.start(StartRequest(tmp_path))
        assert session2.status.value == "running"
        assert path_policy.resolve_allowed_roots()  # 运行时根已合入
        await mgr.stop(session.id)
        await mgr.stop(session2.id)

    @pytest.mark.asyncio
    async def test_persisted_root_reloaded_by_new_manager(self, tmp_path, monkeypatch):
        """重启后回载：新 SessionManager 读持久化白名单，无需再次确认。"""
        from agent.config import settings
        from agent.preview.port_allocator import PortAllocator
        from agent.preview.session_manager import SessionManager
        from agent.preview.storage import PreviewStorage
        from agent.preview.vite_manager import VitePreviewManager

        _allow_project_path(monkeypatch, tmp_path / "allowed")
        (tmp_path / "allowed").mkdir(exist_ok=True)
        monkeypatch.setattr(settings, "preview_db_path", str(tmp_path / "preview.db"))
        _write_package_json(tmp_path, {"vue": "^3.4"})
        db = str(tmp_path / "preview.db")

        # 第一代：确认加入
        mgr1 = SessionManager(
            vite=VitePreviewManager(spawner=_fake_spawner(FakeProcess)),
            allocator=PortAllocator(5173, 5176),
            storage=PreviewStorage(db),
        )
        s1 = await mgr1.start(StartRequest(tmp_path, allow_path=True))
        await mgr1.stop(s1.id)

        # 模拟重启：清空运行时状态 + 新实例，不带 allow_path 也应放行
        from agent.preview import path_policy

        path_policy.clear_runtime_roots()
        mgr2 = SessionManager(
            vite=VitePreviewManager(spawner=_fake_spawner(FakeProcess)),
            allocator=PortAllocator(5173, 5176),
            storage=PreviewStorage(db),
        )
        s2 = await mgr2.start(StartRequest(tmp_path))
        assert s2.status.value == "running"
        await mgr2.stop(s2.id)

    def test_runtime_roots_merge_into_resolve(self, tmp_path, monkeypatch):
        from agent.config import settings
        from agent.preview import path_policy

        # 未配置白名单 → 运行时根目录并入默认规则
        monkeypatch.setattr(settings, "preview_allowed_paths", [])
        path_policy.add_runtime_root(tmp_path)
        roots = path_policy.resolve_allowed_roots()
        assert tmp_path.resolve() in roots


class TestHtmlStaticPreview:
    """BUGFIX #176：HTML 框架进程内静态服务 —— 零 Node 依赖。"""

    @pytest.mark.asyncio
    async def test_html_project_previews_without_node(self, tmp_path, monkeypatch):
        """无 package.json / node_modules / vite 的纯静态工程也能预览。"""
        import urllib.request

        from agent.config import settings
        from agent.preview.models import StartPreviewRequest
        from agent.preview.port_allocator import PortAllocator
        from agent.preview.session_manager import SessionManager
        from agent.preview.storage import PreviewStorage
        from agent.preview.vite_manager import VitePreviewManager

        _allow_project_path(monkeypatch, tmp_path)
        monkeypatch.setattr(settings, "preview_db_path", str(tmp_path / "preview.db"))
        (tmp_path / "centralBusMonitor.html").write_text(
            "<html><body>OK176</body></html>", encoding="utf-8"
        )
        allocator = PortAllocator(5391, 5392)
        mgr = SessionManager(
            # 真实 manager（不注入 spawner）—— 走静态服务真链路而非 Vite 子进程
            vite=VitePreviewManager(),
            allocator=allocator,
            storage=PreviewStorage(str(tmp_path / "preview.db")),
        )
        req = StartPreviewRequest(project_path=str(tmp_path), entry_file="centralBusMonitor.html")
        session = await mgr.start(req)
        try:
            assert session.framework.value == "html"
            # 入口文件直达（不是目录首页）
            assert session.url == "http://127.0.0.1:5391/centralBusMonitor.html"
            assert mgr._vite.is_running(session.id) is True
            # HTTP 真的能拉到内容（进程内静态服务）
            body = urllib.request.urlopen(session.url, timeout=3).read()
            assert b"OK176" in body
        finally:
            await mgr.stop(session.id)
        # 停止后端口释放，服务不可达；同端口可被再次分配（无泄漏）
        with pytest.raises(OSError):
            urllib.request.urlopen("http://127.0.0.1:5391/", timeout=2)
        assert allocator.used_slots() == 0

    def test_entry_relative_mapping(self, tmp_path):
        """入口文件绝对/相对/越界路径的 URL 拼接归一。"""
        from agent.preview.session_manager import _entry_relative_to

        assert _entry_relative_to(tmp_path, str(tmp_path / "sub" / "a.html")) == "sub/a.html"
        assert _entry_relative_to(tmp_path, "b.html") == "b.html"
        # 越界文件 → None（回退目录首页）
        assert _entry_relative_to(tmp_path, str(tmp_path.parent / "x.html")) is None


def StartRequest(project_path, port=None, allow_path=False):
    from agent.preview.models import StartPreviewRequest

    return StartPreviewRequest(project_path=str(project_path), port=port, allow_path=allow_path)


# ---- install_manager ------------------------------------------------------


class TestInstallManager:
    @pytest.mark.asyncio
    async def test_skip_when_node_modules_exists(self, tmp_path):
        from agent.preview.install_manager import ensure_dependencies

        (tmp_path / "node_modules").mkdir(parents=True)
        ok = await ensure_dependencies(tmp_path, "s1")
        assert ok is True

    @pytest.mark.asyncio
    async def test_no_package_manager(self, tmp_path, monkeypatch):
        import shutil

        from agent.preview.install_manager import ensure_dependencies

        monkeypatch.setattr(shutil, "which", lambda name: None)
        ok = await ensure_dependencies(tmp_path, "s1")
        assert ok is False

    @pytest.mark.asyncio
    async def test_spawn_failure(self, tmp_path, monkeypatch):
        import shutil

        from agent.preview.install_manager import ensure_dependencies

        monkeypatch.setattr(shutil, "which", lambda name: "npm")

        class FailProc:
            stdout = None
            stderr = None

            async def wait(self):
                return 1

        ok = await ensure_dependencies(tmp_path, "s1", spawner=lambda *a, **k: FailProc())
        assert ok is False


# ---- API ------------------------------------------------------------------


class TestPreviewAPI:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from agent.config import settings
        from agent.preview.port_allocator import reset_default_allocator
        from agent.preview.session_manager import reset_default_manager
        from agent.preview.storage import reset_default_storage
        from agent.preview.vite_manager import VitePreviewManager, reset_default_vite_manager

        _allow_project_path(monkeypatch, tmp_path)
        monkeypatch.setattr(settings, "preview_db_path", str(tmp_path / "preview.db"))
        reset_default_storage()
        reset_default_manager()
        reset_default_vite_manager()
        reset_default_allocator()
        from agent.preview.port_allocator import PortAllocator
        from agent.preview.session_manager import SessionManager
        from agent.preview.storage import PreviewStorage

        SessionManager(
            vite=VitePreviewManager(spawner=_fake_spawner(FakeProcess)),
            allocator=PortAllocator(5173, 5176),
            storage=PreviewStorage(str(tmp_path / "preview.db")),
        )
        _write_package_json(tmp_path, {"vue": "^3.4"})

        from agent.main import app
        from fastapi.testclient import TestClient

        return TestClient(app), tmp_path

    def test_start_and_info(self, client):
        c, tmp_path = client
        resp = c.post("/preview/start", json={"project_path": str(tmp_path)})
        assert resp.status_code == 200
        body = resp.json()
        assert body["framework"] == "vue"
        assert body["status"] == "running"
        assert body["port"] == 5173
        sid = body["id"]
        info = c.get(f"/preview/info/{sid}")
        assert info.status_code == 200
        assert info.json()["id"] == sid
        c.post(f"/preview/stop/{sid}")

    def test_start_invalid_path(self, client):
        c, _ = client
        # Windows 用不存在的盘符；Linux 用根下不存在的绝对路径
        # （两平台上溯都找不到 package.json → 400）
        invalid = "Z:/definitely/missing" if os.name == "nt" else "/definitely/missing"
        resp = c.post("/preview/start", json={"project_path": invalid})
        assert resp.status_code == 400

    def test_list_sessions(self, client):
        c, tmp_path = client
        c.post("/preview/start", json={"project_path": str(tmp_path)})
        resp = c.get("/preview/sessions")
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_info_not_found(self, client):
        c, _ = client
        assert c.get("/preview/info/nope").status_code == 404

    def test_stop_not_found(self, client):
        c, _ = client
        assert c.post("/preview/stop/nope").status_code == 404

    def test_reload_running(self, client):
        c, tmp_path = client
        sid = c.post("/preview/start", json={"project_path": str(tmp_path)}).json()["id"]
        resp = c.post(f"/preview/reload/{sid}")
        assert resp.status_code == 200
        assert resp.json()["id"] == sid

    def test_install_trigger(self, client):
        c, tmp_path = client
        sid = c.post("/preview/start", json={"project_path": str(tmp_path)}).json()["id"]
        resp = c.post(f"/preview/install/{sid}")
        assert resp.status_code == 200
        assert resp.json()["install_started"] is True

    def test_install_not_found(self, client):
        c, _ = client
        assert c.post("/preview/install/nope").status_code == 404


# ---- SSE 三处同步 + 事件机制 ----------------------------------------------


class TestPreviewEvents:
    def test_stream_channel_registry(self):
        from agent.graph.stream import _CHANNEL_BY_KIND

        assert "preview_hmr_connected" in _CHANNEL_BY_KIND
        assert "preview_hmr_disconnected" in _CHANNEL_BY_KIND
        assert "preview_build_error" in _CHANNEL_BY_KIND
        assert _CHANNEL_BY_KIND["preview_hmr_connected"] == "agent://preview_hmr_connected"
        assert _CHANNEL_BY_KIND["preview_build_error"] == "agent://preview_build_error"

    @pytest.mark.asyncio
    async def test_emit_consume_flush(self):
        from agent.preview import events as pe

        await pe.flush_events()
        await pe.emit_event(pe.EVT_PREVIEW_HMR_CONNECTED, {"session_id": "s1"})
        await pe.emit_event(pe.EVT_PREVIEW_BUILD_ERROR, {"session_id": "s1", "error": "x"})
        consumed = await pe.consume_events()
        assert len(consumed) == 2
        assert consumed[0][0] == pe.EVT_PREVIEW_HMR_CONNECTED
        assert await pe.flush_events() == 0

    @pytest.mark.asyncio
    async def test_subscribe_broadcast(self):
        from agent.preview import events as pe

        q = await pe.subscribe("sess-a")
        await pe.emit_event(
            pe.EVT_PREVIEW_HMR_CONNECTED, {"session_id": "sess-a", "status": "connected"}
        )
        envelope = await asyncio.wait_for(q.get(), timeout=1.0)
        assert envelope["event"] == pe.EVT_PREVIEW_HMR_CONNECTED
        await pe.unsubscribe("sess-a", q)

    def test_events_module_has_install_progress(self):
        from agent.preview.events import EVT_PREVIEW_INSTALL_PROGRESS

        assert EVT_PREVIEW_INSTALL_PROGRESS == "preview_install_progress"


class TestPreviewApiRouterRegistered:
    def test_main_app_has_preview_routes(self):
        from agent.main import app

        # 路由以 _IncludedRouter 挂载（FastAPI 新版结构），用行为断言
        from fastapi.testclient import TestClient

        with TestClient(app) as c:
            # 无效路径 → 400（路由存在且进入 handler）
            assert c.post("/preview/start", json={"project_path": "Z:/missing"}).status_code == 400
            # 不存在的会话流 → 404（SSE 路由已挂载）
            assert c.get("/preview/stream/nope").status_code == 404
            assert c.get("/preview/info/nope").status_code == 404
