"""Phase 4 V0: Local AI client tests (local_small / local_vision / embedding)."""
from __future__ import annotations

import pytest


# ---- local_small tests ----------------------------------------------------

class TestLocalSmallClient:
    def test_import(self):
        from agent.llm.local_small import LocalSmallLLMClient
        c = LocalSmallLLMClient(base_url="http://127.0.0.1:8081/v1", model="test")
        assert c.base_url == "http://127.0.0.1:8081/v1"
        assert c.model == "test"

    async def test_classify_intent_empty_text(self):
        from agent.llm.local_small import LocalSmallLLMClient
        c = LocalSmallLLMClient(base_url="http://127.0.0.1:8081/v1")
        result = await c.classify_intent("")
        assert result == "query"

    async def test_classify_intent_fallback_on_connection_error(self, monkeypatch):
        """local_small 不可达时回退到 query（安全默认）。"""
        from agent.llm.local_small import LocalSmallLLMClient
        c = LocalSmallLLMClient(base_url="http://127.0.0.1:19999/v1")
        result = await c.classify_intent("查询订单")
        assert result == "query"  # 安全兜底

    async def test_plan_fallback_on_error(self):
        """local_small 不可达时 plan 返回空计划。"""
        from agent.llm.local_small import LocalSmallLLMClient
        c = LocalSmallLLMClient(base_url="http://127.0.0.1:19999/v1")
        steps, explanation = await c.plan(
            intent="query",
            user_prompt="test",
            history=[],
            tool_specs=[],
        )
        assert steps == []
        assert explanation == ""

    async def test_repair_raises_unavailable(self):
        """端侧不做 repair，抛异常让 fallback 链切下一级。"""
        from agent.llm.local_small import LocalSmallLLMClient, LocalSmallUnavailableError
        c = LocalSmallLLMClient(base_url="http://127.0.0.1:8081/v1")
        with pytest.raises(LocalSmallUnavailableError):
            await c.repair_call(original={}, error="test", history=[])

    async def test_summarise_raises_unavailable(self):
        """端侧不做 summarise，抛异常让 fallback 链切下一级。"""
        from agent.llm.local_small import LocalSmallLLMClient, LocalSmallUnavailableError
        c = LocalSmallLLMClient(base_url="http://127.0.0.1:8081/v1")
        with pytest.raises(LocalSmallUnavailableError):
            await c.summarise(intent="query", user_prompt="test", plan=[], results=[])


# ---- local_vision tests ---------------------------------------------------

class TestLocalVisionClient:
    def test_import(self):
        from agent.llm.local_vision import LocalVisionClient
        c = LocalVisionClient(base_url="http://127.0.0.1:8082/v1", model="moondream2")
        assert c.base_url == "http://127.0.0.1:8082/v1"

    async def test_understand_screenshot_fallback(self):
        """视觉模型不可达时返回空字符串。"""
        from agent.llm.local_vision import LocalVisionClient
        c = LocalVisionClient(base_url="http://127.0.0.1:19998/v1")
        result = await c.understand_screenshot(b"fake-image-bytes")
        assert result == ""

    async def test_extract_text_fallback(self):
        """视觉模型不可达时返回空字符串。"""
        from agent.llm.local_vision import LocalVisionClient
        c = LocalVisionClient(base_url="http://127.0.0.1:19998/v1")
        result = await c.extract_text_from_image(b"fake-image-bytes")
        assert result == ""

    async def test_health_check_returns_false(self):
        from agent.llm.local_vision import LocalVisionClient
        c = LocalVisionClient(base_url="http://127.0.0.1:19998/v1")
        result = await c.health_check()
        assert result is False


# ---- local_embedding tests ------------------------------------------------

class TestLocalEmbeddingClient:
    def test_import(self):
        from agent.llm.embedding import LocalEmbeddingClient
        c = LocalEmbeddingClient(base_url="http://127.0.0.1:8083/v1")
        assert c.dimensions == 384

    async def test_embed_fallback(self):
        """embedding 模型不可达时返回零向量。"""
        from agent.llm.embedding import LocalEmbeddingClient
        c = LocalEmbeddingClient(base_url="http://127.0.0.1:19997/v1")
        result = await c.embed("test")
        assert len(result) == 384
        assert all(v == 0.0 for v in result)

    async def test_embed_batch_fallback(self):
        from agent.llm.embedding import LocalEmbeddingClient
        c = LocalEmbeddingClient(base_url="http://127.0.0.1:19997/v1")
        results = await c.embed_batch(["a", "b", "c"])
        assert len(results) == 3
        for r in results:
            assert len(r) == 384
            assert all(v == 0.0 for v in r)

    async def test_health_check_returns_false(self):
        from agent.llm.embedding import LocalEmbeddingClient
        c = LocalEmbeddingClient(base_url="http://127.0.0.1:19997/v1")
        result = await c.health_check()
        assert result is False
