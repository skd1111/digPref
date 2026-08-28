"""执行过程可视化（阶段二） · JsonRpcStdioClient 协议测试。

用内嵌 Python 假执行器（逐行回 JSON-RPC）验证客户端协议行为，
不依赖真实 eaide-executor 二进制（构建产物不在测试环境保证范围内）。
"""

from __future__ import annotations

import asyncio
import sys
import textwrap

import pytest
from agent.builtin.jsonrpc_stdio import JsonRpcStdioClient

# 假执行器：读一行 JSON 请求 → 按 method 回一行 JSON 响应。
# ping → pong；builtin_echo → 原样返回 params；builtin_fail → JSON-RPC error。
_FAKE_EXECUTOR = textwrap.dedent(
    """
    import json, sys
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        rid = req.get("id")
        method = req.get("method")
        if method == "ping":
            resp = {"jsonrpc": "2.0", "id": rid, "result": {"pong": True}}
        elif method == "builtin_echo":
            resp = {"jsonrpc": "2.0", "id": rid, "result": dict(req.get("params") or {}, ok=True)}
        elif method == "builtin_fail":
            resp = {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32000, "message": "boom"}}
        elif method == "builtin_slow":
            import time; time.sleep(0.2)
            resp = {"jsonrpc": "2.0", "id": rid, "result": {"ok": True}}
        else:
            resp = {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": "unknown"}}
        sys.stdout.write(json.dumps(resp) + "\\n")
        sys.stdout.flush()
    """
)


def _client() -> JsonRpcStdioClient:
    return JsonRpcStdioClient(sys.executable)


@pytest.fixture
async def client():
    c = JsonRpcStdioClient(sys.executable)
    # 用 -c 拉起假执行器（与真实二进制同一 stdio 协议）
    c._binary = sys.executable
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        _FAKE_EXECUTOR,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    c._proc = proc
    c._reader_task = asyncio.create_task(c._read_loop())
    c._stderr_task = asyncio.create_task(c._stderr_loop())
    c._started = True
    yield c
    await c.stop()


async def test_invoke_roundtrip(client: JsonRpcStdioClient) -> None:
    result = await client.invoke("builtin_echo", {"path": "x.txt", "allowed_roots": []})
    assert result["ok"] is True
    assert result["path"] == "x.txt"


async def test_invoke_error_raises(client: JsonRpcStdioClient) -> None:
    with pytest.raises(RuntimeError, match="boom"):
        await client.invoke("builtin_fail", {})


async def test_unknown_method_raises(client: JsonRpcStdioClient) -> None:
    with pytest.raises(RuntimeError, match="-32601"):
        await client.invoke("no_such_method", {})


async def test_concurrent_calls_pair_by_id(client: JsonRpcStdioClient) -> None:
    """并发调用按 id 配对（方案 20.3：多工具并发时结果不能串）。"""
    results = await asyncio.gather(
        client.invoke("builtin_echo", {"n": 1}),
        client.invoke("builtin_echo", {"n": 2}),
        client.invoke("builtin_echo", {"n": 3}),
    )
    assert sorted(r["n"] for r in results) == [1, 2, 3]


async def test_invoke_after_stop_raises() -> None:
    c = _client()  # 未 start
    with pytest.raises(RuntimeError, match="not running"):
        await c.invoke("ping", {})


async def test_stop_is_idempotent() -> None:
    c = _client()
    await c.stop()
    await c.stop()  # 不抛即通过


async def test_resolve_respects_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from agent.builtin.jsonrpc_stdio import resolve_executor_bin

    fake = tmp_path / "eaide-executor.exe"
    fake.write_bytes(b"MZ")
    monkeypatch.setenv("EAIDE_EXECUTOR_BIN", str(fake))
    assert resolve_executor_bin() == str(fake)
