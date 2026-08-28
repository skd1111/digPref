"""执行过程可视化（阶段二） · eaide-executor JSON-RPC stdio 客户端。

定位：`TauriRuntimeClient` 协议的第二种实现（第一种是桌面壳注入的 IPC 桥）。
Agent 独立运行（uvicorn / PyInstaller exe，无桌面壳注入）时，由 `main.py`
lifespan 拉起 `eaide-executor` 子进程并注入 —— 9 个 Rust 工具统一走
Rust 沙箱实现（`builtin/path_sandbox.rs`），消除「Python 原生兜底与 Rust
沙箱安全边界不一致」的缺口；二进制缺失 / 子进程故障时返错，
`tauri_bridge` 仍按既有链路降级到 Python 原生兜底。

协议纪律（与 Rust 端 `executor_rpc.rs` 镜像）：
    - stdout 只传 JSON-RPC 2.0（一行一条），解析失败即丢弃该行；
    - 子进程 stderr 转发到本进程日志（诊断用，绝不进协议流）；
    - 方法名 = Tauri command 名（`builtin_<tool>`），参数结构与
      `tauri_bridge.build_rust_args` 产出一致 —— 两种壳无缝切换。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# 二进制名（平台后缀）
_EXECUTOR_NAMES: tuple[str, ...] = (
    ("eaide-executor.exe",) if sys.platform == "win32" else ("eaide-executor",)
)
# 随包分发相对路径（与 tauri.conf.json resources / eaide-agent.spec datas 对齐）
_VENDOR_REL_DIR = "vendor/executor"


def _bundled_candidates() -> list[Path]:
    """捆绑二进制候选路径：cwd 优先，缺失回退 PyInstaller _MEIPASS。

    与 officecli_runtime._bundled_candidates 同一模式（BUGFIX #133 教训：
    运行时解析一律多级回退，缺失返 None 而不是崩溃）。
    """
    roots = [Path.cwd()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    # 开发态：从模块位置向上推导仓库根，兼容 target/release 与 target/debug
    candidates: list[Path] = []
    try:
        repo_root = Path(__file__).resolve().parents[5]
        target_dir = repo_root / "apps" / "desktop" / "src-tauri" / "target"
        for profile in ("release", "debug"):
            candidates.extend(target_dir / profile / n for n in _EXECUTOR_NAMES)
        roots.append(repo_root)
    except IndexError:
        pass
    candidates.extend(root / _VENDOR_REL_DIR / n for root in roots for n in _EXECUTOR_NAMES)
    return candidates


def resolve_executor_bin() -> str | None:
    """按多级回退定位 eaide-executor 二进制；全部缺失返 None。

    优先级：环境变量显式指定 > 随包候选路径（cwd/_MEIPASS/仓库根）> PATH。
    """
    override = (os.environ.get("EAIDE_EXECUTOR_BIN") or "").strip()
    if override and Path(override).is_file():
        return override
    for candidate in _bundled_candidates():
        if candidate.is_file():
            return str(candidate)
    return shutil.which("eaide-executor")


class JsonRpcStdioClient:
    """TauriRuntimeClient 实现：JSON-RPC 2.0 stdio 调独立 eaide-executor。

    Usage:
        client = JsonRpcStdioClient(binary_path)
        await client.start()
        result = await client.invoke("builtin_stat_file", {"path": ..., "allowed_roots": [...]})
        await client.stop()
    """

    def __init__(self, binary: str) -> None:
        self._binary = binary
        self._proc: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._started = False

    @property
    def running(self) -> bool:
        return self._started and self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        """拉起子进程（幂等；失败上抛由调用方兜底）。"""
        if self.running:
            return
        creation_flags = 0
        if sys.platform == "win32":
            # 不弹控制台窗口（桌面壳内子进程纪律，与 officecli 一致）
            import subprocess as _sp

            creation_flags = getattr(_sp, "CREATE_NO_WINDOW", 0)
        self._proc = await asyncio.create_subprocess_exec(
            self._binary,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creation_flags,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._stderr_task = asyncio.create_task(self._stderr_loop())
        self._started = True
        log.info("eaide-executor started", binary=self._binary, pid=self._proc.pid)

    async def invoke(self, command: str, args: dict[str, Any]) -> dict[str, Any]:
        """TauriRuntimeClient 协议方法：command = builtin_<tool>，args 直传 params。

        Raises:
            RuntimeError: 子进程未运行 / 已退出 / 协议错误（调用方降级兜底）。
        """
        if not self.running:
            raise RuntimeError("eaide-executor not running")
        assert self._proc is not None and self._proc.stdin is not None
        request_id = uuid.uuid4().hex
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": command,
            "params": args,
        }
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            self._proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            await self._proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError, OSError) as exc:
            self._pending.pop(request_id, None)
            raise RuntimeError(f"eaide-executor write failed: {exc}") from exc
        try:
            message = await asyncio.wait_for(future, timeout=120.0)
        except asyncio.TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise RuntimeError("eaide-executor response timeout (120s)") from exc
        if not isinstance(message, dict):
            raise RuntimeError("eaide-executor returned non-object result")
        if "error" in message and message.get("error"):
            err = message["error"]
            raise RuntimeError(f"eaide-executor error {err.get('code')}: {err.get('message')}")
        result = message.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("eaide-executor result is not a ToolResult dict")
        return result

    async def stop(self) -> None:
        """关闭子进程（幂等）；未决请求全部以错误了结。"""
        self._started = False
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("eaide-executor stopped"))
        self._pending.clear()
        proc = self._proc
        self._proc = None
        if proc is not None and proc.returncode is None:
            # 关 stdin = EOF，Rust 端循环自然退出；给 2s 宽限再强杀
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, OSError):
                try:
                    proc.kill()
                except OSError:
                    pass
        for task in (self._reader_task, self._stderr_task):
            if task is not None and not task.done():
                task.cancel()
        self._reader_task = None
        self._stderr_task = None

    # ---- 内部循环 ----------------------------------------------------------

    async def _read_loop(self) -> None:
        """逐行读 stdout 解析 JSON-RPC 响应 → 解析到 pending future。"""
        assert self._proc is not None and self._proc.stdout is not None
        try:
            while True:
                line = await self._proc.stdout.readline()
                if not line:
                    break
                try:
                    message = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue  # 协议纪律：非 JSON 行一律忽略
                request_id = message.get("id")
                future = (
                    self._pending.pop(request_id, None) if isinstance(request_id, str) else None
                )
                if future is not None and not future.done():
                    future.set_result(message)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("eaide-executor read loop crashed")
        finally:
            # 子进程退出 → 未决请求全部了结（调用方降级到 Python 兜底）
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(RuntimeError("eaide-executor exited"))
            self._pending.clear()

    async def _stderr_loop(self) -> None:
        """stderr 转发到日志（诊断用；绝不进协议流）。"""
        assert self._proc is not None and self._proc.stderr is not None
        try:
            while True:
                line = await self._proc.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    log.debug("eaide-executor stderr", line=text)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass


async def try_start_executor_client() -> JsonRpcStdioClient | None:
    """lifespan 启动辅助：定位二进制 → 拉起 → ping 探活。

    任何一步失败都返 None（调用方保持「无注入 → Python 原生兜底」既有降级），
    绝不让执行器故障阻断 Agent 启动。
    """
    binary = resolve_executor_bin()
    if not binary:
        log.info("eaide-executor binary not found; keep python-native fallback")
        return None
    client = JsonRpcStdioClient(binary)
    try:
        await client.start()
        pong = await asyncio.wait_for(client.invoke("ping", {}), timeout=5.0)
        if not pong.get("pong"):
            raise RuntimeError(f"unexpected ping result: {pong}")
    except Exception as exc:
        log.warning("eaide-executor startup failed; keep python-native fallback", error=str(exc))
        try:
            await client.stop()
        except Exception:
            pass
        return None
    return client
