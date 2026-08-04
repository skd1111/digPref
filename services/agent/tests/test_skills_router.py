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
    (d / "order.yaml").write_text("""
schema_version: "1.0"
id: db_query_order
name: 订单
trigger_keywords: [订单, order]
""", encoding="utf-8")
    (d / "user.yaml").write_text("""
schema_version: "1.0"
id: db_query_user
name: 用户
trigger_keywords: [用户, user]
""", encoding="utf-8")
    (d / "ssh.yaml").write_text("""
schema_version: "1.0"
id: ssh_diagnostic
name: SSH
trigger_keywords: [ssh, 远程]
enabled: false
""", encoding="utf-8")
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
