# services/agent/tests/test_knowledge_llm_concurrency.py
"""知识库云端 LLM 调用闸门（rag_llm_concurrency）与入库僵尸自愈单元测试。

背景（BUGFIX #198「上传后一直在索引中」）：
- 429 的真实来源是「入库期每父块一次云端 LLM 调用」，而不是「文档数量」。早期把限流
  加在文档级（rag_ingest_concurrency=2）→ 批量导入退化成串行：单文档要等几十秒的云端
  往返，N 个文档排成 N/2 批 → 界面长期显示「索引中」。改为两级分工：
  文档级控 CPU/内存（解析 + ONNX 向量化），LLM 调用级控云端并发（避免 429）。
- 入库是后台任务：进程重启 / 崩溃后任务不恢复，但文档状态仍停在 indexing → 界面永久
  显示「索引中」，且无任何自愈路径。启动时需把残留记录置为 failed。

覆盖：
- 信号量按 rag_llm_concurrency 创建，limit 变化时重建
- 同时在飞的云端 LLM 调用数不超过上限（其余排队）
- 上下文前缀 / 实体抽取 等入库期调用确实走闸门
- 父块间并行：单文档不再串行等 N 次云端往返
- reset_orphaned_indexing_docs：indexing → failed，ready 不受影响
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from agent.config import settings
from agent.knowledge import ingestion, rag_config
from agent.knowledge.storage import KnowledgeStorage

# ---- 闸门本体 ----------------------------------------------------------------


def test_llm_gate_follows_config(monkeypatch):
    """信号量按 rag_llm_concurrency 创建；同 limit 复用、limit 变化重建。"""
    monkeypatch.setattr(settings, "rag_llm_concurrency", 3)
    gate = ingestion._get_llm_gate()
    assert gate is not None
    # 同一 limit → 复用同一信号量
    assert ingestion._get_llm_gate() is gate
    # limit 变化 → 重建
    monkeypatch.setattr(settings, "rag_llm_concurrency", 5)
    assert ingestion._get_llm_gate() is not gate


async def test_llm_gate_caps_inflight_calls(monkeypatch):
    """同时在飞的云端调用数不超过上限（其余排队）。"""
    monkeypatch.setattr(settings, "rag_llm_concurrency", 2)
    running = 0
    peak = 0

    async def fake_llm(task, prompt):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.05)
        running -= 1
        return "ok"

    await asyncio.gather(*(ingestion._call_llm(fake_llm, "t", "p") for _ in range(8)))
    assert peak <= 2, f"在飞 LLM 调用数超限：{peak}"
    assert peak >= 1


async def test_contextual_prefix_goes_through_gate(monkeypatch):
    """上下文前缀生成走闸门：并发调用被上限约束，且返回值正确回传。"""
    monkeypatch.setattr(settings, "rag_llm_concurrency", 1)
    monkeypatch.setattr(settings, "rag_llm_enhance_enabled", True)
    monkeypatch.setattr(settings, "rag_llm_contextual_enabled", True)
    running = 0
    peak = 0

    async def fake_llm(task, prompt):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.02)
        running -= 1
        return "  前缀  "

    ing = ingestion.KnowledgeIngestion.__new__(ingestion.KnowledgeIngestion)
    ing.storage = MagicMock()
    results = await asyncio.gather(
        *(ing._llm_contextual_prefix("正文", fake_llm) for _ in range(4))
    )
    assert peak <= 1, f"上下文前缀未受闸门约束：{peak}"
    assert all(r == "前缀" for r in results), results


async def test_parent_llm_calls_run_in_parallel(monkeypatch, tmp_path):
    """父块间并行：单文档的上下文前缀不再串行等 N 次云端往返。"""
    monkeypatch.setattr(settings, "rag_llm_concurrency", 8)
    monkeypatch.setattr(settings, "rag_llm_enhance_enabled", True)
    monkeypatch.setattr(settings, "rag_llm_contextual_enabled", True)
    monkeypatch.setattr(settings, "rag_entity_extract_enabled", True)
    running = 0
    peak = 0

    async def fake_prefix(text, llm):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.03)
        running -= 1
        return "前缀"

    async def fake_entities(text, llm):
        return []

    parents = [SimpleNamespace(ord=i, text=f"第{i}段正文", chunks=[], seq=i) for i in range(3)]

    src = tmp_path / "doc.md"
    src.write_text("# 标题\n\n正文内容", encoding="utf-8")

    ing = ingestion.KnowledgeIngestion.__new__(ingestion.KnowledgeIngestion)
    ing.storage = MagicMock()
    ing.storage.upsert_parents.return_value = {0: 1, 1: 2, 2: 3}
    ing.storage.upsert_entities.return_value = 1
    ing._chunk = MagicMock(return_value={"parents": parents, "children": []})
    ing._embed_and_store = AsyncMock()
    ing._llm_contextual_prefix = fake_prefix
    ing._extract_entities = fake_entities

    import agent.doc_review.parser as parser_mod

    monkeypatch.setattr(
        parser_mod,
        "parse_document",
        lambda _p: SimpleNamespace(full_text="正文内容", pages=[]),
    )

    await ing.ingest_file(str(src), doc_id="d1", copy=False)

    assert peak > 1, f"父块未并行处理（peak={peak}）：单文档仍在串行等云端往返"
    # 并行不改变结果集合：每个父块各拿到一个前缀
    assert ing._embed_and_store.await_count == 1


# ---- 入库僵尸自愈 ------------------------------------------------------------


def test_reset_orphaned_indexing_docs(tmp_path, monkeypatch):
    """残留 indexing / queued 文档置 failed；ready 文档不受影响。"""
    monkeypatch.setattr(settings, "rag_kb_dir", str(tmp_path / "knowledge"))
    kb = KnowledgeStorage(rag_config.kb_db_path())
    kb.insert_doc(
        doc_id="d1",
        title="中断的文档",
        file_name="a.md",
        source_type="md",
        status="indexing",
    )
    kb.insert_doc(
        doc_id="d2",
        title="排队中没排到的文档",
        file_name="c.md",
        source_type="md",
        status="queued",
    )
    kb.insert_doc(
        doc_id="d3",
        title="已就绪的文档",
        file_name="b.md",
        source_type="md",
        status="ready",
    )

    healed = kb.reset_orphaned_indexing_docs("上次索引被中断（Agent 重启或崩溃），请重新导入")
    assert healed == 2, healed
    assert kb.get_doc("d1")["status"] == "failed"
    assert "上次索引被中断" in (kb.get_doc("d1")["error"] or "")
    assert kb.get_doc("d2")["status"] == "failed"
    # 已就绪的文档不受影响
    assert kb.get_doc("d3")["status"] == "ready"


def test_reset_orphaned_indexing_docs_is_idempotent(tmp_path, monkeypatch):
    """重复调用不会再次命中（第二次影响 0 行），不产生噪音日志。"""
    monkeypatch.setattr(settings, "rag_kb_dir", str(tmp_path / "knowledge"))
    kb = KnowledgeStorage(rag_config.kb_db_path())
    kb.insert_doc(
        doc_id="d1",
        title="中断的文档",
        file_name="a.md",
        source_type="md",
        status="indexing",
    )
    assert kb.reset_orphaned_indexing_docs("reason") == 1
    assert kb.reset_orphaned_indexing_docs("reason") == 0
