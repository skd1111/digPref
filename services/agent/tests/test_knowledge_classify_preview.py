# services/agent/tests/test_knowledge_classify_preview.py
"""导入前分类预判（/knowledge/v1/docs/classify/preview）单元测试。

覆盖：
- 总开关 / 自动分类开关关闭 → enabled=False 且给出原因
- 数据字典无「知识库分类」条目 → enabled=False
- 正常路径：读取文件前 2000 字 → LLM 判定 → 返回分类 key + 显示名
- LLM 返回无法识别的分类 → 该条带 error（不阻断其他文件）
- 读不到文件 → 该条带 error
"""

from __future__ import annotations

import pytest
from agent.config import settings
from agent.knowledge import api as kb_api
from agent.knowledge.api import (
    V1ClassifyPreviewItem,
    V1ClassifyPreviewRequest,
    v1_classify_preview,
)
from agent.knowledge.ingestion import KnowledgeIngestion

CATEGORIES = [
    {"key": "finance", "label": "财务/财税", "description": "报销预算税务"},
    {"key": "legal", "label": "法务/合同", "description": "合同条款法律"},
    {"key": "compliance", "label": "合规/制度", "description": "内控制度合规"},
]


@pytest.fixture
def preview_env(monkeypatch, tmp_path):
    """开启自动分类相关开关，注入 fake LLM 与字典分类。

    本文件专注测 LLM 预判路径：关掉嵌入相似度分类（开发机存在真实 bge ONNX
    模型时会优先命中嵌入路径，fake LLM 不再被调用）。
    """
    monkeypatch.setattr(settings, "rag_llm_enhance_enabled", True)
    monkeypatch.setattr(settings, "rag_auto_classify_enabled", True)
    monkeypatch.setattr(settings, "rag_embed_classify_enabled", False)

    import agent.knowledge.category as cat_mod

    monkeypatch.setattr(cat_mod, "get_kb_categories", lambda: list(CATEGORIES))

    ing = KnowledgeIngestion(storage=None, embedding=None)
    monkeypatch.setattr(kb_api, "_ingestion", ing)
    return ing


async def _fake_llm_factory(result: str):
    async def _llm(kind: str, prompt: str) -> str:
        assert kind == "kb_classify"
        assert "文档内容" in prompt
        return result

    return _llm


async def test_disabled_switch_returns_reason(monkeypatch):
    monkeypatch.setattr(settings, "rag_llm_enhance_enabled", False)
    monkeypatch.setattr(settings, "rag_auto_classify_enabled", True)
    res = await v1_classify_preview(V1ClassifyPreviewRequest(paths=["C:/x.md"]))
    assert res.enabled is False
    assert "大模型知识库验证" in res.reason


async def test_missing_dictionary_returns_reason(monkeypatch):
    monkeypatch.setattr(settings, "rag_llm_enhance_enabled", True)
    monkeypatch.setattr(settings, "rag_auto_classify_enabled", True)
    import agent.knowledge.category as cat_mod

    monkeypatch.setattr(cat_mod, "get_kb_categories", lambda: [])
    res = await v1_classify_preview(V1ClassifyPreviewRequest(paths=["C:/x.md"]))
    assert res.enabled is False
    assert "知识库分类" in res.reason


async def test_classifies_file_and_returns_category(preview_env, monkeypatch, tmp_path):
    f = tmp_path / "报销制度.md"
    f.write_text("本制度规定了差旅费报销标准与预算审批流程。", encoding="utf-8")
    preview_env._llm = await _fake_llm_factory("finance")

    res = await v1_classify_preview(V1ClassifyPreviewRequest(paths=[str(f)]))
    assert res.enabled is True
    assert len(res.items) == 1
    item = res.items[0]
    assert item.category == "finance"
    assert item.label == "财务/财税"
    assert item.source == "llm"
    assert item.error == ""


async def test_heuristic_fallback_when_llm_unavailable(preview_env, tmp_path):
    """LLM 不可用（限流/异常）时按文件名启发式兜底，不再让分类留空。"""

    async def _boom(kind: str, prompt: str) -> str:
        raise RuntimeError("LLM HTTP 429 rate limit")

    preview_env._llm = _boom
    f = tmp_path / "中华人民共和国公司法.md"
    f.write_text("公司法 全文", encoding="utf-8")

    res = await v1_classify_preview(V1ClassifyPreviewRequest(paths=[str(f)]))
    item = res.items[0]
    assert item.category == "legal"
    assert item.source == "heuristic"
    assert item.error == ""


async def test_unrecognized_llm_output_marks_error(preview_env, tmp_path):
    f = tmp_path / "未知.md"
    f.write_text("一些内容", encoding="utf-8")
    preview_env._llm = await _fake_llm_factory("胡说八道")

    res = await v1_classify_preview(V1ClassifyPreviewRequest(paths=[str(f)]))
    assert res.enabled is True
    assert res.items[0].category == ""
    assert "未能判定分类" in res.items[0].error


async def test_unreadable_file_marks_error(preview_env):
    res = await v1_classify_preview(V1ClassifyPreviewRequest(paths=["C:/不存在/文件.md"]))
    assert res.enabled is True
    assert res.items[0].category == ""
    assert res.items[0].error


async def test_label_mapping_uses_dictionary(preview_env, monkeypatch, tmp_path):
    """LLM 返回显示名时也能反向映射回分类 key。"""
    f = tmp_path / "合规.md"
    f.write_text("合规相关制度", encoding="utf-8")
    preview_env._llm = await _fake_llm_factory("合规/制度")

    res = await v1_classify_preview(V1ClassifyPreviewRequest(paths=[str(f)]))
    assert res.items[0].category == "compliance"
    assert res.items[0].label == "合规/制度"


async def test_many_files_are_processed_in_batches(preview_env, tmp_path):
    """条数多时端点内部分批处理（每批 10 个），结果不丢。"""
    preview_env._llm = await _fake_llm_factory("finance")
    files: list[str] = []
    for i in range(12):
        f = tmp_path / f"文件{i}.md"
        f.write_text("制度内容", encoding="utf-8")
        files.append(str(f))

    res = await v1_classify_preview(V1ClassifyPreviewRequest(paths=files))
    assert res.enabled is True
    assert len(res.items) == 12
    assert all(it.category == "finance" for it in res.items)


def test_response_model_defaults():
    item = V1ClassifyPreviewItem(path="C:/a.md")
    assert item.category == ""
    assert item.label == ""
    assert item.error == ""
    assert item.source == ""


def test_guess_category_heuristics():
    """启发式兜底：文件名关键词 → 分类 key。"""
    from agent.knowledge.category import guess_category

    assert guess_category("中华人民共和国增值税暂行条例.md") == "finance"
    assert guess_category("中华人民共和国海关法（2021）.md") == "legal"
    assert guess_category("互联网信息服务算法推荐管理规定.md") == "compliance"
    assert guess_category("个人信息保护相关制度.md") == "data_security"
    assert guess_category("某不知名文档.md") == ""
