# services/agent/tests/test_knowledge_embed_classify.py
"""知识库嵌入相似度分类（方案一：bge-small-zh 类中心余弦最近邻）单元测试。

覆盖：
- 命中最近类中心 → (key, "embed")，且不调 LLM / 不走启发式
- 低于置信度阈值 → 视为不可判 → 启发式兜底（LLM 关闭时）
- embedding 失败（零向量契约）→ 回落 LLM 路径（需增强总开关）
- 嵌入分类开关关闭 → 直接走 LLM 路径，embedding 不被触碰
- 类中心缓存：同分类列表复用（embed_batch 只调一次），列表变更即重嵌
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from agent.config import settings
from agent.knowledge.ingestion import KnowledgeIngestion

CATEGORIES = [
    {"key": "finance", "label": "财务/财税", "description": "报销预算税务会计"},
    {"key": "legal", "label": "法务/合同", "description": "合同法律诉讼条款"},
    {"key": "compliance", "label": "合规/制度", "description": "内控制度合规监管"},
]


def _unit(i: int, n: int) -> list[float]:
    """n 维单位基向量（互为正交 → 余弦判别清晰）。"""
    v = [0.0] * n
    v[i] = 1.0
    return v


class FakeEmbedding:
    """可控向量 fake：embed_batch 返回类中心、embed 返回文档向量。

    遵守 OnnxEmbeddingClient 契约：失败返零向量，调用方以 any() 判定。
    embed_batch 按 texts 数量循环扩展，便于验证「类列表变更 → 重嵌」。
    """

    def __init__(self, centers: list[list[float]], doc: list[float]) -> None:
        self.centers = centers
        self.doc = doc
        self.embed_batch_calls = 0
        self.embed_calls = 0
        self.last_text = ""  # 记录最近一次文档侧嵌入文本（验证截断长度用）

    async def embed(self, text: str) -> list[float]:
        self.embed_calls += 1
        self.last_text = text
        return list(self.doc)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.embed_batch_calls += 1
        return [list(self.centers[i % len(self.centers)]) for i in range(len(texts))]


def _make_ingestion(embedding: FakeEmbedding) -> KnowledgeIngestion:
    return KnowledgeIngestion(storage=MagicMock(), embedding=embedding)


def _fake_llm(result: str) -> Any:
    async def _llm(kind: str, prompt: str) -> str:
        assert kind == "kb_classify"
        return result

    return _llm


@pytest.fixture
def classify_env(monkeypatch):
    """开启嵌入分类 + 自动分类子开关，关闭 LLM 增强总开关，注入固定分类列表。"""
    monkeypatch.setattr(settings, "rag_embed_classify_enabled", True)
    monkeypatch.setattr(settings, "rag_auto_classify_enabled", True)
    monkeypatch.setattr(settings, "rag_llm_enhance_enabled", False)
    import agent.knowledge.category as cat_mod

    monkeypatch.setattr(cat_mod, "get_kb_categories", lambda: [dict(c) for c in CATEGORIES])


async def test_embed_classify_hits_nearest_center(classify_env):
    """文档向量与 legal 类中心最近且超阈值 → (legal, embed)，不调 LLM。"""
    emb = FakeEmbedding(
        centers=[_unit(0, 3), _unit(1, 3), _unit(2, 3)],
        doc=[0.05, 0.995, 0.0],
    )
    ing = _make_ingestion(emb)
    ing._llm = _fake_llm("finance")  # 若被调用会返回 finance，断言 embed 优先
    cat, source = await ing.classify_with_fallback(
        "本合同适用于中华人民共和国法律，争议提交诉讼解决。", "合同", "合同范本.docx"
    )
    assert (cat, source) == ("legal", "embed")
    assert emb.embed_batch_calls == 1  # 类中心嵌入一次
    assert emb.embed_calls == 1


async def test_below_threshold_falls_back_to_heuristic(classify_env, monkeypatch):
    """最高余弦低于阈值（热配置生效）→ 视为不可判 → 启发式兜底（LLM 关闭不调）。"""
    monkeypatch.setattr(settings, "rag_embed_classify_threshold", 0.99)
    emb = FakeEmbedding(
        centers=[_unit(0, 3), _unit(1, 3), _unit(2, 3)],
        doc=[0.1, 0.9, 0.0],  # 与 legal 余弦 ≈ 0.9 < 0.99 阈值
    )
    ing = _make_ingestion(emb)
    ing._llm = _fake_llm("finance")
    cat, source = await ing.classify_with_fallback("内容", "标题", "中华人民共和国民法典.docx")
    assert (cat, source) == ("legal", "heuristic")  # 文件名以「法典」启发式命中


async def test_zero_vector_contract_falls_back_to_llm(classify_env, monkeypatch):
    """类中心嵌入返回零向量（模型不可用契约）→ 回落 LLM（需增强总开关开启）。"""
    monkeypatch.setattr(settings, "rag_llm_enhance_enabled", True)
    emb = FakeEmbedding(
        centers=[[0.0, 0.0, 0.0]] * 3,  # 类中心全零 → 判嵌入不完整 → 跳过
        doc=[0.1, 0.2, 0.3],
    )
    ing = _make_ingestion(emb)
    ing._llm = _fake_llm("finance")
    cat, source = await ing.classify_with_fallback("内容", "标题", "file.txt")
    assert (cat, source) == ("finance", "llm")


async def test_disabled_embed_uses_llm_directly(classify_env, monkeypatch):
    """嵌入分类开关关闭 → 直接走 LLM，embedding 全程不被触碰。"""
    monkeypatch.setattr(settings, "rag_embed_classify_enabled", False)
    monkeypatch.setattr(settings, "rag_llm_enhance_enabled", True)
    emb = FakeEmbedding(centers=[_unit(0, 3)] * 3, doc=[1.0, 0.0, 0.0])
    ing = _make_ingestion(emb)
    ing._llm = _fake_llm("compliance")
    cat, source = await ing.classify_with_fallback("内容", "标题", "file.txt")
    assert (cat, source) == ("compliance", "llm")
    assert emb.embed_batch_calls == 0 and emb.embed_calls == 0


async def test_classify_text_chars_truncates_head(classify_env, monkeypatch):
    """rag_classify_text_chars 热生效：文档侧嵌入文本按配置截断正文。"""
    monkeypatch.setattr(settings, "rag_classify_text_chars", 10)
    emb = FakeEmbedding(
        centers=[_unit(0, 3), _unit(1, 3), _unit(2, 3)],
        doc=[0.05, 0.995, 0.0],
    )
    ing = _make_ingestion(emb)
    body = "正" * 100
    await ing.classify_with_fallback(body, "标题", "合同.docx")
    # 文档侧文本 = 标题 + 文件名 + 正文前 10 字；正文第 11 字起被截断
    assert emb.last_text.count("正") == 10
    assert emb.last_text.startswith("标题\n合同.docx\n")


async def test_centers_cache_reused_until_categories_change(classify_env, monkeypatch):
    """同分类列表复用类中心缓存（embed_batch 一次）；列表变更后指纹失效重嵌。"""
    emb = FakeEmbedding(
        centers=[_unit(0, 3), _unit(1, 3), _unit(2, 3)],
        doc=[0.05, 0.995, 0.0],
    )
    ing = _make_ingestion(emb)
    for _ in range(2):
        cat, source = await ing.classify_with_fallback("内容", "标题", "合同.docx")
        assert (cat, source) == ("legal", "embed")
    assert emb.embed_batch_calls == 1  # 缓存复用

    import agent.knowledge.category as cat_mod

    monkeypatch.setattr(
        cat_mod,
        "get_kb_categories",
        lambda: (
            [dict(c) for c in CATEGORIES]
            + [{"key": "hr", "label": "人事", "description": "劳动合同考勤"}]
        ),
    )
    cat, source = await ing.classify_with_fallback("内容", "标题", "合同.docx")
    assert emb.embed_batch_calls == 2  # 指纹变更 → 重嵌
    assert (cat, source) == ("legal", "embed")  # 仍命中最近中心
