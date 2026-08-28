"""执行过程可视化（阶段三） · 当前工具执行上下文（call_id / run_id）。

dispatcher 在真正执行工具前 `bind_exec_scope()`，工具实现（如 `shell.py` 的
流式输出）从上下文读取 call_id / run_id 给细粒度事件（shell_chunk /
tool_progress）盖章 —— 与同一次调用的 tool_call / tool_result 事件同源配对
（BUGFIX #164 的 call_id 贯穿原则）。

为什么不用显式参数：工具签名是跨 Python/Rust 两种执行形态的统一接口，
为可视化单独改 9 个工具签名不值当；contextvar 在 asyncio 任务链内自然继承。
"""

from __future__ import annotations

import contextvars

# 当前执行中的工具调用标识（无上下文 = None，事件静默不发）
CURRENT_CALL_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "eaide_builtin_call_id", default=None
)
# 当前 run（多会话并发时事件按 run 路由到对应页签）
CURRENT_RUN_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "eaide_builtin_run_id", default=None
)


def bind_exec_scope(call_id: str | None, run_id: str | None) -> contextvars.Token[str | None]:
    """绑定当前执行上下文；返回 Token 供 reset（dispatcher finally 还原）。"""
    t1 = CURRENT_CALL_ID.set(call_id or None)
    CURRENT_RUN_ID.set(run_id or None)
    return t1


def reset_exec_scope(token: contextvars.Token[str | None]) -> None:
    """还原上下文（幂等兜底：reset 失败静默）。"""
    try:
        CURRENT_CALL_ID.reset(token)
        CURRENT_RUN_ID.set(None)
    except (ValueError, LookupError):
        pass


def current_call_id() -> str | None:
    return CURRENT_CALL_ID.get()


def current_run_id() -> str | None:
    return CURRENT_RUN_ID.get()
