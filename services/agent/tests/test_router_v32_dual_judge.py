"""test_router_v32_dual_judge —— Phase 2C V3-2 dual_judge 测试。

覆盖：
- 正常 2-candidate 并发 → 裁判选最优
- 单 candidate 全过 → 直接返回（无需裁判）
- 全失败 → RuntimeError
- _LOCAL_ONLY_TASKS 红线（raise ValueError）
- judge_backend=None → 用本地规则（pick longest）
- judge_backend 不在 callers → 兜底本地规则
- judge 输出格式异常 → 兜底选第一个
- _build_judge_prompt 安全替换（不误吞 user_prompt 里的 {xxx}）
"""
from __future__ import annotations

import asyncio

import pytest

from agent.llm.dual_judge import (
    DualJudgeResult,
    _build_judge_prompt,
    _parse_judge_output,
    run_dual_with_judge,
)
from agent.llm.router import _LOCAL_ONLY_TASKS


# ---- helpers --------------------------------------------------------------

async def _ok(text: str, latency: float = 0.0):
    async def _caller(kind: str, prompt: str) -> str:
        if latency:
            await asyncio.sleep(latency)
        return text
    return _caller


async def _err(msg: str):
    async def _caller(kind: str, prompt: str):
        raise RuntimeError(msg)
    return _caller


# ---- 基础路径 --------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_candidates_with_judge_picks_winner():
    a = await _ok("A 答案")
    b = await _ok("B 答案")

    async def judge(kind, prompt):
        return "WINNER: 1\nB 更准确"

    async def judge(kind, prompt):
        return "WINNER: 1\nB 更准确"

    result = await run_dual_with_judge(
        user_prompt="问题",
        backend_callers={"ollama": a, "private": b},
        candidates=["ollama", "private"],
        judge_caller=judge,
        judge_backend_name="ollama",
        task_kind="summarise",
    )
    assert isinstance(result, DualJudgeResult)
    assert result.winner_text == "B 答案"
    assert result.trace.winner_index == 1
    assert result.trace.judge_backend == "ollama"
    assert "B 更准确" in result.trace.judge_reason


@pytest.mark.asyncio
async def test_single_candidate_returns_directly_no_judge():
    """1 个 candidate 时直接返回（无需裁判）。"""
    a = await _ok("Only A")

    async def judge_should_not_be_called(kind, prompt):
        raise AssertionError("judge should not be called for 1 candidate")

    result = await run_dual_with_judge(
        user_prompt="x",
        backend_callers={"ollama": a},
        candidates=["ollama"],
        judge_caller=judge_should_not_be_called,
        judge_backend_name="none",
        task_kind="summarise",
    )
    assert result.winner_text == "Only A"
    assert result.trace.judge_backend == "none"


# ---- 失败路径 --------------------------------------------------------------

@pytest.mark.asyncio
async def test_all_candidates_fail_raises():
    """所有 candidate 都失败 → RuntimeError。"""
    a = await _err("a-fail")
    b = await _err("b-fail")
    with pytest.raises(RuntimeError, match="all.*candidates failed"):
        await run_dual_with_judge(
            user_prompt="x",
            backend_callers={"ollama": a, "private": b},
            candidates=["ollama", "private"],
            task_kind="summarise",
        )


@pytest.mark.asyncio
async def test_partial_failure_only_successful_compete():
    """1 个失败 1 个成功 → 成功者直接胜出（无需裁判）。"""
    a = await _ok("Winner A")
    b = await _err("b down")

    result = await run_dual_with_judge(
        user_prompt="x",
        backend_callers={"ollama": a, "private": b},
        candidates=["ollama", "private"],
        task_kind="summarise",
    )
    assert result.winner_text == "Winner A"
    assert result.trace.judge_backend == "none"  # 单 success 不调 judge


# ---- 红线 -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_local_only_task_forbidden():
    """_LOCAL_ONLY_TASKS 任务（intent / repair / biznav_extract）禁止双模型并行。"""
    a = await _ok("a")
    b = await _ok("b")
    for k in _LOCAL_ONLY_TASKS:
        with pytest.raises(ValueError, match="_LOCAL_ONLY_TASKS"):
            await run_dual_with_judge(
                user_prompt="x",
                backend_callers={"ollama": a, "private": b},
                candidates=["ollama", "private"],
                task_kind=k,
            )


@pytest.mark.asyncio
async def test_missing_caller_raises():
    """caller map 缺候选 → ValueError。"""
    a = await _ok("a")
    with pytest.raises(ValueError, match="missing backend_callers"):
        await run_dual_with_judge(
            user_prompt="x",
            backend_callers={"ollama": a},
            candidates=["ollama", "private"],  # private 不在 callers
            task_kind="summarise",
        )


@pytest.mark.asyncio
async def test_empty_candidate_list_raises():
    """candidates 空 → ValueError。"""
    a = await _ok("a")
    with pytest.raises(ValueError, match="at least 1"):
        await run_dual_with_judge(
            user_prompt="x",
            backend_callers={"ollama": a},
            candidates=[],
            task_kind="summarise",
        )


# ---- judge 兜底路径 -------------------------------------------------------

