"""V1 LLM 意图分类 + 关键词回退测试。"""

import pytest
from agent.skills.intent_classifier import IntentResult
from agent.skills.loader import SkillLoader
from agent.skills.router import SkillRouter


@pytest.fixture
def router(tmp_path):
    d = tmp_path / "skills"
    d.mkdir()
    (d / "order.yaml").write_text(
        """
schema_version: "1.0"
id: db_query_order
name: 订单
trigger_keywords: [订单, order]
role: utility
""",
        encoding="utf-8",
    )
    (d / "finance.yaml").write_text(
        """
schema_version: "1.0"
id: finance_reconcile
name: 财务对账
trigger_keywords: [对账, reconcile]
role: reasoning
""",
        encoding="utf-8",
    )
    loader = SkillLoader(d)
    loader.load_all()
    return SkillRouter(loader)


def test_sync_keyword_route_unchanged(router):
    """V0 同步 route() 行为不变。"""
    r = router.route("查订单")
    assert r.skill_id == "db_query_order"
    r2 = router.route("完全无关")
    assert r2.skill_id is None


@pytest.mark.asyncio
async def test_route_async_keyword_high_confidence_skips_llm(router):
    """关键词 2+ 命中（≥0.67）直接走，不调 LLM。"""
    r = await router.route_async("订单 查 order")
    # 2 关键词命中 → confidence 0.67 → 直接返回
    assert r.skill_id == "db_query_order"
    assert r.confidence == pytest.approx(0.67, abs=0.01)


@pytest.mark.asyncio
async def test_route_async_low_confidence_uses_keyword(router, monkeypatch):
    """1 关键词命中（<0.67）走 LLM 路径；LLM 不可用 → 关键词回退。"""

    # Mock LLM 不可用
    async def mock_unavailable(*args, **kwargs):
        return IntentResult(skill_id=None, confidence=0.0)

    monkeypatch.setattr("agent.skills.router.classify_with_llm", mock_unavailable)

    r = await router.route_async("订单")  # 1 命中 = 0.33
    assert r.skill_id == "db_query_order"
    assert r.confidence == pytest.approx(0.33, abs=0.01)


@pytest.mark.asyncio
async def test_route_async_no_match(router, monkeypatch):
    """完全无匹配 → 关键词返回 skill_id=None。"""

    async def mock_unavailable(*args, **kwargs):
        return IntentResult(skill_id=None, confidence=0.0)

    monkeypatch.setattr("agent.skills.router.classify_with_llm", mock_unavailable)

    r = await router.route_async("完全不相关的内容")
    assert r.skill_id is None
    assert r.confidence == 0.0


@pytest.mark.asyncio
async def test_route_async_with_mocked_llm(router, monkeypatch):
    """Mock LLM 返回 utility 决断 → 跳过 reasoning 层。

    修复：BUGFIX-V1-intent-mock：必须用 `import agent.skills.router as r_mod`
    并改 `r_mod.classify_with_llm`，因为 SkillRouter._classify_with_one_backend
    调用 `classify_with_llm(...)` 时是从 router 模块命名空间 lookup，而
    `monkeypatch.setattr("agent.skills.router.classify_with_llm", ...)` 同样
    写的是模块全局，但 SkillLoader.list() / SkillRouter 路径不引用模块全局，
    所以必须 monkeypatch 模块。
    """
    import agent.skills.router as router_mod

    mock_result = IntentResult(
        skill_id="finance_reconcile", confidence=0.85, reasoning="utility 选定"
    )
    call_log = []

    async def mock_classify(*args, **kwargs):
        call_log.append(("called", kwargs.get("ollama_base_url", "")))
        return mock_result

    monkeypatch.setattr(router_mod, "classify_with_llm", mock_classify)
    r = await router.route_async("对账")
    print(f"\nDEBUG call_log={call_log} skill={r.skill_id} conf={r.confidence}")
    assert r.skill_id == "finance_reconcile"
    assert r.confidence == 0.85


@pytest.mark.asyncio
async def test_route_async_no_utility_backend_falls_to_keyword(router, monkeypatch):
    """无 utility 后端 → 跳过 utility 层 → 关键词回退。"""
    # 临时改 utility role
    loader = router._loader
    util = loader.get("db_query_order")
    original_role = util.role
    util.role = "execution"
    try:

        async def mock_unavailable(*args, **kwargs):
            return IntentResult(skill_id=None, confidence=0.0)

        monkeypatch.setattr("agent.skills.router.classify_with_llm", mock_unavailable)

        r = await router.route_async("完全不相关")
        assert r.skill_id is None
    finally:
        util.role = original_role
