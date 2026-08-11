"""HITL 中断原语测试 —— 非阻塞版（Codex 风格）。

背景：旧的阻塞式 await_approval() 已被重构为
    start_approval() + check_decision() + post_decision() + cleanup_approval()
（见 interrupt.py 模块 docstring）。本文件覆盖新 API。

时序纪律：所有起了后台轮询任务的用例都 await 到任务自然结束
（找到决策即返回 / 超时即返回），不留悬挂 task 跨测试污染事件循环。
"""

from __future__ import annotations

import asyncio

import pytest
from agent.graph.interrupt import (
    _LOCAL_DECISIONS,
    check_decision,
    cleanup_approval,
    post_decision,
    start_approval,
)


@pytest.fixture(autouse=True)
def _clear():
    _LOCAL_DECISIONS.clear()
    yield
    _LOCAL_DECISIONS.clear()


class TestPostAndCheck:
    async def test_approve_visible(self):
        await post_decision("appr_ok", "approve")
        assert await check_decision("appr_ok") == "approve"

    async def test_reject_visible(self):
        await post_decision("appr_rej", "reject")
        assert await check_decision("appr_rej") == "reject"

    async def test_check_before_decision_is_none(self):
        assert await check_decision("never_posted") is None

    async def test_check_consumes_once(self):
        """内存 fallback 下 check_decision 取一次即消费（下次为 None）。"""
        await post_decision("once", "approve")
        assert await check_decision("once") == "approve"
        assert await check_decision("once") is None


class TestStartApproval:
    async def test_start_then_decision_picked_up(self):
        await start_approval(approval_id="s1", plan={"server": "db"}, timeout_sec=2)
        await post_decision("s1", "approve")
        # 让后台轮询跑一轮（poll_interval=0.25s）找到决策后自然退出
        await asyncio.sleep(0.3)
        assert await check_decision("s1") == "approve"

    async def test_timeout_auto_rejects(self):
        await start_approval(approval_id="s_to", plan={"server": "db"}, timeout_sec=0.3)
        # 超时后后台任务写入 reject 并退出
        await asyncio.sleep(0.6)
        assert await check_decision("s_to") == "reject"


class TestPostDecisionValidation:
    async def test_invalid_decision_raises(self):
        with pytest.raises(ValueError):
            await post_decision("appr_bad", "maybe")


class TestCleanup:
    async def test_cleanup_noop_without_redis(self):
        """无 Redis 时 cleanup 是 no-op，不得抛错。"""
        await cleanup_approval("whatever")