@pytest.mark.asyncio
async def test_judge_caller_none_falls_back_to_local_rule():
    """judge_caller=None → 用本地规则（pick longest text）。"""
    a = await _ok("short")  # len=5
    b = await _ok("this is a much longer response than the other")  # len=44

    result = await run_dual_with_judge(
        user_prompt="x",
        backend_callers={"ollama": a, "private": b},
        candidates=["ollama", "private"],
        judge_caller=None,
        task_kind="summarise",
    )
    # b 文本更长 → 胜出
    assert "longer" in result.winner_text
    assert "longest text" in result.trace.judge_reason


@pytest.mark.asyncio
async def test_judge_caller_unavailable_falls_back_to_local():
    """judge_caller 是 None 类型兜底本地规则（之前是 judge_backend missing；现在统一 judge_caller=None）。"""
    a = await _ok("AAA" * 10)  # 30 chars
    b = await _ok("BB")  # 2 chars

    result = await run_dual_with_judge(
        user_prompt="x",
        backend_callers={"ollama": a, "private": b},
        candidates=["ollama", "private"],
        judge_caller=None,
        task_kind="summarise",
    )
    assert "AAA" in result.winner_text
    assert "longest text" in result.trace.judge_reason


@pytest.mark.asyncio
async def test_judge_returns_garbage_falls_back_to_first():
    """裁判输出不可解析 → 兜底选第一个 successful。"""
    a = await _ok("A wins by default")
    b = await _ok("B should lose")

    async def judge_garbage(kind, prompt):
        return "I think the answer is unclear and depends on context."

    result = await run_dual_with_judge(
        user_prompt="x",
        backend_callers={"ollama": a, "private": b},
        candidates=["ollama", "private"],
        judge_caller=judge_garbage,
        judge_backend_name="ollama",
        task_kind="summarise",
    )
    assert result.winner_text == "A wins by default"
    assert result.trace.winner_index == 0


# ---- _parse_judge_output ---------------------------------------------------

def test_parse_judge_output_winner_format():
    assert _parse_judge_output("WINNER: 1", 3) == (1, "")
    assert _parse_judge_output("winner = 2", 3) == (2, "")
    assert _parse_judge_output("WINNER: 5\nout of range", 3) == (2, "")  # 截断到 n-1
    assert _parse_judge_output("WINNER: -1", 3) == (0, "")  # 截断到 0


def test_parse_judge_output_with_reason():
    idx, reason = _parse_judge_output("WINNER: 0\nA 更准确\n事实正确", 2)
    assert idx == 0
    assert "A 更准确" in reason
    assert "事实正确" in reason


def test_parse_judge_output_no_winner_returns_default():
    """无 WINNER 标记 → 默认 0。"""
    idx, reason = _parse_judge_output("No clear winner", 3)
    assert idx == 0
    assert reason == ""


# ---- _build_judge_prompt ---------------------------------------------------

def test_build_judge_prompt_does_not_swallow_user_braces():
    """用户 prompt 含 {xxx} 不应被 .format 误吞。"""
    p = _build_judge_prompt(
        "用户：{user_prompt}\n候选：{candidates}\n数量：{len_candidates}",
        user_prompt="订单系统 {pg} 配置",
        candidate_texts=["候选 A", "候选 B"],
    )
    assert "{user_prompt}" not in p
    assert "{len_candidates}" not in p
    assert "{candidates}" not in p
    assert "订单系统 {pg} 配置" in p  # 用户原文保留
    assert "[0] 候选 A" in p
    assert "[1] 候选 B" in p
    assert "数量：2" in p


def test_build_judge_prompt_partial_replacement():
    """多个 {user_prompt} 出现也全部替换。"""
    p = _build_judge_prompt(
        "first {user_prompt} second {user_prompt}",
        user_prompt="X",
        candidate_texts=["A"],
    )
    assert p == "first X second X"


# ---- latency 计时 ---------------------------------------------------------

@pytest.mark.asyncio
async def test_latency_recorded_per_candidate():
    async def slow(kind, prompt):
        await asyncio.sleep(0.05)
        return "slow but ok"

    async def fast(kind, prompt):
        return "fast"

    result = await run_dual_with_judge(
        user_prompt="x",
        backend_callers={"ollama": slow, "private": fast},
        candidates=["ollama", "private"],
        judge_caller=None,  # latency 测试无需 judge
        task_kind="summarise",
    )
    latencies = {c.backend_name: c.latency_ms for c in result.trace.candidates}
    assert latencies["ollama"] >= 40  # 至少 40ms
    assert latencies["private"] >= 0


# ---- judge 失败兜底 -------------------------------------------------------

@pytest.mark.asyncio
async def test_judge_caller_raises_falls_back_to_first():
    a = await _ok("First wins")
    b = await _ok("Second loses")

    async def judge_crashes(kind, prompt):
        raise RuntimeError("judge LLM down")

    # judge_backend='ollama' 但 judge 走同一个 caller —— 我们要分开 judge backend
    # 重新设计：把 judge_backend 改成 'private'（也是 caller）
    async def private_judge(kind, prompt):
        raise RuntimeError("judge private crash")

    result = await run_dual_with_judge(
        user_prompt="x",
        backend_callers={"ollama": a, "private": b},
        candidates=["ollama", "private"],
        judge_caller=private_judge,
        judge_backend_name="private",  # 用 private 做 judge，但它会 crash
        task_kind="summarise",
    )
    assert result.winner_text == "First wins"
    assert result.trace.winner_index == 0
    assert "judge_caller failed" in result.trace.judge_reason