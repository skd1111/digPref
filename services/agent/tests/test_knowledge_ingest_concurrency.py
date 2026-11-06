# services/agent/tests/test_knowledge_ingest_concurrency.py
"""知识库入库并发闸门（rag_ingest_concurrency）单元测试。

背景：批量导入时每个文档一个后台入库任务，全部并发会瞬间打爆云端模型
（429 速率限制）→ 分类/实体抽取/上下文前缀成片失败。用信号量限流。

覆盖：
- 信号量按 rag_ingest_concurrency 创建，limit 变化时重建
- 并发入库的实际在跑数不超过上限（其余排队）
"""

from __future__ import annotations

import asyncio

from agent.config import settings
from agent.knowledge import api as kb_api


def test_semaphore_follows_config(monkeypatch):
    monkeypatch.setattr(settings, "rag_ingest_concurrency", 3)
    sem = kb_api._get_ingest_semaphore()
    assert sem is not None
    # 同一 limit → 复用同一信号量
    assert kb_api._get_ingest_semaphore() is sem
    # limit 变化 → 重建
    monkeypatch.setattr(settings, "rag_ingest_concurrency", 5)
    assert kb_api._get_ingest_semaphore() is not sem


async def test_ingest_concurrency_is_capped(monkeypatch):
    monkeypatch.setattr(settings, "rag_ingest_concurrency", 2)
    running = 0
    peak = 0

    class FakeIngestion:
        async def ingest_file(self, *args, **kwargs):
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.05)
            running -= 1

    monkeypatch.setattr(kb_api, "_ingestion", FakeIngestion())
    await asyncio.gather(
        *(kb_api._run_ingest(f"/tmp/f{i}.md", f"d{i}", "", None) for i in range(6))
    )
    assert peak <= 2, f"并发入库数超限：{peak}"
    assert peak >= 1
