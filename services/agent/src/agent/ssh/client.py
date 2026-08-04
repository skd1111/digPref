"""Phase 2B V0 · asyncssh 客户端封装 —— PoC。

设计原则：
  1. V0 PoC：connect / disconnect / exec_command / sftp_ls / sftp_get 5 个核心操作
  2. asyncssh 全异步 API + 连接池（每个 session 一个 connect）
  3. 密码不在日志 / 审计中保留（_scrub_password helper）
  4. PTY 交互 V1 接力（V0 仅 echo 模式单命令执行）
  5. 错误统一转 SshError 子类

依赖：
  - asyncssh >= 2.14（已安装 2.24.0）
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from agent.ssh.models import (
    AuthMethod,
    ConnectionStatus,
    PtyMode,
    SftpEntry,
    SshAuthError,
    SshConnectionError,
    SshExecResponse,
    SshSession,
    check_session_limit,
    sanitize_command,
    sanitize_host,
    sanitize_path,
    sanitize_user,
)


logger = logging.getLogger(__name__)


# ---- 异步 SSH 客户端包装 -----------------------------------------------------

class SshClient:
    """单个 SSH 会话的客户端（V0 PoC）。

    生命周期：
        1. async with SshClient(...) as client: —— 自动连接
        2. await client.exec_command("ls -la")
        3. await client.sftp_ls("/path")
        4. async with 自动断开

    V0 仅支持 password 认证；V1 接力 publickey + ProxyJump。
    """

    def __init__(
        self,
        *,
        host: str,
        port: int = 22,
        username: str = "",
        password: str | None = None,
        auth_method: AuthMethod = AuthMethod.PASSWORD,
        client_keys: list[str] | None = None,  # V1 接力
        pty_mode: PtyMode = PtyMode.ECHO,
        connect_timeout: float = 10.0,
    ) -> None:
        # 校验 + 净化所有参数
        self._host = sanitize_host(host)
        self._port = port
        self._username = sanitize_user(username)
        self._password = password
        self._auth_method = auth_method
        self._pty_mode = pty_mode
        self._connect_timeout = connect_timeout

        self._conn: Any = None  # asyncssh.connect 返的对象
        self._sftp: Any = None  # lazy
        self._status = ConnectionStatus.DISCONNECTED

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    @property
    def username(self) -> str:
        return self._username

    @property
    def status(self) -> ConnectionStatus:
        return self._status

    async def connect(self) -> None:
        """异步连接到 SSH 服务端。

        Raises:
            SshConnectionError: 网络不可达 / 超时。
            SshAuthError: 密码错误 / 认证失败。
        """
        import asyncssh  # 延迟 import（测试环境可能未装）

        self._status = ConnectionStatus.CONNECTING
        try:
            # V0 仅 password 认证
            if self._auth_method == AuthMethod.PASSWORD:
                self._conn = await asyncio.wait_for(
                    asyncssh.connect(
                        host=self._host,
                        port=self._port,
                        username=self._username,
                        password=self._password,
                        known_hosts=None,  # V0 PoC 跳过 known_hosts 校验；V1 接力
                    ),
                    timeout=self._connect_timeout,
                )
            elif self._auth_method == AuthMethod.PUBLICKEY:
                # V1 接力
                raise NotImplementedError("publickey auth is V1; V0 only supports password")
            elif self._auth_method == AuthMethod.NONE:
                raise NotImplementedError("none auth not supported")
            else:
                raise ValueError(f"unknown auth_method: {self._auth_method}")
            self._status = ConnectionStatus.CONNECTED
            logger.info("ssh_connected host=%s port=%d user=%s", self._host, self._port, self._username)
        except asyncio.TimeoutError as exc:
            self._status = ConnectionStatus.ERROR
            raise SshConnectionError(f"connection timeout after {self._connect_timeout}s") from exc
        except asyncssh.PermissionDenied as exc:
            self._status = ConnectionStatus.ERROR
            raise SshAuthError(f"authentication failed: {exc}") from exc
        except asyncssh.Error as exc:
            self._status = ConnectionStatus.ERROR
            raise SshConnectionError(f"SSH error: {exc}") from exc
        except (OSError, asyncio.TimeoutError) as exc:
            self._status = ConnectionStatus.ERROR
            raise SshConnectionError(f"connection failed: {type(exc).__name__}: {exc}") from exc

    async def disconnect(self) -> None:
        """异步断开。"""
        if self._conn is not None:
            try:
                self._conn.close()
                await self._conn.wait_closed()
            except Exception:
                pass  # best-effort
            self._conn = None
            self._sftp = None
            self._status = ConnectionStatus.DISCONNECTED

    async def exec_command(
        self,
        command: str,
        *,
        timeout_sec: float = 30.0,
    ) -> SshExecResponse:
        """在远程主机执行命令（V0 echo 模式）。

        Args:
            command: shell 命令（已 sanitize）。
            timeout_sec: 命令超时。

        Returns:
            SshExecResponse 含 stdout / stderr / exit_code / ok。
        """
        if self._conn is None or self._status != ConnectionStatus.CONNECTED:
            raise SshConnectionError("not connected")

        command = sanitize_command(command)
        started = time.monotonic()

        try:
            # asyncssh.run 返回 CompletedProcess
            result = await asyncio.wait_for(
                self._conn.run(command, check=False),
                timeout=timeout_sec,
            )
            elapsed_ms = int((time.monotonic() - started) * 1000)
            stdout = result.stdout if isinstance(result.stdout, str) else (
                result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
            )
            stderr = result.stderr if isinstance(result.stderr, str) else (
                result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            )
            exit_code = result.exit_status if hasattr(result, "exit_status") else (
                result.returncode if hasattr(result, "returncode") else None
            )
            return SshExecResponse(
                session_id="",  # 实际 session_id 由调用方填充
                command=command,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                elapsed_ms=elapsed_ms,
                ok=exit_code == 0,
            )
        except asyncio.TimeoutError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return SshExecResponse(
                session_id="",
                command=command,
                exit_code=None,
                elapsed_ms=elapsed_ms,
                ok=False,
                error=f"command timeout after {timeout_sec}s",
            )

    async def sftp_ls(self, path: str = "/") -> list[SftpEntry]:
        """列出 SFTP 目录。

        Args:
            path: 远程目录路径（已 sanitize）。

        Returns:
            list[SftpEntry]：每个条目含 name / is_dir / size / mtime / permissions。
        """
        if self._conn is None or self._status != ConnectionStatus.CONNECTED:
            raise SshConnectionError("not connected")
        path = sanitize_path(path)
        if self._sftp is None:
            import asyncssh
            self._sftp = await self._conn.start_sftp_client()
        entries: list[SftpEntry] = []
        async for entry in self._sftp.scandir(path):
            attrs = entry.attrs
            entries.append(SftpEntry(
                path=f"{path.rstrip('/')}/{entry.name}",
                name=entry.name,
                is_dir=attrs.permissions is not None and (attrs.permissions & 0o40000) != 0,
                size=attrs.size if not (attrs.permissions and (attrs.permissions & 0o40000)) else None,
                mtime=int(attrs.mtime) if attrs.mtime else None,
                permissions=attrs.permissions,
            ))
        return entries

    async def sftp_get(self, remote_path: str, local_path: str) -> int:
        """下载远程文件到本地。

        Returns:
            下载字节数。
        """
        if self._conn is None or self._status != ConnectionStatus.CONNECTED:
            raise SshConnectionError("not connected")
        remote_path = sanitize_path(remote_path)
        if self._sftp is None:
            import asyncssh
            self._sftp = await self._conn.start_sftp_client()
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        async with self._sftp.open(remote_path, "rb") as src:
            data = await src.read()
        Path(local_path).write_bytes(data)
        return len(data)


# ---- 辅助：脱敏密码用于审计日志 ----------------------------------------------

def _scrub_password(args: dict) -> dict:
    """脱敏：password 字段只保留长度，不保留原值。"""
    scrubbed = dict(args)
    if "password" in scrubbed and isinstance(scrubbed["password"], str):
        scrubbed["password"] = {"length": len(scrubbed["password"])}
    return scrubbed


    # ---- 异步上下文管理器支持 ---------------------------------------------------

    async def __aenter__(self) -> "SshClient":
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.disconnect()