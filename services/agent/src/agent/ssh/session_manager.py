"""Phase 2B V0 · SSH 会话管理器 —— 多会话生命周期 + 并发限制。

V0 职责：
  1. session_id → SshClient 字典映射
  2. 并发上限校验（_MAX_SESSIONS = 32）
  3. 自动清理已断开会话
  4. 线程安全（asyncio.Lock）

V1 接力：
  - 会话自动重连（心跳检测 + 重试）
  - 会话超时回收（idle > 30min 自动 disconnect）
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from agent.ssh.client import SshClient
from agent.ssh.models import (
    AuthMethod,
    ConnectionStatus,
    PtyMode,
    SshSession,
    check_session_limit,
)

logger = logging.getLogger(__name__)


class SshSessionManager:
    """全局 SSH 会话管理器（单例）。"""

    def __init__(self) -> None:
        self._sessions: dict[str, SshClient] = {}
        self._session_meta: dict[str, SshSession] = {}
        self._lock = asyncio.Lock()

    async def connect(
        self,
        *,
        host: str,
        port: int = 22,
        username: str = "",
        password: str | None = None,
        auth_method: AuthMethod = AuthMethod.PASSWORD,
        pty_mode: PtyMode = PtyMode.ECHO,
        connect_timeout: float = 10.0,
    ) -> SshSession:
        """创建新会话 + 连接。

        Raises:
            SshConnectionError: 网络不可达 / 超时。
            SshAuthError: 认证失败。
            SshConnectionError: 达到 _MAX_SESSIONS 上限。
        """
        async with self._lock:
            check_session_limit(len(self._sessions))

            client = SshClient(
                host=host,
                port=port,
                username=username,
                password=password,
                auth_method=auth_method,
                pty_mode=pty_mode,
                connect_timeout=connect_timeout,
            )
            await client.connect()  # 失败抛 SshError
            session_id = uuid.uuid4().hex
            now = datetime.now(timezone.utc).isoformat()
            self._sessions[session_id] = client
            self._session_meta[session_id] = SshSession(
                session_id=session_id,
                host=host,
                port=port,
                username=username,
                auth_method=auth_method,
                status=ConnectionStatus.CONNECTED,
                created_at=now,
                last_used=now,
                pty_mode=pty_mode,
            )
            return self._session_meta[session_id]

    async def disconnect(self, session_id: str) -> bool:
        """断开并清理指定 session。返 True 表示存在并断开，False 表示不存在。"""
        async with self._lock:
            client = self._sessions.pop(session_id, None)
            meta = self._session_meta.pop(session_id, None)
            if client is None:
                return False
            await client.disconnect()
            logger.info(
                "ssh_session_disconnected session_id=%s host=%s",
                session_id,
                meta.host if meta else "?",
            )
            return True

    async def disconnect_all(self) -> int:
        """断开所有 session。返断开数量。"""
        async with self._lock:
            count = len(self._sessions)
            for _sid, client in list(self._sessions.items()):
                await client.disconnect()
            self._sessions.clear()
            self._session_meta.clear()
            return count

    def get_client(self, session_id: str) -> SshClient | None:
        """获取客户端对象（仅供 api.py / session_manager 内部使用）。"""
        return self._sessions.get(session_id)

    def get_meta(self, session_id: str) -> SshSession | None:
        return self._session_meta.get(session_id)

    def list_sessions(self) -> list[SshSession]:
        return list(self._session_meta.values())

    async def touch(self, session_id: str) -> None:
        """更新 last_used 时间戳。"""
        meta = self._session_meta.get(session_id)
        if meta:
            meta.last_used = datetime.now(timezone.utc).isoformat()


# ---- 单例工厂 -------------------------------------------------------------

_DEFAULT_MANAGER: SshSessionManager | None = None


def get_default_manager() -> SshSessionManager:
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = SshSessionManager()
    return _DEFAULT_MANAGER


def reset_default_manager() -> None:
    """测试 hook：重置单例 + 断开所有会话。"""
    global _DEFAULT_MANAGER
    if _DEFAULT_MANAGER is not None:
        # 同步断开所有（不需要 await —— 测试环境已经关闭 event loop）
        for client in _DEFAULT_MANAGER._sessions.values():
            try:
                client._conn.close() if client._conn else None
            except Exception:
                pass
    _DEFAULT_MANAGER = None
