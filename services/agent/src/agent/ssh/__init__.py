"""Phase 2B V0 · 类 FinalShell SSH PTY PoC —— V0 公开 API。

V0 PoC 范围（2.5 工作日）：
  - asyncssh 2.24.0 客户端封装（connect / disconnect / exec / sftp_ls / sftp_get）
  - session_manager 单例 + 并发限制
  - SQLite ssh.db 持久化（ssh_sessions + ssh_commands 双表）
  - SSE 三处同步 4 新事件
  - FastAPI 7 端点
  - 1 demo SSH server（端口 2222，echo / ls / pwd / whoami）
  - 密码脱敏 + 命令 sanitize + 主机名校验
  - 34+ 测试覆盖

V0 不做（V1 接力）：
  - 真实 PTY 交互（双向 stdin/stdout）
  - publickey 认证
  - 端口转发（local / remote）
  - 跳板机（ProxyJump）
  - 会话自动重连 / 心跳 / 超时回收
  - 前端 SSH 面板 UI
  - 模型文件 / 已知主机 known_hosts 校验
"""
from __future__ import annotations

from agent.ssh.api import router as ssh_api_router
from agent.ssh.client import SshClient, _scrub_password
from agent.ssh.events import (
    EVT_SSH_COMMAND_DONE,
    EVT_SSH_CONNECTED,
    EVT_SSH_DISCONNECTED,
    EVT_SSH_ERROR,
)
from agent.ssh.models import (
    AuthMethod,
    ConnectionStatus,
    PtyMode,
    SftpEntry,
    SshAuthError,
    SshCommandError,
    SshConnectionError,
    SshError,
    SshExecRequest,
    SshExecResponse,
    SshPathSecurityError,
    SshSession,
    SshSessionNotFoundError,
    check_session_limit,
    sanitize_command,
    sanitize_host,
    sanitize_path,
    sanitize_user,
)
from agent.ssh.server import SshDemoServer, get_default_server, reset_default_server
from agent.ssh.session_manager import SshSessionManager, get_default_manager, reset_default_manager
from agent.ssh.storage import SshStorage, get_default_storage, reset_default_storage


__all__ = [
    # 数据类
    "SshSession", "SshExecRequest", "SshExecResponse", "SftpEntry",
    # 枚举
    "AuthMethod", "ConnectionStatus", "PtyMode",
    # 异常
    "SshError", "SshConnectionError", "SshAuthError",
    "SshCommandError", "SshSessionNotFoundError", "SshPathSecurityError",
    # 工具
    "sanitize_host", "sanitize_user", "sanitize_command", "sanitize_path", "check_session_limit",
    # 客户端
    "SshClient", "_scrub_password",
    # 服务端
    "SshDemoServer", "get_default_server", "reset_default_server",
    # 会话管理
    "SshSessionManager", "get_default_manager", "reset_default_manager",
    # 存储
    "SshStorage", "get_default_storage", "reset_default_storage",
    # 事件常量
    "EVT_SSH_CONNECTED", "EVT_SSH_DISCONNECTED",
    "EVT_SSH_COMMAND_DONE", "EVT_SSH_ERROR",
    # API router
    "ssh_api_router",
]