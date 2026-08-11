"""异步 SSH 客户端封装。

默认使用 ssh-agent 和 ~/.ssh/config 中的密钥配置。
不显式传 client_keys=None，让 asyncssh 走默认密钥发现流程。

Host key verification: 默认启用 known_hosts 校验。
设置环境变量 EAIDE_SSH_VERIFY_HOST_KEY=0 可绕过（仅限测试环境）。
"""

from __future__ import annotations

import os

import asyncssh


async def connect(host: str) -> asyncssh.SSHClientConnection:
    """连接到 SSH 主机。

    使用默认密钥发现（ssh-agent + ~/.ssh/id_*），
    而非显式禁用客户端密钥文件。

    Host key verification：默认要求 known_hosts 中存在对应条目。
    若 known_hosts 不存在或未记录目标主机，连接将被拒绝以防止 MITM 攻击。
    仅在 EAIDE_SSH_VERIFY_HOST_KEY=0 时跳过主机密钥校验（仅限测试环境）。
    """
    kwargs = {}
    if os.environ.get("EAIDE_SSH_VERIFY_HOST_KEY") == "0":
        kwargs["known_hosts"] = None  # skip host key verification (TEST ONLY)
    # else: asyncssh default — validates against ~/.ssh/known_hosts
    return await asyncssh.connect(host, **kwargs)
