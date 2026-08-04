"""Phase 15 V0 · 预览会话管理器（SessionManager）。

职责：
  - 会话 CRUD + 状态机（starting → running / installing / errored / stopped）
  - 端口位图分配 / 释放
  - Vite 子进程启动 / 停止 / 崩溃自动重启（1 次）
  - preview.db 持久化 + audit.sqlite 审计
  - 不活跃会话 30 分钟自动停止
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from agent.preview import events as preview_events
from agent.preview.audit import audit_preview
from agent.preview.config_generator import generate as generate_config
from agent.preview.framework_detector import detect_framework, find_project_root
from agent.preview.models import (
    PreviewSession,
    PreviewStatus,
    StartPreviewRequest,
    now_ms,
)
from agent.preview.path_policy import (
    PreviewPathNotAllowedError,
    validate_project_path,
)
from agent.preview.port_allocator import PortAllocator
from agent.preview.storage import PreviewStorage
from agent.preview.vite_manager import VitePreviewManager, ViteUnavailableError

INACTIVE_TIMEOUT_SEC = 30 * 60  # 30 分钟无活动自动停止（设计 §5.2）
SESSION_SWEEP_SEC = 60.0
CRASH_RESTART_DELAY_SEC = 1.0


class PreviewError(RuntimeError):
    """预览会话业务错误。"""


class SessionManager:
    """预览会话编排（进程 + 端口 + 状态 + 持久化）。"""

    def __init__(
        self,
        *,
        vite: VitePreviewManager | None = None,
        allocator: PortAllocator | None = None,
        storage: PreviewStorage | None = None,
        inactive_timeout_sec: int = INACTIVE_TIMEOUT_SEC,
    ) -> None:
        from agent.preview.port_allocator import get_default_allocator
        from agent.preview.storage import get_default_storage
        from agent.preview.vite_manager import get_default_vite_manager

        self._vite = vite or get_default_vite_manager()
        self._allocator = allocator or get_default_allocator()
        self._storage = storage or get_default_storage()
        self._inactive_timeout_sec = inactive_timeout_sec
        self._sessions: dict[str, PreviewSession] = {}
        self._sweep_task: asyncio.Task[None] | None = None
        # 构造即注册为全局默认（与 ssh.session_manager 同模式）——
        # 让 FastAPI 路由（get_default_manager）直接拿到本实例
        global _default_manager
        _default_manager = self

    # ---- 生命周期 ---------------------------------------------------------

    async def start(self, req: StartPreviewRequest) -> PreviewSession:
        """一键启动预览会话。

        流程：校验路径 → 检测框架 → 分配端口 → 生成配置 → 启动 Vite 子进程
        → 落库 + 审计 → 返回会话。启动失败时回滚（释放端口）。
        """
        project_root = _resolve_project_root(req.project_path)
        if project_root is None:
            raise PreviewError(f"项目目录不存在或找不到 package.json: {req.project_path}")
        try:
            project_root = validate_project_path(project_root)
        except PreviewPathNotAllowedError as exc:
            raise PreviewError(str(exc)) from exc

        framework = req.framework or detect_framework(project_root)
        port = req.port or self._allocator.allocate()
        if port is None:
            raise PreviewError("端口范围 5173-5300 已全部占用，请先停止部分预览会话")
        if not self._allocator.is_allocated(port):
            self._allocator.allocate(preferred=port)

        session_id = uuid.uuid4().hex
        url = f"http://127.0.0.1:{port}"
        session = PreviewSession(
            id=session_id,
            project_path=str(project_root),
            entry_file=req.entry_file or "",
            framework=framework,
            port=port,
            url=url,
            status=PreviewStatus.STARTING,
            created_at=now_ms(),
            last_active_at=now_ms(),
        )
        self._sessions[session_id] = session

        # 配置生成失败 → 回滚
        try:
            config_path = generate_config(project_root, framework, port)
        except OSError as exc:
            self._allocator.release(port)
            self._sessions.pop(session_id, None)
            raise PreviewError(f"生成 Vite 配置失败: {exc}") from exc
        session.config_path = config_path

        try:
            pid = await self._vite.start(
                session_id=session_id,
                project_path=str(project_root),
                config_path=config_path,
                port=port,
            )
        except (ViteUnavailableError, OSError) as exc:
            self._allocator.release(port)
            self._sessions.pop(session_id, None)
            raise PreviewError(str(exc)) from exc

        session.pid = pid
        session.status = PreviewStatus.RUNNING
        session.last_active_at = now_ms()
        await self._persist(session)
        await audit_preview(
            "preview_session_started",
            {
                "session_id": session.id,
                "project_path": session.project_path,
                "framework": session.framework.value,
                "port": session.port,
                "pid": pid,
            },
        )
        self._ensure_sweep()
        return session

    async def stop(self, session_id: str) -> PreviewSession:
        """停止会话（终止子进程 + 释放端口 + 审计）。"""
        session = self._sessions.get(session_id)
        if session is None:
            raise PreviewError(f"会话不存在: {session_id}")
        if await self._vite.stop(
            session_id,
            release_port=self._release_port,
        ):
            pass
        elif session.status != PreviewStatus.ERRORED:
            self._allocator.release(session.port)
        session.status = PreviewStatus.STOPPED
        session.last_active_at = now_ms()
        await self._persist(session)
        await audit_preview(
            "preview_session_stopped",
            {
                "session_id": session.id,
                "project_path": session.project_path,
                "port": session.port,
            },
        )
        return session

    def _release_port(self, port: int) -> None:
        """释放端口（满足 VitePreviewManager.stop 的 Callable[[int], None]）。"""
        self._allocator.release(port)

    async def reload(self, session_id: str) -> PreviewSession:
        """强制刷新：Vite full-reload（子进程存活时发 reload 信号事件）。"""
        session = self._sessions.get(session_id)
        if session is None:
            raise PreviewError(f"会话不存在: {session_id}")
        if session.status in (PreviewStatus.STOPPED, PreviewStatus.ERRORED):
            raise PreviewError(f"会话已停止，无法刷新: {session_id}")
        # Vite 无 HTTP full-reload API（生产不暴露）；这里 touch 配置触发 watcher
        if session.config_path:
            path = Path(session.config_path)
            await asyncio.to_thread(path.touch)
        session.last_active_at = now_ms()
        await self._persist(session)
        return session

    async def restart_crashed(self, session_id: str) -> bool:
        """崩溃自动重启（1 次）。重启成功返回 True。"""
        session = self._sessions.get(session_id)
        if session is None or session.config_path is None:
            return False
        await asyncio.sleep(CRASH_RESTART_DELAY_SEC)
        try:
            pid = await self._vite.start(
                session_id=session_id,
                project_path=session.project_path,
                config_path=session.config_path,
                port=session.port,
            )
        except (ViteUnavailableError, OSError):
            session.status = PreviewStatus.ERRORED
            session.error = "Vite 子进程崩溃且自动重启失败"
            await self._persist(session)
            await audit_preview(
                "preview_session_errored",
                {
                    "session_id": session.id,
                    "error": session.error,
                },
            )
            return False
        session.pid = pid
        session.status = PreviewStatus.RUNNING
        session.last_active_at = now_ms()
        session.error = None
        await self._persist(session)
        preview_events.emit_event_sync(
            preview_events.EVT_PREVIEW_HMR_CONNECTED,
            {
                "session_id": session_id,
                "status": "connected",
                "timestamp": now_ms(),
            },
        )
        return True

    async def set_install_progress(self, session_id: str, progress: int) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.install_progress = max(0, min(100, progress))
        if progress < 100:
            session.status = PreviewStatus.INSTALLING
        else:
            session.status = PreviewStatus.RUNNING
        session.last_active_at = now_ms()
        await self._persist(session)

    # ---- 查询 -------------------------------------------------------------

    def get(self, session_id: str) -> PreviewSession | None:
        return self._sessions.get(session_id)

    def list_active(self) -> list[PreviewSession]:
        return [
            s
            for s in self._sessions.values()
            if s.status in (PreviewStatus.STARTING, PreviewStatus.RUNNING, PreviewStatus.INSTALLING)
        ]

    def list_all(self) -> list[PreviewSession]:
        return sorted(self._sessions.values(), key=lambda s: s.created_at, reverse=True)

    def touch(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_active_at = now_ms()

    # ---- 内部 -------------------------------------------------------------

    async def _persist(self, session: PreviewSession) -> None:
        from contextlib import suppress

        with suppress(Exception):  # noqa: BLE001 —— 持久化失败不阻断主流程
            await self._storage.upsert_session(session)

    def _ensure_sweep(self) -> None:
        if self._sweep_task is None or self._sweep_task.done():
            self._sweep_task = asyncio.create_task(self._sweep())

    async def _sweep(self) -> None:
        """周期任务：不活跃会话自动停止 + 崩溃会话自动重启（1 次）。"""
        while True:
            await asyncio.sleep(SESSION_SWEEP_SEC)
            for session in list(self._sessions.values()):
                if session.status == PreviewStatus.STOPPED:
                    self._sessions.pop(session.id, None)
                    continue
                if session.status == PreviewStatus.ERRORED:
                    continue
                alive = self._vite.is_running(session.id)
                if not alive and session.status == PreviewStatus.RUNNING:
                    # 崩溃 → 自动重启（1 次；restart_crashed 内再失败标 errored）
                    await self.restart_crashed(session.id)
                    continue
                if not alive:
                    continue
                active = now_ms() - session.last_active_at < self._inactive_timeout_sec * 1000
                if not active:
                    await self.stop(session.id)

    async def shutdown(self) -> None:
        """Agent 关闭时终止全部子进程。"""
        if self._sweep_task is not None:
            self._sweep_task.cancel()
        await self._vite.stop_all()


def _resolve_project_root(project_path: str) -> Path | None:
    """规范化项目根路径（实现文档 §6：Windows 中文路径 as_posix 归一）。"""
    p = Path(project_path).expanduser()
    if p.is_file():
        p = p.parent
    p = p.resolve(strict=False)
    if not p.exists():
        # 文件本身不存在 → 尝试向上找 package.json
        root = find_project_root(project_path)
        return root
    if p.is_dir():
        return p
    return None


_default_manager: SessionManager | None = None


def get_default_manager() -> SessionManager:
    global _default_manager
    if _default_manager is None:
        _default_manager = SessionManager()
    return _default_manager


def reset_default_manager() -> None:
    global _default_manager
    _default_manager = None
