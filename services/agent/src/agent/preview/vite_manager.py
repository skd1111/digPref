"""Phase 15 V0 · Vite 子进程管理（subprocess.Popen 封装）。

职责：
  - 启动 / 停止 / 重启 Vite 子进程（sanitized env，不继承敏感变量）
  - 异步读取 stdout/stderr，解析 `[vite] hmr update` / `[vite] error` 关键字
  - psutil 内存监控（超 512MB 自动 kill）+ 异常退出自动重启（1 次）

进程环境白名单（CLAUDE.md §5 红线）：仅透传 PATH / SystemRoot / TEMP /
HOME|USERPROFILE / NODE_ENV / EAIDE_AGENT_PORT —— 绝不继承
EAIDE_PRIVATE_LLM_API_KEY 等敏感变量。
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import functools
import http.server
import logging
import os
import shutil
import socket
import socketserver
import sys
import threading
import weakref
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.preview import events as preview_events
from agent.preview.models import Framework, now_ms

log = logging.getLogger("agent.preview.vite_manager")

MAX_VITE_MEMORY_BYTES = 512 * 1024 * 1024  # 512MB（设计 §5.2）
MEMORY_SAMPLE_SEC = 5.0
CRASH_RESTART_DELAY_SEC = 1.0


@dataclass
class _ProcessHandle:
    session_id: str
    proc: asyncio.subprocess.Process
    port: int
    restarts: int = 0
    stopped: bool = False
    read_task: asyncio.Task[None] | None = None
    watch_task: asyncio.Task[None] | None = None
    mem_task: asyncio.Task[None] | None = None
    logs: list[str] = field(default_factory=list)


class ViteUnavailableError(RuntimeError):
    """Node.js / Vite 二进制不可用。"""


# ---- BUGFIX #176：HTML 框架进程内静态服务（零 Node 依赖）-------------------
# 老系统纯静态工程（无 package.json / node_modules）预览不需要也不应该依赖
# Vite —— Python 标准库 ThreadingHTTPServer 直接伺服目录即可。
# shim 仿 asyncio.subprocess.Process 最小接口（pid / returncode / stdout /
# stderr / terminate / kill / wait），让既有 _ProcessHandle 生命周期逻辑复用。


class _QuietHttpHandler(http.server.SimpleHTTPRequestHandler):
    """请求日志静默（避免每次刷新都刷进 agent.log）。"""

    def log_message(self, fmt: str, *args: Any) -> None:
        return


class _ThreadingHttpServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class StaticServerProcess:
    """进程内静态文件服务的 Process 仿制（接口对齐 asyncio.subprocess.Process）。"""

    def __init__(self, server: _ThreadingHttpServer, thread: threading.Thread) -> None:
        self._server = server
        self._thread = thread
        self.pid: int | None = os.getpid()
        self.stdout: asyncio.StreamReader | None = None
        self.stderr: asyncio.StreamReader | None = None
        self._shut = threading.Event()

    @property
    def returncode(self) -> int | None:
        if self._thread.is_alive():
            return None
        return 0

    def terminate(self) -> None:
        """确定性关停（#176）：置 __shutdown_request 后唤醒 select 阻塞。

        socketserver.shutdown() 会死等 serve 线程退出，而 serve 线程阻塞在
        select(poll_interval=0.5s) 内 —— 这里不等它，直接置标志位后用哨兵连接把
        select 立刻唤醒；serve 循环下一圈自检标志位即退出（官方推荐模式）。
        线程退出由 _shut 事件对外可观测，避免「以为停了其实没停」。
        """
        if self._shut.is_set():
            return
        self._shut.set()
        # 等价于 self._server._BaseServer__shutdown_request = True（私有名改写，
        # setattr 绕开类型检查；socketserver 无公开的非阻塞 shutdown API）
        setattr(self._server, "_BaseServer__shutdown_request", True)  # noqa: B010
        port = self._server.server_address[1]
        with suppress(OSError):
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                pass

    def kill(self) -> None:
        self.terminate()

    async def wait(self) -> int:
        """阻塞到服务线程真正退出 —— 带超时，超时后不再拖累事件循环拆卸。

        超时场景（理论上不该发生，terminate 是确定性的）：线程已失联，
        返回退出码并放手，避免 watcher/stop 被一个僵尸线程永久卡死。
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_STATIC_JOIN_EXECUTOR, self._thread.join, 5.0)
        if not self._thread.is_alive():
            # 线程已退出 → 回收监听套接字（server_close 幂等）
            with suppress(OSError):
                self._server.server_close()
        return 0


