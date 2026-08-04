"""ssh.exec —— 在远程主机上执行命令。"""
from __future__ import annotations

from mcp_server_ssh.client import connect
from mcp_server_ssh.config import Settings
from mcp_server_ssh.safety.cmd_blacklist import assert_safe
from mcp_server_ssh.safety.host_whitelist import assert_allowed


async def run(args: dict) -> dict:
    s = Settings()
    host = args["host"]
    command = args["command"]

    # 安全校验：主机白名单 + 命令黑名单
    assert_allowed(host)
    assert_safe(command)

    timeout = args.get("timeout_sec") or s.tool_timeout_sec
    async with await connect(host) as conn:
        proc = await conn.run(command, timeout=timeout)

    # 输出截断：保留开头部分（通常最有用的信息在前面）
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    truncated = len(stdout) > 8000 or len(stderr) > 4000

    return {
        "ok": proc.exit_status == 0,
        "exit_status": proc.exit_status,
        "stdout": stdout[:8000],   # 取前 8000 字符（而非末尾）
        "stderr": stderr[:4000],
        "truncated": truncated,
    }
