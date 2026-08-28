"""BUGFIX #157 —— 模型后端瞬时故障（ReadError/断连）重试后再判死（2026-08-26）。

真实翻车（日志 2a2f42f8）：PPT 工作流执行了 20+ 步 shell 后，模型后端一次
httpx.ReadError 直接触发「工具编排失败（ReadError），已停止尝试」，整轮成果报废。
修复：chat_with_tools 瞬时故障重试 3 次（指数退避 0.5s/1.0s）；业务错误不重试；
重试耗尽后文案告知已保留成果、可发「继续」接着做。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from agent.tools.loop import DynamicToolLoop, _is_transient_backend_error


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _FlakyBackend:
    """前 fails 次调用抛指定异常，之后按脚本返回。"""

    def __init__(self, fails, scripted, exc_factory):
        self.fails = fails
        self._scripted = list(scripted)
        self._exc_factory = exc_factory
        self.calls = 0
        self.request_messages: list[list[dict]] = []

    async def chat_with_tools(self, messages, tools, **kwargs):
        self.calls += 1
        if self.calls <= self.fails:
            raise self._exc_factory()
        self.request_messages.append([dict(m) for m in messages])
        return self._scripted.pop(0)


class _StubLLM:
    def __init__(self, backend):
        self._backend = backend

    async def resolve_native_backend(self):
        return ("private", self._backend)


class _StubCatalog:
    async def definitions(self, names=None):
        return []

    async def summaries(self):
        return []


def _state(**extra):
    base = {
        "tool_calling_mode": "native",
        "user_prompt": "做一个ppt",
        "messages": [],
        "tool_results": [],
        "tool_turn_count": 0,
    }
    base.update(extra)
    return base


def test_transient_error_classification():
    assert _is_transient_backend_error(httpx.ReadError("peer closed"))
    assert _is_transient_backend_error(httpx.ConnectError("refused"))
    assert _is_transient_backend_error(httpx.ReadTimeout("slow"))
    assert _is_transient_backend_error(ConnectionResetError("reset"))
    # 业务错误不重试
    assert not _is_transient_backend_error(ValueError("bad param"))
    assert not _is_transient_backend_error(KeyError("missing_field"))


def test_read_error_retried_then_recovers():
    """一次 ReadError 后重试成功 → 任务照常完成，不再整轮报废。"""
    backend = _FlakyBackend(
        fails=1,
        scripted=[{"content": "完成。", "tool_calls": []}],
        exc_factory=lambda: httpx.ReadError("peer closed connection"),
    )
    loop = DynamicToolLoop(_StubLLM(backend), _StubCatalog())
    out = _run(loop.run(_state()))
    assert out["final_answer"] == "完成。"
    assert backend.calls == 2  # 首次失败 + 重试成功


def test_business_error_not_retried_falls_back_prompt_protocol():
    """非瞬时错误不重试；未执行过工具 → _run_native 返 None（回退提示词协议信号）。"""
    backend = _FlakyBackend(
        fails=99,
        scripted=[],
        exc_factory=lambda: ValueError("bad param"),
    )
    loop = DynamicToolLoop(_StubLLM(backend), _StubCatalog())
    out = _run(loop._run_native(_state()))
    assert out is None
    assert backend.calls == 1  # 只试一次


def test_retries_exhausted_keeps_partial_results_with_actionable_message():
    """重试耗尽且已有工具成果 → 终答告知成果已保留、可发「继续」，不再说「已停止尝试」。"""
    backend = _FlakyBackend(
        fails=99,
        scripted=[],
        exc_factory=lambda: httpx.ReadError("peer closed connection"),
    )
    loop = DynamicToolLoop(_StubLLM(backend), _StubCatalog())
    state = _state(tool_results=[{"name": "shell", "ok": True}, {"name": "write_file", "ok": True}])
    out = _run(loop.run(state))
    assert backend.calls == 3  # 重试上限 3 次
    final = str(out["final_answer"])
    assert "ReadError" in final
    assert "2 步结果已保留" in final
    assert "继续" in final


def test_retry_respects_max_attempts(monkeypatch):
    """重试次数受 _NATIVE_BACKEND_RETRIES 约束（防慢后端上无限重试）。"""
    from agent.tools import loop as loop_mod

    monkeypatch.setattr(loop_mod, "_NATIVE_BACKEND_RETRIES", 2)
    backend = _FlakyBackend(
        fails=99,
        scripted=[],
        exc_factory=lambda: httpx.ReadError("x"),
    )
    loop = DynamicToolLoop(_StubLLM(backend), _StubCatalog())
    out = _run(loop.run(_state(tool_results=[{"name": "shell", "ok": True}])))
    assert backend.calls == 2
    assert out["final_answer"] is not None


@pytest.mark.parametrize("exc", [httpx.ReadError("x")])
def test_parametrize_smoke(exc):
    assert _is_transient_backend_error(exc)
