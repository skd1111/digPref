"""intent_node 集成 skill 路由 + system_prompt 注入测试。"""
import pytest

from agent.skills import api as api_mod
from agent.skills.router import SkillRouter
from agent.skills.loader import SkillLoader


@pytest.fixture
def reset_loader(tmp_path):
    """重置全局 loader，注入临时目录。"""
    test_dir = tmp_path / "eaide" / "skills"
    test_dir.mkdir(parents=True, exist_ok=True)
    (test_dir / "order.yaml").write_text("""
schema_version: "1.0"
id: db_query_order
name: 订单
trigger_keywords: [订单, order]
system_prompt: 你是一名订单分析师
""", encoding="utf-8")
    new_loader = SkillLoader(test_dir)
    new_loader.load_all()
    saved = api_mod._loader
    api_mod._loader = new_loader
    yield new_loader
    api_mod._loader = saved


def test_router_picks_correct_skill(reset_loader):
    r = SkillRouter(reset_loader).route("查订单")
    assert r.skill_id == "db_query_order"
    assert r.confidence > 0


def test_router_threshold_filters_low_confidence(reset_loader):
    """置信度 < 0.33 不应被注入。"""
    r = SkillRouter(reset_loader).route("x")  # 完全无关
    assert r.skill_id is None
    # 1 关键词命中 (confidence = 1/3 = 0.33) 应该 ≥ threshold (0.33)
    # **C6 fix**: 之前 0.34 threshold 会让 1 关键词命中落空。V0 改为 0.33
    r2 = SkillRouter(reset_loader).route("order")  # 单关键词
    assert r2.confidence == pytest.approx(0.33, abs=0.01)
    assert r2.confidence >= 0.33  # 1 关键词应该触发
    # 0 关键词不触发
    r3 = SkillRouter(reset_loader).route("zzz")
    assert r3.skill_id is None


def test_build_system_prompt_includes_skill(reset_loader):
    skill = reset_loader.get("db_query_order")
    out = SkillRouter(reset_loader).build_system_prompt("BASE", skill)
    assert "BASE" in out
    assert "订单" in out
    assert "你是一名订单分析师" in out
    assert "## 当前技能" in out