def _start_static_server(directory: str, port: int) -> StaticServerProcess:
    """启动进程内静态文件服务（绑定 127.0.0.1，仅本机）。"""
    handler = functools.partial(_QuietHttpHandler, directory=directory)
    server = _ThreadingHttpServer(("127.0.0.1", port), handler)
    thread = threading.Thread(
        target=server.serve_forever,
        name=f"eaide-preview-static-{port}",
        daemon=True,
    )
    thread.start()
    return StaticServerProcess(server, thread)


# 静态服务 join 专用线程池：事件循环关闭时不阻塞拆卸（与默认 executor 隔离）。
_STATIC_JOIN_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="eaide-preview-join",
)

# 存活静态服务弱引用登记（#176 泄漏兑底）：事件循环被直接关闭时（如测试套件，
# watcher/stop 被取消）serve 线程会变孤儿，阻塞解释器退出 —— 退出时统一收割。
# 生产链路正常由 SessionManager.shutdown() → stop_all() 收尾，此处仅为安全网。
_LIVE_STATIC_SERVERS: weakref.WeakSet[StaticServerProcess] = weakref.WeakSet()


def _reap_static_servers() -> None:
    for proc in list(_LIVE_STATIC_SERVERS):
        proc.terminate()
    for proc in list(_LIVE_STATIC_SERVERS):
        # terminate 置位 + 哨兵唤醒后，serve 线程应在一个 poll 周期内退出；
        # 这里补一次 server_close 释放监听端口（幂等，重复调用安全）。
        proc._thread.join(timeout=2.0)
        with suppress(OSError):
            proc._server.server_close()
    # 关停退出钩子专用池（内部按存活线程数投哨兵），避免卡在 join 上的
    # worker 拖累解释器退出（_python_exit 会 join 所有池线程）。
    _STATIC_JOIN_EXECUTOR.shutdown(wait=False)


atexit.register(_reap_static_servers)


