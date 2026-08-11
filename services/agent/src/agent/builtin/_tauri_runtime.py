"""Phase 1B V2 · Tauri 运行时句柄注入点。

架构说明（重要）：
    EAIDE 的 Python Agent 是 Tauri 桌面壳（Rust）拉起的**独立子进程**，
    Python 进程无法直接持有 Rust 侧的 `tauri::AppHandle`。因此本模块定义
    一个**可注入的运行时客户端协议**：

        class TauriRuntimeClient(Protocol):
            async def invoke(self, command: str, args: dict) -> dict: ...

    - 桌面壳集成时（V2 后续），宿主把实现该协议的客户端（HTTP / socket /
      stdio 桥）注入进来，`tauri_bridge` 即可真正调用 Rust 端 `builtin_*`
      Tauri Command。
    - Agent 独立运行（uvicorn / PyInstaller 直启）时无注入 → `get_tauri_app_handle()`
      返回 None → dispatcher 走 Python 原生兜底实现（delete/move/shell），
      保证原生工具层在所有部署形态下都可用。

线程安全：模块级单例 + threading.Lock（FastAPI lifespan 与 dispatcher 并发访问）。
"""

from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TauriRuntimeClient(Protocol):
    """Tauri 运行时客户端协议（宿主注入对象需实现）。"""

    async def invoke(self, command: str, args: dict[str, Any]) -> dict[str, Any]:
        """调用 Rust 端 Tauri Command，返回 ToolResult 结构 dict。"""
        ...


_LOCK = threading.Lock()
_RUNTIME: TauriRuntimeClient | None = None


def set_tauri_runtime(runtime: TauriRuntimeClient | None) -> None:
    """注入 / 清除 Tauri 运行时客户端。

    Args:
        runtime: 实现 `invoke(command, args) -> dict` 的客户端；传 None 清除。
    """
    global _RUNTIME
    with _LOCK:
        _RUNTIME = runtime


def get_tauri_app_handle() -> TauriRuntimeClient | None:
    """返回当前注入的运行时客户端（无注入 → None）。"""
    with _LOCK:
        return _RUNTIME


def clear_tauri_runtime() -> None:
    """清除运行时（测试 / 关闭用）。"""
    set_tauri_runtime(None)


def is_tauri_runtime_available() -> bool:
    """运行时是否可用。"""
    return get_tauri_app_handle() is not None
