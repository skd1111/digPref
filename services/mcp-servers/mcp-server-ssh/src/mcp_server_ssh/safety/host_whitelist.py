"""主机白名单 —— 拒绝连接到白名单之外的主机。

支持格式：
  - 精确匹配：host = "192.168.1.1" 或 "web.example.com"
  - user@host 格式：自动剥离 user@ 前缀后匹配
  - CIDR 子网：host = "10.0.0.0/8"（使用 ipaddress 模块匹配）
"""
from __future__ import annotations

import ipaddress

from mcp_server_ssh.config import Settings


class HostNotAllowedError(Exception):
    pass


def _normalize_host(host: str) -> str:
    """剥离 user@ 前缀，返回纯主机名/IP。"""
    if "@" in host:
        return host.rsplit("@", 1)[-1]
    return host


def assert_allowed(host: str) -> None:
    allowed = Settings().allowed_hosts
    if not allowed:
        raise HostNotAllowedError(f"未配置 SSH 白名单，拒绝连接: {host!r}")
    normalized = _normalize_host(host)
    for entry in allowed:
        # CIDR 匹配
        if "/" in entry:
            try:
                net = ipaddress.ip_network(entry, strict=False)
                if ipaddress.ip_address(normalized) in net:
                    return
            except ValueError:
                pass  # 不是有效 IP/CIDR，跳过
        # 精确匹配
        if normalized == entry:
            return
    raise HostNotAllowedError(f"SSH 主机 {host!r} 不在白名单中")