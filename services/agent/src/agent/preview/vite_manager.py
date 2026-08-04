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
import os
import shutil
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.preview import events as preview_events
from agent.preview.models import now_ms

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
        config_path: str,
        port: int,
    ) -> int:
        """启动 Vite 子进程，返回 PID。

        失败抛 ViteUnavailableError / OSError。进程启动后立即返回；
        stderr/stdout 由后台任务解析（HMR 状态 / 编译错误）。
        """
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
            args = [cmd, "--config", config_path, "--port", str(port), "--strictPort"]
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


def _resolve_vite_command(project_path: str | Path) -> str:
    """解析 Vite 可执行路径。

    优先项目内 node_modules（vite bin 脚本 / .bin 可执行），
    其次全局 PATH 上的 vite。找不到抛 ViteUnavailableError。
    """
    root = Path(project_path)
    local_bin_js = root / "node_modules" / "vite" / "bin" / "vite.js"
    if local_bin_js.is_file():
        node = shutil.which("node")
        if node:
            return f"{node} {local_bin_js.as_posix()}"
    for candidate in (
        root / "node_modules" / ".bin" / "vite",
        root / "node_modules" / ".bin" / "vite.cmd",
    ):
        if candidate.exists():
            return str(candidate)
    if shutil.which("vite"):
        return "vite"
    if sys.platform == "win32" and (root / "node_modules" / ".bin" / "vite.exe").exists():
        return str(root / "node_modules" / ".bin" / "vite.exe")
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