class VitePreviewManager:
    """Vite 子进程生命周期管理。"""

    def __init__(
        self,
        *,
        spawner: Callable[..., Any] | None = None,
        max_memory_bytes: int = MAX_VITE_MEMORY_BYTES,
    ) -> None:
        # spawner 可注入（测试用 mock 进程）；None 时用 create_subprocess_exec
        self._spawner = spawner
        self._max_memory_bytes = max_memory_bytes
        self._procs: dict[str, _ProcessHandle] = {}

    # ---- 查询 ------------------------------------------------------------

    def pid_of(self, session_id: str) -> int | None:
        handle = self._procs.get(session_id)
        return handle.proc.pid if handle and handle.proc.pid else None

    def is_running(self, session_id: str) -> bool:
        handle = self._procs.get(session_id)
        if handle is None or handle.proc.returncode is not None:
            return False
        return not handle.stopped

    def recent_logs(self, session_id: str, limit: int = 50) -> list[str]:
        handle = self._procs.get(session_id)
        return handle.logs[-limit:] if handle else []

    # ---- 生命周期 ---------------------------------------------------------

    async def start(
        self,
        *,
        session_id: str,
        project_path: str,
        config_path: str | None,
        port: int,
        framework: Framework | str | None = None,
    ) -> int:
        """启动预览子进程，返回 PID。

        HTML 框架（BUGFIX #176）用进程内静态服务，零 Node 依赖；
        其余框架启动 Vite 子进程。失败抛 ViteUnavailableError / OSError。
        进程启动后立即返回；stderr/stdout 由后台任务解析（HMR 状态 / 编译错误）。
        """
        fw = framework.value if isinstance(framework, Framework) else framework

        if fw == Framework.HTML.value:
            # 纯静态工程：无 package.json / node_modules 也能预览，不走 Vite。
            # 端口占用 → OSError 上抛，由 SessionManager 回滚并给出明确提示。
            proc: Any = _start_static_server(project_path, port)
            _LIVE_STATIC_SERVERS.add(proc)
            handle = _ProcessHandle(session_id=session_id, proc=proc, port=port)
            self._procs[session_id] = handle
            handle.watch_task = asyncio.create_task(self._watcher(handle))
            log.info("[preview] static server started: %s -> 127.0.0.1:%s", project_path, port)
            # 静态服务无 HMR；直接就绪，前端状态徽章不卡在 connecting
            preview_events.emit_event_sync(
                preview_events.EVT_PREVIEW_HMR_CONNECTED,
                {
                    "session_id": session_id,
                    "status": "connected",
                    "timestamp": now_ms(),
                },
            )
            return proc.pid or 0

        env = _sanitized_env()

        if self._spawner is not None:
            proc = self._spawner(
                args=[project_path, config_path, str(port)],
                env=env,
                cwd=project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        else:
            cmd = _resolve_vite_command(project_path)
            # 非 HTML 分支由 SessionManager 保证 config_path 非空；防御性校验避免
            # None 混入 argv（create_subprocess_exec 会拿到 "None" 字符串）
            if config_path is None:
                raise ViteUnavailableError("非 HTML 框架预览缺少 Vite 配置文件路径")
            args = [*cmd, "--config", config_path, "--port", str(port), "--strictPort"]
            proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=project_path,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
            )

        handle = _ProcessHandle(session_id=session_id, proc=proc, port=port)
        self._procs[session_id] = handle
        handle.read_task = asyncio.create_task(self._reader(handle))
        handle.watch_task = asyncio.create_task(self._watcher(handle))
        handle.mem_task = asyncio.create_task(self._memory_monitor(handle))
        return proc.pid or 0

    async def stop(
        self,
        session_id: str,
        *,
        release_port: Callable[[int], None] | None = None,
    ) -> bool:
        """停止 Vite 子进程（幂等）。返回是否确实在运行。"""
        handle = self._procs.pop(session_id, None)
        if handle is None:
            return False
        handle.stopped = True
        for task in (handle.read_task, handle.watch_task, handle.mem_task):
            if task is not None:
                task.cancel()
        proc = handle.proc
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    proc.kill()
                    await proc.wait()
                except (ProcessLookupError, OSError):
                    pass
        if release_port is not None:
            release_port(handle.port)
        return True

    async def stop_all(self) -> int:
        count = 0
        for session_id in list(self._procs):
            if await self.stop(session_id):
                count += 1
        return count

    # ---- 内部：日志解析 / 崩溃重启 / 内存监控 -----------------------------

    async def _reader(self, handle: _ProcessHandle) -> None:
        """逐行读取 stdout/stderr，解析 HMR 与编译错误关键字。"""
        stream_tasks = [
            asyncio.create_task(self._drain(handle, handle.proc.stdout)),
            asyncio.create_task(self._drain(handle, handle.proc.stderr)),
        ]
        await asyncio.gather(*stream_tasks, return_exceptions=True)

    async def _drain(
        self,
        handle: _ProcessHandle,
        stream: asyncio.StreamReader | None,
    ) -> None:
        if stream is None:
            return
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            handle.logs.append(line)
            if len(handle.logs) > 2000:
                del handle.logs[:1000]
            self._parse_line(handle.session_id, line)

    def _parse_line(self, session_id: str, line: str) -> None:
        lower = line.lower()
        if "hmr update" in lower or "hmr connected" in lower:
            preview_events.emit_event_sync(
                preview_events.EVT_PREVIEW_HMR_CONNECTED,
                {
                    "session_id": session_id,
                    "status": "connected",
                    "timestamp": now_ms(),
                },
            )
        elif "hmr disconnected" in lower or "websocket error" in lower or "hmr reconnect" in lower:
            preview_events.emit_event_sync(
                preview_events.EVT_PREVIEW_HMR_DISCONNECTED,
                {
                    "session_id": session_id,
                    "status": "reconnecting",
                    "timestamp": now_ms(),
                },
            )
        elif (
            "[vite] error" in lower
            or "error while transforming" in lower
            or "internal server error" in lower
        ):
            preview_events.emit_event_sync(
                preview_events.EVT_PREVIEW_BUILD_ERROR,
                {
                    "session_id": session_id,
                    "error": line,
                    "file": _extract_file(line),
                    "line": None,
                    "column": None,
                    "timestamp": now_ms(),
                },
            )

    async def _watcher(self, handle: _ProcessHandle) -> None:
        """等待进程退出；异常退出时发 build_error 事件（重启由 SessionManager 决策）。"""
        try:
            await handle.proc.wait()
        except (ProcessLookupError, OSError):
            return
        if handle.stopped or handle not in self._procs.values():
            return
        preview_events.emit_event_sync(
            preview_events.EVT_PREVIEW_HMR_DISCONNECTED,
            {
                "session_id": handle.session_id,
                "status": "reconnecting",
                "timestamp": now_ms(),
            },
        )
        preview_events.emit_event_sync(
            preview_events.EVT_PREVIEW_BUILD_ERROR,
            {
                "session_id": handle.session_id,
                "error": f"Vite 子进程异常退出（exit={handle.proc.returncode}）",
                "file": None,
                "line": None,
                "column": None,
                "timestamp": now_ms(),
            },
        )

    async def _memory_monitor(self, handle: _ProcessHandle) -> None:
        """周期采样子进程内存，超限自动 kill（设计 §7 监控）。"""
        try:
            import psutil
        except ImportError:
            return
        while True:
            await asyncio.sleep(MEMORY_SAMPLE_SEC)
            if handle.stopped or handle.proc.returncode is not None:
                return
            try:
                proc = psutil.Process(handle.proc.pid)
                rss = proc.memory_info().rss
                if rss > self._max_memory_bytes:
                    preview_events.emit_event_sync(
                        preview_events.EVT_PREVIEW_BUILD_ERROR,
                        {
                            "session_id": handle.session_id,
                            "error": (
                                f"Vite 子进程内存超限（{rss / 1024 / 1024:.0f}MB"
                                " > 512MB），自动停止"
                            ),
                            "file": None,
                            "line": None,
                            "column": None,
                            "timestamp": now_ms(),
                        },
                    )
                    handle.stopped = True
                    with suppress(ProcessLookupError, OSError):
                        handle.proc.kill()
                    return
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                return


