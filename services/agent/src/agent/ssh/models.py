"""Phase 2B V0 · 类 FinalShell SSH PTY PoC —— 数据模型。

V0 状态：
  - 数据类：SshSession / SshExecRequest / SshExecResponse / SftpEntry / PtyRequest
  - 枚举：AuthMethod / ConnectionStatus / PtyMode
  - 异常：SshConnectionError / SshAuthError / SshCommandError / SshSessionNotFoundError
  - 工具：sanitize_host / sanitize_path

V1 接力（V1 阶段）：
  - 真实 PTY 交互（双向 stdin/stdout 流）
  - 端口转发（local / remote）
  - 跳板机（ProxyJump）
  - 文件传输进度回调
  - 会话自动重连 + 心跳
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


# ---- 常量 ---------------------------------------------------------------------

# 主机名合法性（防止命令注入到 SSH target）
_HOST_REGEX = re.compile(r"^[a-zA-Z0-9._\-]+$")
_IPV4_REGEX = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")

# 用户名合法性
_USER_REGEX = re.compile(r"^[a-zA-Z0-9._\-@]+$")

# 最大命令长度
_MAX_CMD_LEN = 8192

# 最大并发会话数
_MAX_SESSIONS = 32


# ---- 枚举 ---------------------------------------------------------------------

class AuthMethod(str, Enum):
    """认证方式。"""
    PASSWORD = "password"
    PUBLICKEY = "publickey"   # V1 接力
    NONE = "none"             # V1 接力


class ConnectionStatus(str, Enum):
    """会话状态。"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"


class PtyMode(str, Enum):
    """PTY 交互模式（V1 接力；V0 仅 echo 模式）。"""
    NONE = "none"
    ECHO = "echo"             # V0 默认：单命令执行
    INTERACTIVE = "interactive"  # V1 接力：双向 stdin/stdout 流


# ---- 数据类 -------------------------------------------------------------------

@dataclass
class SshSession:
    """SSH 会话信息。

    Attributes:
        session_id: UUID4 hex（与 SSE event 关联）。
        host: 主机名或 IP。
        port: 端口（默认 22）。
        username: 用户名。
        auth_method: 认证方式。
        status: 当前连接状态。
        created_at: 创建时间 ISO 8601 UTC。
        last_used: 最后一次使用时间。
        pty_mode: PTY 模式（V0 = echo）。
    """
    session_id: str
    host: str
    port: int = 22
    username: str = ""
    auth_method: AuthMethod = AuthMethod.PASSWORD
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    created_at: str = ""
    last_used: str = ""
    pty_mode: PtyMode = PtyMode.ECHO
    meta: dict = field(default_factory=dict)


@dataclass
class SshExecRequest:
    """远程命令执行请求。

    Attributes:
        session_id: 已连接的 session_id。
        command: 要执行的命令（V0 echo 模式直接传完整命令行）。
        timeout_sec: 命令超时（默认 30s）。
    """
    session_id: str
    command: str
    timeout_sec: float = 30.0


@dataclass
class SshExecResponse:
    """远程命令执行响应。

    Attributes:
        session_id: 关联 session_id。
        command: 执行的命令。
        exit_code: 退出码（None 表示超时或异常）。
        stdout: 标准输出（解码为 utf-8 字符串；非 utf-8 用 replace errors）。
        stderr: 标准错误。
        elapsed_ms: 命令耗时。
        ok: 是否成功（exit_code == 0 且未超时）。
    """
    session_id: str
    command: str
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: int = 0
    ok: bool = False
    error: str | None = None


@dataclass
class SftpEntry:
    """SFTP 文件 / 目录条目。

    Attributes:
        path: 完整路径。
        name: basename。
        is_dir: 是否目录。
        size: 文件大小（字节；目录时为 None）。
        mtime: 修改时间（Unix timestamp；None 表示未知）。
        permissions: 权限位（如 0o755）。
    """
    path: str
    name: str
    is_dir: bool
    size: int | None = None
    mtime: int | None = None
    permissions: int | None = None


@dataclass
class PtyRequest:
    """PTY 交互请求（V1 接力；V0 占位）。"""
    session_id: str
    cols: int = 80
    rows: int = 24
    term_type: str = "xterm-256color"


# ---- 异常 ---------------------------------------------------------------------

class SshError(Exception):
    """SSH 通用错误。"""


class SshConnectionError(SshError):
    """SSH 连接失败（网络不可达 / 认证失败 / 主机拒绝）。"""


class SshAuthError(SshError):
    """认证失败（密码错误 / 公钥未注册）。"""


class SshCommandError(SshError):
    """命令执行失败（超时 / 非零退出 / 流错误）。"""


class SshSessionNotFoundError(SshError):
    """会话不存在或已断开。"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"SSH session not found or disconnected: {session_id}")


class SshPathSecurityError(SshError):
    """SFTP 路径非法（命令注入 / 路径穿越）。"""


# ---- 工具函数 ---------------------------------------------------------------

def sanitize_host(host: str) -> str:
    """校验主机名合法性（防止命令注入到 ssh 命令行）。

    Args:
        host: 主机名 / IP / IPv6。

    Returns:
        原 host。

    Raises:
        SshPathSecurityError: 主机名含非法字符。
    """
    if not host:
        raise SshPathSecurityError("empty host")
    # IPv6: [::1]:22 拆出来校验
    if host.startswith("["):
        return host  # IPv6 字面量，交给 asyncssh 校验
    if _IPV4_REGEX.match(host):
        # 校验每个段 0..255
        try:
            for seg in host.split("."):
                v = int(seg)
                if not (0 <= v <= 255):
                    raise SshPathSecurityError(f"invalid IPv4 segment: {seg}")
        except ValueError as exc:
            raise SshPathSecurityError(f"invalid IPv4: {exc}") from exc
        return host
    if _HOST_REGEX.match(host):
        return host
    raise SshPathSecurityError(f"invalid host: {host!r}")


def sanitize_user(user: str) -> str:
    """校验用户名合法性。"""
    if not user:
        raise SshPathSecurityError("empty username")
    if len(user) > 128:
        raise SshPathSecurityError(f"username too long: {len(user)}")
    if not _USER_REGEX.match(user):
        raise SshPathSecurityError(f"invalid username: {user!r}")
    return user


def sanitize_command(command: str) -> str:
    """校验命令长度（防止超长命令 DoS）。

    V0 不做内容过滤（用户对会话有完全控制权）；
    V1 接力时如启用 WorkMode 联动，可加入危险命令黑名单（rm -rf /, dd, mkfs 等）。
    """
    if not command:
        raise SshPathSecurityError("empty command")
    if len(command) > _MAX_CMD_LEN:
        raise SshPathSecurityError(f"command too long: {len(command)} > {_MAX_CMD_LEN}")
    return command


def sanitize_path(path: str) -> str:
    """校验 SFTP 路径（防止命令注入）。

    不允许：
    - 空字符串
    - 反引号 ` / $ / ; / | / & （命令注入元字符）
    - 换行符 / 回车符
    """
    if not path:
        raise SshPathSecurityError("empty path")
    # 拒绝命令注入元字符
    forbidden = ["`", "$", ";", "|", "&", "\n", "\r", ">", "<"]
    for ch in forbidden:
        if ch in path:
            raise SshPathSecurityError(f"forbidden char in path: {ch!r}")
    return path


def check_session_limit(active_count: int) -> None:
    """校验并发会话数限制。"""
    if active_count >= _MAX_SESSIONS:
        raise SshConnectionError(
            f"too many concurrent SSH sessions: {active_count} >= {_MAX_SESSIONS}"
        )