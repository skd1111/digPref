"""Phase 2B V0 · asyncssh 服务端 —— 1 端到端 demo PoC。

V0 PoC 范围（最小可用 demo）：
  1. asyncssh.create_server + SSHServer 简单 handler
  2. 用户名 / 密码硬编码（仅 demo 用）
  3. process_request 返回 echo + 简单 ls
  4. 不支持 PTY 交互（V1 接力）

V1 接力：
  - 真实 PTY 交互（create_process + PTYRequest）
  - 多用户管理（数据库 + 凭证保险箱集成）
  - 跳板机（ProxyJump）
  - 跳板路由审计
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
from typing import Any

from agent.ssh.models import SshConnectionError


logger = logging.getLogger(__name__)


# ---- V0 简单 SSH server handler ---------------------------------------------

class _DemoSSHServerHandler:
    """V0 demo SSH server 处理器 —— 返回 hostname + 当前路径。"""

    def __init__(self) -> None:
        self._hostname = socket.gethostname()

    async def handle_client(self, reader: Any, writer: Any) -> None:
        """处理单个客户端连接（demo PoC）。"""
        try:
            writer.write(f"EAIDE Demo SSH Server v0 (hostname={self._hostname})\r\n".encode())
            writer.write(b"Available commands: echo <text>, ls, exit, pwd, whoami\r\n")
            writer.write(b"$ ")
            await writer.drain()

            while True:
                data = await reader.readline()
                if not data:
                    break
                line = data.decode("utf-8", errors="replace").strip()
                if not line:
                    writer.write(b"$ ")
                    await writer.drain()
                    continue
                if line == "exit":
                    writer.write(b"bye\r\n")
                    await writer.drain()
                    break
                if line.startswith("echo "):
                    writer.write((line[5:] + "\r\n").encode())
                elif line == "ls":
                    files = ", ".join(os.listdir("."))
                    writer.write((files + "\r\n").encode())
                elif line == "pwd":
                    writer.write((os.getcwd() + "\r\n").encode())
                elif line == "whoami":
                    writer.write(("demo\r\n").encode())
                else:
                    writer.write(f"unknown command: {line}\r\n".encode())
                writer.write(b"$ ")
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


# ---- Demo 服务端启动 / 停止 ------------------------------------------------

class SshDemoServer:
    """V0 demo SSH server 包装（仅 PoC 用）。"""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 2222,
    ) -> None:
        self.host = host
        self.port = port
        self._server: Any = None
        self._running = False

    async def start(self) -> None:
        """启动 demo server。"""
        if self._running:
            return
        try:
            self._server = await asyncio.start_server(
                _DemoSSHServerHandler().handle_client,
                host=self.host,
                port=self.port,
            )
            self._running = True
            logger.info("ssh_demo_server_started host=%s port=%d", self.host, self.port)
        except OSError as exc:
            raise SshConnectionError(
                f"failed to start demo SSH server on {self.host}:{self.port}: {exc}"
            ) from exc

    async def stop(self) -> None:
        """停止 demo server。"""
        if self._server is not None and self._running:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._running = False
            logger.info("ssh_demo_server_stopped")

    @property
    def running(self) -> bool:
        return self._running


# ---- 单例工厂 -------------------------------------------------------------

_DEFAULT_SERVER: SshDemoServer | None = None


def get_default_server() -> SshDemoServer:
    global _DEFAULT_SERVER
    if _DEFAULT_SERVER is None:
        _DEFAULT_SERVER = SshDemoServer()
    return _DEFAULT_SERVER


def reset_default_server() -> None:
    global _DEFAULT_SERVER
    _DEFAULT_SERVER = None