def _resolve_vite_command(project_path: str | Path) -> list[str]:
    """解析 Vite 可执行命令（参数列表）。

    优先项目内 node_modules（vite bin 脚本 / .bin 可执行），
    其次全局 PATH 上的 vite。找不到抛 ViteUnavailableError。

    返回 argv 列表而非单个字符串 —— create_subprocess_exec 把含空格
    的复合字符串当成单个可执行文件名，Windows 上必然失败。
    """
    root = Path(project_path)
    local_bin_js = root / "node_modules" / "vite" / "bin" / "vite.js"
    if local_bin_js.is_file():
        node = shutil.which("node")
        if node:
            return [node, local_bin_js.as_posix()]
    for candidate in (
        root / "node_modules" / ".bin" / "vite",
        root / "node_modules" / ".bin" / "vite.cmd",
    ):
        if candidate.exists():
            return [str(candidate)]
    global_vite = shutil.which("vite")
    if global_vite:
        return [global_vite]
    if sys.platform == "win32" and (root / "node_modules" / ".bin" / "vite.exe").exists():
        return [str(root / "node_modules" / ".bin" / "vite.exe")]
    raise ViteUnavailableError(
        "未找到 Vite：项目 node_modules 缺失或未安装 vite。"
        "请先运行依赖安装（POST /preview/install）或安装 Node.js ≥ 18。"
    )


def _sanitized_env() -> dict[str, str]:
    """进程环境白名单 —— 绝不继承 Agent 敏感环境变量。"""
    env: dict[str, str] = {}
    for key in ("PATH", "SystemRoot", "TEMP", "TMP", "NODE_ENV", "LANG", "LC_ALL"):
        if key in os.environ:
            env[key] = os.environ[key]
    for key in ("HOME", "USERPROFILE"):
        if key in os.environ:
            env[key] = os.environ[key]
    agent_port = os.environ.get("EAIDE_AGENT_PORT")
    if agent_port:
        env["EAIDE_AGENT_PORT"] = agent_port
    env.setdefault("NODE_ENV", "development")
    return env


def _extract_file(line: str) -> str | None:
    """从 Vite 错误行尽力提取文件路径（形如 /path/file.vue:12:5）。"""
    for part in line.split():
        if ("/" in part or "\\" in part) and (
            ":" in part or part.endswith((".vue", ".tsx", ".jsx", ".ts", ".js", ".html", ".svelte"))
        ):
            return part
    return None


_default_vite_manager: VitePreviewManager | None = None


def get_default_vite_manager() -> VitePreviewManager:
    global _default_vite_manager
    if _default_vite_manager is None:
        _default_vite_manager = VitePreviewManager()
    return _default_vite_manager


def reset_default_vite_manager() -> None:
    global _default_vite_manager
    _default_vite_manager = None
