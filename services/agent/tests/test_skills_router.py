"""Router 单元测试。"""

import pytest
from agent.skills.loader import SkillLoader
from agent.skills.models import Skill
from agent.skills.router import SkillRouter


@pytest.fixture
def router(tmp_path):
    """Loader + 3 skills：order / user / ssh，order 关键词命中 2 个，user 1 个。"""
    d = tmp_path / "skills"
    d.mkdir()
    (d / "order.yaml").write_text(
        """
schema_version: "1.0"
id: db_query_order
name: 订单
trigger_keywords: [订单, order]
""",
        encoding="utf-8",
    )
    (d / "user.yaml").write_text(
        """
schema_version: "1.0"
id: db_query_user
name: 用户
trigger_keywords: [用户, user]
""",
        encoding="utf-8",
    )
    (d / "ssh.yaml").write_text(
        """
schema_version: "1.0"
id: ssh_diagnostic
name: SSH
trigger_keywords: [ssh, 远程]
enabled: false
""",
        encoding="utf-8",
    )
    loader = SkillLoader(d)
    loader.load_all()
    return SkillRouter(loader)


def test_route_matches_keyword(router):
    r = router.route("查一下订单")
    assert r.skill_id == "db_query_order"
    assert "订单" in r.matched_keywords


def test_route_no_match(router):
    r = router.route("完全无关的查询")
    assert r.skill_id is None
    assert r.confidence == 0.0


def test_route_skips_disabled(router):
    r = router.route("ssh 远程登录")
    assert r.skill_id is None  # ssh_diagnostic enabled=false


def test_route_tiebreak_min_id(router):
    """两个 skill 命中 1 关键词，并列取 id 字典序最小。"""
    r = router.route("查 order 和 user")  # 都命中 1 个
    # order 和 user 各 1 分，取字典序小者
    assert r.skill_id == "db_query_order"


def test_route_confidence_three_keywords_max(router):
    """3 个不同关键词命中 → confidence=1.0。
    注：相同 kw 重复 N 次只算 1 hit（substring check + set semantics）。"""
    r = router.route("订单 查 order")  # 2 hits, confidence = 2/3
    assert r.skill_id == "db_query_order"
    assert r.confidence == pytest.approx(2 / 3, abs=0.01)
    # 加一个 "订单查" 临时塞到 trigger_keywords 测 3 hits
    router._loader._skills["db_query_order"].trigger_keywords.append("订单查")
    r2 = router.route("订单 查 order 订单查")  # 3 hits
    assert r2.confidence == pytest.approx(1.0, abs=0.01)


def test_build_system_prompt_no_skill(router):
    base = "base prompt"
    out = router.build_system_prompt(base, None)
    assert out == base


def test_build_system_prompt_with_skill(router):
    skill = router._loader.get("db_query_order")
    out = router.build_system_prompt("base", skill)
    assert "base" in out
    assert "订单" in out
    assert "## 当前技能" in out


# ---- 2026-08-26 关键词归一化 + 中英混排兜底 -------------------------------


@pytest.fixture
def office_router(tmp_path):
    """内置 PPT skill 同构关键词集（做ppt/生成ppt/演示文稿…）。"""
    d = tmp_path / "office_skills"
    d.mkdir()
    (d / "pptx.yaml").write_text(
        """
schema_version: "1.0"
id: office_pptx_designer
name: PPT 汇报生成规范
trigger_keywords: [做ppt, 生成ppt, 演示文稿, 幻灯片]
""",
        encoding="utf-8",
    )
    loader = SkillLoader(d)
    loader.load_all()
    return SkillRouter(loader)


def test_keyword_split_phrasing_hits_ppt_skill(office_router):
    """真实案例：「做一个介绍下你自己的ppt」不含连续「做ppt」也要命中。"""
    r = office_router.route("做一个介绍下你自己（EAIDE 企业 AI 助理）的ppt")
    assert r.skill_id == "office_pptx_designer"


def test_keyword_with_space_and_case(office_router):
    r = office_router.route("帮我 做个 PPT 吧")
    assert r.skill_id == "office_pptx_designer"


def test_keyword_normalize_exact(office_router):
    r = office_router.route("生成PPT")  # 大写 + 无空格
    assert r.skill_id == "office_pptx_designer"


def test_pure_chinese_keyword_still_works(office_router):
    r = office_router.route("来一份演示文稿")
    assert r.skill_id == "office_pptx_designer"


def test_no_token_no_false_hit(office_router):
    r = office_router.route("今天天气怎么样")
    assert r.skill_id is None


# ---- 2026-08-26 Skill 粘性（intent_node 继承上一轮技能） -----------------


def test_followup_prompt_detected():
    from agent.graph.nodes.intent import _looks_like_followup

    assert _looks_like_followup("也太丑了，用你自带的skill优化一下")
    assert _looks_like_followup("重新做，配色换成绿色")
    assert not _looks_like_followup("今天几号")
    # 长输入判为新任务，不继承
    assert not _looks_like_followup("优化" + "需求" * 80)


def test_inherit_last_skill_requires_loader_hit(tmp_path, monkeypatch):
    from agent.graph.nodes import intent as intent_mod
    from agent.skills import api as skills_api

    d = tmp_path / "skills"
    d.mkdir()
    (d / "pptx.yaml").write_text(
        """
schema_version: "1.0"
id: office_pptx_designer
name: PPT 汇报生成规范
trigger_keywords: [做ppt]
""",
        encoding="utf-8",
    )
    loader = SkillLoader(d)
    loader.load_all()
    saved = skills_api._loader
    monkeypatch.setattr(skills_api, "_loader", loader)
    try:
        got = intent_mod._inherit_last_skill("太丑了，优化一下", "office_pptx_designer")
        assert got is not None and got.skill_id == "office_pptx_designer"
        # 非追问短句 → 不继承
        assert intent_mod._inherit_last_skill("今天几号", "office_pptx_designer") is None
        # 未知/不存在的前轮 skill → 不继承
        assert intent_mod._inherit_last_skill("太丑了", "no_such_skill") is None
    finally:
        monkeypatch.setattr(skills_api, "_loader", saved)


def test_classify_with_llm_fenced_json(monkeypatch):
    """围栏 JSON 仍可解析，null skill 也正常（spec §4.5 第三层）。"""
    import asyncio

    import httpx
    from agent.skills.intent_classifier import classify_with_llm

    async def fake_post(*a, **k):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {
                    "message": {
                        "content": (
                            '```json\n{"skill_id": null, "confidence": 0, '
                            '"reasoning": "no match"}\n```'
                        )
                    }
                }

        return R()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    skills = [Skill(id="db", name="数据库", description="d", trigger_keywords=["查表"])]
    result = asyncio.run(classify_with_llm("你好", skills))
    assert result is not None and result.skill_id is None
