"""多会话并发：模型选择的 run 级作用域隔离（2026-08-26）。

真实场景：两个会话并发跑，A 选了模型 m3、B 用默认——旧实现经 LMRouter 实例
字段互踩（后发的请求改掉前者正在执行的回答链模型）。修复：chat_stream 在
各自事件生成器任务里调 bind_run_scope 写 ContextVar，属性读取 run 作用域
优先，未设值回落实例字段（单跑旧语义不变）。
"""

from __future__ import annotations

import asyncio

from agent.llm.router import LMRouter, bind_run_scope


def test_run_scope_overrides_instance_fields():
    router = LMRouter()
    router.set_chat_model_override("m1")
    router.set_inference_mode("normal")

    async def scoped():
        bind_run_scope("m3", "performance")
        return router.chat_model_override, router.inference_mode

    # 作用域内：取本 run 的设定
    override, mode = asyncio.run(scoped())
    assert override == "m3"
    assert mode == "performance"
    # 作用域外：回落实例字段（单跑/旧语义）
    assert router.chat_model_override == "m1"
    assert router.inference_mode == "normal"


def test_two_concurrent_runs_keep_own_scope():
    """并发两个 run 各用各的模型，互不串扰。"""
    router = LMRouter()

    async def run_a():
        bind_run_scope("m3", "normal")
        await asyncio.sleep(0.02)  # 让 run_b 先设自己的作用域
        return router.chat_model_override

    async def run_b():
        bind_run_scope("m5", "performance")
        await asyncio.sleep(0.01)
        return router.chat_model_override

    async def both():
        return await asyncio.gather(run_a(), run_b())

    a, b = asyncio.run(both())
    assert a == "m3"
    assert b == "m5"


def test_scope_none_means_clear_for_this_run():
    """run 内显式不选模型（None）= 本 run 走默认链，不吃别的 run 写入的实例值。"""
    router = LMRouter()
    router.set_chat_model_override("m1")

    async def scoped():
        bind_run_scope(None, "normal")
        return router.chat_model_override

    assert asyncio.run(scoped()) is None
    assert router.chat_model_override == "m1"
