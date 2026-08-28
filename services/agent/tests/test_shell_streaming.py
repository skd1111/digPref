"""执行过程可视化（阶段三） · shell 流式输出测试。

验证：
1. 有 call_id 上下文时，执行期间逐批 emit shell_chunk（结束帧带 exit_code）；
2. 无上下文时保持原路径（不发细粒度事件），行为不变；
3. tool_result 语义不变（ok = 退出码 0，BUGFIX #165 口径）。
"""

from __future__ import annotations

from agent.builtin.events import EVT_SHELL_CHUNK, consume_builtin_events, flush_builtin_events
from agent.builtin.exec_context import bind_exec_scope, reset_exec_scope
from agent.builtin.shell import builtin_shell


async def test_streaming_emits_chunks_with_exit_frame() -> None:
    await flush_builtin_events()
    token = bind_exec_scope("call_stream_test", "run_stream_test")
    try:
        result = await builtin_shell("echo hello", timeout_sec=30)
    finally:
        reset_exec_scope(token)
    assert result.ok is True

    events = await consume_builtin_events()
    chunks = [p for kind, p in events if kind == EVT_SHELL_CHUNK]
    assert chunks, "应当至少有一条 shell_chunk"
    # 全部带同一 call_id（前端按它归并到工具卡）
    assert all(c["call_id"] == "call_stream_test" for c in chunks)
    # 结束帧：最后一帧带 exit_code=0
    assert chunks[-1]["exit_code"] == 0
    # 正文帧里能看到命令输出
    body = "".join(c["chunk"] for c in chunks if c["chunk"])
    assert "hello" in body


async def test_no_context_keeps_legacy_path() -> None:
    await flush_builtin_events()
    result = await builtin_shell("echo legacy", timeout_sec=30)
    assert result.ok is True
    events = await consume_builtin_events()
    assert not [p for kind, p in events if kind == EVT_SHELL_CHUNK], (
        "无 call_id 上下文时不应发细粒度事件（单测/直调保持原路径）"
    )


async def test_streaming_failure_semantics_match_legacy() -> None:
    """非零退出仍算失败（BUGFIX #165 口径），且结束帧带真实退出码。"""
    await flush_builtin_events()
    token = bind_exec_scope("call_fail_test", None)
    try:
        # exit 3 在白名单外不拦（首 token 无限制时放行），命令本身非零退出
        result = await builtin_shell("exit 3", timeout_sec=30)
    finally:
        reset_exec_scope(token)
    assert result.ok is False
    assert "exit_code=3" in (result.error or "")

    events = await consume_builtin_events()
    chunks = [p for kind, p in events if kind == EVT_SHELL_CHUNK]
    if chunks:  # pwsh/cmd 均支持 exit；结束帧退出码必须真实
        assert chunks[-1]["exit_code"] == 3


async def test_ui_summary_on_success() -> None:
    result = await builtin_shell("echo ui-summary", timeout_sec=30)
    assert result.ok is True
    assert result.ui is not None
    assert "退出码 0" in str(result.ui.get("summary"))
