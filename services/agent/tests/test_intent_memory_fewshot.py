"""动态 Few-Shot 案例库与闭环反馈测试（2026-08-31）。"""

from __future__ import annotations

import pytest
from agent.graph import intent_memory as im

# ---- 确定性伪向量化（避免真实模型）------------------------------------------------


def _fake_embed_factory(mapping: dict[str, list[float]]):
    async def _fake_embed(text: str):
        for key, vec in mapping.items():
            if key in text:
                return list(vec)
        return [-1.0, 0.0]

    return _fake_embed


@pytest.fixture
def fake_embed(monkeypatch):
    mapping = {
        "订单": [0.0, 1.0],
        "对账": [1.0, 0.0],
    }
    monkeypatch.setattr(im, "_embed_text", _fake_embed_factory(mapping))


# ---- 成功路由案例 + 检索 -----------------------------------------------------------


class TestExamples:
    async def test_record_and_retrieve_top_k(self, fake_embed):
        await im.record_example("r1", "查询订单表昨日数据", "data_query", ["target_table"])
        await im.record_example("r2", "帮我做财务对账", "task_execution", ["data_source"])
        await im.record_example("r3", "订单表有多少条记录", "data_query", [])

        hits = await im.retrieve_examples("统计订单表行数", top_k=2)
        assert len(hits) == 2
        # 与「订单」向量一致的两条排在前面
        assert {h["query_text"] for h in hits} == {"查询订单表昨日数据", "订单表有多少条记录"}
        assert all(h["intent_category"] == "data_query" for h in hits)

    async def test_negative_excluded_positive_boosted(self, fake_embed):
        await im.record_example("r1", "查询订单表昨日数据", "data_query", [])
        await im.record_example("r2", "订单表有多少条记录", "data_query", [])
        # r2 被 👎 → negative，检索排除
        await im.harden_by_run("r2")
        # r1 被 👍 → positive
        await im.mark_positive("r1")

        hits = await im.retrieve_examples("统计订单表行数", top_k=3)
        texts = [h["query_text"] for h in hits]
        assert "查询订单表昨日数据" in texts
        assert "订单表有多少条记录" not in texts

    async def test_harden_by_run_flows_to_hard_samples(self, fake_embed):
        await im.record_example("r9", "把订单表清空", "task_execution", [])
        await im.harden_by_run("r9")
        samples = await im.list_hard_samples()
        assert "把订单表清空" in samples

    async def test_only_entity_keys_stored(self, fake_embed):
        """红线：实体只存键名，不存参数明文。"""
        await im.record_example("r1", "查询订单表", "data_query", ["target_table", "data_source"])
        import aiosqlite

        async with aiosqlite.connect(im._db_path()) as conn:
            cursor = await conn.execute("SELECT entities_json FROM intent_examples")
            row = await cursor.fetchone()
        assert row is not None
        assert "target_table" in row[0]
        assert "target_table=" not in row[0]  # 无键值明文


# ---- 困难样本库 --------------------------------------------------------------------


class TestHardSamples:
    async def test_add_and_list(self):
        await im.add_hard_sample("误触发样本一", source="hitl_reject")
        await im.add_hard_sample("误触发样本二", source="thumbs_down")
        await im.add_hard_sample("   ")  # 空白忽略
        samples = await im.list_hard_samples()
        assert set(samples) == {"误触发样本一", "误触发样本二"}

    async def test_semantic_route_picks_up_hard_samples(self, monkeypatch, fake_embed):
        """语义路由把困难样本合并为全局负样本并拦截。"""
        from agent.config import settings
        from agent.graph.semantic_route import Route, SemanticIntentRouter

        monkeypatch.setattr(settings, "semantic_route_enabled", True)
        await im.add_hard_sample("查询订单表但上次执行错了")

        class _Emb:
            model = "fake"

            def _vec(self, text):
                # 所有「订单」文本同向量 → 靠动态负样本拦截
                return [0.0, 1.0] if "订单" in text else [-1.0, 0.0]

            async def health_check(self):
                return True

            async def embed(self, text):
                return self._vec(text)

            async def embed_batch(self, texts):
                return [self._vec(t) for t in texts]

        routes = (Route(name="db_query", utterances=("查询订单表",), analysis={"intent": "query"}),)
        router = SemanticIntentRouter(routes, embedding=_Emb())
        out = await router.route("查询订单表但上次执行错了")
        assert out is None  # 动态负样本拦截


# ---- 操作链路记忆 ------------------------------------------------------------------


class TestRecentChain:
    async def test_record_and_chain_order(self):
        await im.record_recent("tab-x", "data_query｜target_table=a")
        await im.record_recent("tab-x", "task_execution｜data_source=ds")
        await im.record_recent("tab-y", "chat")
        chain = await im.recent_chain("tab-x")
        assert chain == ["data_query｜target_table=a", "task_execution｜data_source=ds"]

    async def test_keep_limit_prunes(self):
        for i in range(8):
            await im.record_recent("tab-p", f"step{i}")
        chain = await im.recent_chain("tab-p", limit=10)
        assert len(chain) == 5
        assert chain[-1] == "step7"


# ---- Few-Shot 注入 prompt ----------------------------------------------------------


class TestComposePrompt:
    _BLOCK_HEADER = "# 参考历史案例"

    async def test_compose_with_examples(self, fake_embed):
        await im.record_example("r1", "查询订单表昨日数据", "data_query", [])
        prompt = await im.compose_intent_system_prompt("统计订单表行数")
        assert self._BLOCK_HEADER in prompt
        assert "查询订单表昨日数据" in prompt
        assert "intent_category=data_query" in prompt

    async def test_compose_empty_library_plain_template(self):
        prompt = await im.compose_intent_system_prompt("随便问点别的")
        assert self._BLOCK_HEADER not in prompt
        assert "意图分析器" in prompt

    async def test_compose_embed_failure_falls_back(self, monkeypatch):
        await im.record_example("r1", "查询订单表昨日数据", "data_query", [])

        async def _broken(text):
            return None

        monkeypatch.setattr(im, "_embed_text", _broken)
        prompt = await im.compose_intent_system_prompt("统计订单表行数")
        assert self._BLOCK_HEADER not in prompt
