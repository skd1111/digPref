"""Phase 13 V0 — DSpark 推测解码断言测试。

覆盖范围：
  - DSparkConfig Pydantic 校验（n_draft / draft_p_min 边界）
  - SpeculativePolicy.enabled 派发性（off / 边界 / 正常）
  - DEFAULT_POLICIES 5 模式覆盖
  - YAML 加载 + 混合 bool 防御 + 缺 yaml 落回默认
  - decide_dspark 五关铁律：
      1. runtime 未初始化 → off-no-runtime
      2. 全局开关关闭 → off
      3. _LOCAL_ONLY_TASKS 强制 off（即使 YAML 配了也覆盖）
      4. 短输出跳过 → off
      5. 草稿模型路径为空 → off
      6. 类别策略 + 未知类别落回 conservative（**修复后必须 enabled=True**）
  - RoutingDecision 4 字段默认值 + trace_dict 包含
  - DSparkEngine 环形 buffer + stats 汇总
  - 加固：deep copy / reason 顺序 / fallback conservative 启用
"""
import pytest
from pathlib import Path
from pydantic import ValidationError

from agent.llm.dspark import (
    DSparkConfig,
    DEFAULT_POLICIES,
    SPECULATIVE_OFF,
    SpeculativePolicy,
    decide_dspark,
    load_speculative_policies,
)
from agent.llm.dspark.config import policy_for_mode
from agent.llm.dspark.engine import DSparkEngine, make_record
from agent.llm.dspark.policy import (
    _decide_dspark_with_reason,
    REASON_APPLIED,
    REASON_APPLIED_DEFAULT,
    REASON_OFF_GLOBAL,
    REASON_OFF_LOCAL_ONLY,
    REASON_OFF_NO_DRAFT,
    REASON_OFF_NO_RUNTIME,
    REASON_OFF_SHORT,
)
from agent.llm.models import RoutingDecision, TaskCategory, Sensitivity


def _rd(i: int = 0, **overrides) -> RoutingDecision:
    """构造一个 RoutingDecision 工厂（带 DSpark 4 字段默认值）"""
    base = dict(
        request_id=f"r{i}",
        speculative_enabled=(i % 2 == 0),
        n_draft=8 if i % 2 == 0 else 1,
        draft_p_min=0.75 if i % 2 == 0 else 1.0,
        actual_backend="ollama",
    )
    base.update(overrides)
    return RoutingDecision(**base)


# 保存关键模块级常量的原始值（防止测试本身污染全局，被后续测试读到）
@pytest.fixture(autouse=True)
def _guard_globals():
    """每个测试跑完后还原 SPECULATIVE_OFF 和 DEFAULT_POLICIES 的关键字段。

    即便测试逻辑正确，也不允许它们意外修改全局常量。
    """
    saved = (SPECULATIVE_OFF.n_draft, SPECULATIVE_OFF.draft_p_min)
    saved_defaults = {k: (v.n_draft, v.draft_p_min) for k, v in DEFAULT_POLICIES.items()}
    yield
    SPECULATIVE_OFF.n_draft, SPECULATIVE_OFF.draft_p_min = saved
    # 恢复 DEFAULT_POLICIES 中每个 SpeculativePolicy 的关键字段
    for cat, (n_d, p_min) in saved_defaults.items():
        if cat in DEFAULT_POLICIES:
            DEFAULT_POLICIES[cat].n_draft = n_d
            DEFAULT_POLICIES[cat].draft_p_min = p_min



# ---- DSparkConfig Pydantic 边界 -----------------------------------------


def test_config_default_draft_path_none():
    """默认 draft_model_path=None → DSpark 未启用"""
    cfg = DSparkConfig()
    assert cfg.draft_model_path is None
    assert cfg.enable_global is True
    assert cfg.short_output_threshold == 20


def test_config_short_output_threshold_bounds():
    """short_output_threshold 必须 >= 1"""
    with pytest.raises(ValidationError):
        DSparkConfig(short_output_threshold=0)
    DSparkConfig(short_output_threshold=1)  # OK


def test_config_profiles_default_is_copy_per_instance():
    """两次构造 DSparkConfig 各自持有独立的 profiles dict（避免共享可变）。

    **修复 5 后**：default_factory 用 .model_copy() 深拷贝，理论上 cfg1.profiles["intent"]
    改动不影响 cfg2.profiles["intent"]，也不影响 DEFAULT_POLICIES["intent"]。
    """
    cfg1 = DSparkConfig()
    cfg2 = DSparkConfig()
    # **修复 6**：用 model_copy() 而非 SPECULATIVE_OFF（避免污染全局哨兵）
    cfg1.profiles["intent"] = SPECULATIVE_OFF.model_copy(update={"n_draft": 99})
    # 验证：cfg2 不变
    assert cfg2.profiles["intent"].n_draft != 99
    # 验证：DEFAULT_POLICIES 不变
    assert DEFAULT_POLICIES["intent"].n_draft != 99
    # 验证：SPECULATIVE_OFF 不变（修复 6 关键）
    assert SPECULATIVE_OFF.n_draft != 99


def test_config_profiles_mutation_does_not_leak_to_default_policies():
    """**修复 5 加固**：cfg.profiles 改动不影响 DEFAULT_POLICIES"""
    cfg = DSparkConfig()
    cfg.profiles["sql_generation"].n_draft = 99
    cfg.profiles["sql_generation"].draft_p_min = 0.99
    # DEFAULT_POLICIES 应保持原值
    assert DEFAULT_POLICIES["sql_generation"].n_draft == 8
    assert DEFAULT_POLICIES["sql_generation"].draft_p_min == 0.75


def test_config_profiles_mutation_does_not_leak_to_other_instance():
    """**修复 5 加固**：cfg1.profiles 改动不影响 cfg2.profiles"""
    cfg1 = DSparkConfig()
    cfg2 = DSparkConfig()
    cfg1.profiles["code_completion"].n_draft = 16
    assert cfg2.profiles["code_completion"].n_draft == 8  # 不变


# ---- 4 模式派生 --------------------------------------------------------


def test_policy_for_mode_returns_4_levels():
    """4 档预设 K/阈值"""
    assert policy_for_mode("aggressive") == (8, 0.75)
    assert policy_for_mode("standard") == (4, 0.85)
    assert policy_for_mode("conservative") == (2, 0.90)
    assert policy_for_mode("off") == (1, 1.0)


def test_default_policies_cover_all_phase13_categories():
    """DEFAULT_POLICIES 必须覆盖 Phase 1 + 2C + 12 + 13 的全部 task_category"""
    expected = {
        "intent", "repair", "skill_router", "data_summary",  # sensitive
        "plan", "summarise",  # complex
        "sql_generation", "code_completion",  # aggressive
        "code_explanation", "log_analysis", "chat_qa", "toolspec",  # misc
    }
    assert set(DEFAULT_POLICIES.keys()) >= expected


def test_speculative_policy_enabled_derivation():
    """enabled 派发性：off / n_draft=1 / threshold=1.0 → False"""
    assert SpeculativePolicy(task_category="x", mode="off").enabled is False
    assert SpeculativePolicy(task_category="x", mode="aggressive", n_draft=1).enabled is False
    assert SpeculativePolicy(task_category="x", mode="aggressive", n_draft=8, draft_p_min=1.0).enabled is False
    # 正常
    assert SpeculativePolicy(task_category="x", mode="aggressive", n_draft=8, draft_p_min=0.75).enabled is True


def test_speculative_policy_pydantic_bounds():
    """n_draft / draft_p_min 范围校验"""
    with pytest.raises(ValidationError):
        SpeculativePolicy(task_category="x", mode="aggressive", n_draft=0)
    with pytest.raises(ValidationError):
        SpeculativePolicy(task_category="x", mode="aggressive", n_draft=17)
    with pytest.raises(ValidationError):
        SpeculativePolicy(task_category="x", mode="aggressive", draft_p_min=-0.1)
    with pytest.raises(ValidationError):
        SpeculativePolicy(task_category="x", mode="aggressive", draft_p_min=1.1)


# ---- YAML 加载 ----------------------------------------------------------


def test_load_yaml_returns_defaults_when_missing(tmp_path):
    """yaml 不存在 → 落回 DEFAULT_POLICIES"""
    pms = load_speculative_policies(tmp_path / "no.yaml")
    assert pms == DEFAULT_POLICIES


def test_load_yaml_returns_defaults_when_none():
    """yaml 路径为 None → 落回 DEFAULT_POLICIES"""
    assert load_speculative_policies(None) == DEFAULT_POLICIES


def test_load_yaml_real_file(tmp_path):
    """加载仓库自带的 speculative.yaml，应至少 12 个 category"""
    yaml_path = Path(__file__).resolve().parents[1] / "src" / "agent" / "config" / "llm" / "speculative.yaml"
    pms = load_speculative_policies(yaml_path)
    assert len(pms) >= 12
    assert pms["sql_generation"].mode == "aggressive"
    assert pms["intent"].mode == "off"


def test_load_yaml_overrides_per_category(tmp_path):
    """YAML 允许覆盖 n_draft / draft_p_min"""
    yaml = """
profiles:
  - mode: aggressive
    task_categories: [sql_generation]
    n_draft: 6
    draft_p_min: 0.80
"""
    p = tmp_path / "spec.yaml"
    p.write_text(yaml, encoding="utf-8")
    pms = load_speculative_policies(p)
    pol = pms["sql_generation"]
    assert pol.mode == "aggressive"
    assert pol.n_draft == 6
    assert pol.draft_p_min == 0.80


def test_load_yaml_bool_coercion_defensive(tmp_path):
    """YAML 1.1 把 off 解析成 bool → 防御性转回 'off'"""
    yaml = """
profiles:
  - mode: off
    task_categories: [intent]
"""
    p = tmp_path / "spec.yaml"
    p.write_text(yaml, encoding="utf-8")
    pms = load_speculative_policies(p)
    # 即便 yaml 把 off 解析成 False，loader 强制转回 "off"
    assert pms["intent"].mode == "off"


def test_load_yaml_invalid_profile_skipped(tmp_path):
    """缺 mode 的 profile 跳过，其它正常加载"""
    yaml = """
profiles:
  - task_categories: [broken]
  - mode: aggressive
    task_categories: [sql_generation]
"""
    p = tmp_path / "spec.yaml"
    p.write_text(yaml, encoding="utf-8")
    pms = load_speculative_policies(p)
    assert "broken" not in pms
    assert pms["sql_generation"].mode == "aggressive"


# ---- decide_dspark 五关铁律 -------------------------------------------


def _cfg(draft="/tmp/fake.gguf", enable=True, short=20):
    return DSparkConfig(draft_model_path=draft, enable_global=enable, short_output_threshold=short)


def test_decide_dspark_global_off_first():
    """铁律 1：全局开关关闭 → off"""
    pol = decide_dspark(
        config=_cfg(enable=False),
        policies=DEFAULT_POLICIES,
        task_category="sql_generation",
        max_tokens=500,
    )
    assert pol.mode == "off"
    assert pol.enabled is False


def test_decide_dspark_local_only_force_off():
    """铁律 2：_LOCAL_ONLY_TASKS 强制 off（即使 DEFAULT 把 sql 设为 aggressive）"""
    local_only = frozenset({"intent", "repair", "skill_router", "data_summary"})
    for cat in local_only:
        pol = decide_dspark(
            config=_cfg(),
            policies=DEFAULT_POLICIES,
            task_category=cat,
            max_tokens=500,
            local_only_tasks=local_only,
        )
        assert pol.mode == "off", f"{cat} should be forced off"
        assert pol.enabled is False


def test_decide_dspark_local_only_overrides_yaml():
    """铁律 2：即便 YAML 把 intent 配成 aggressive 也强制 off"""
    pms = dict(DEFAULT_POLICIES)
    pms["intent"] = SpeculativePolicy(task_category="intent", mode="aggressive", n_draft=8, draft_p_min=0.75)
    local_only = frozenset({"intent"})
    pol = decide_dspark(
        config=_cfg(),
        policies=pms,
        task_category="intent",
        max_tokens=500,
        local_only_tasks=local_only,
    )
    assert pol.mode == "off"  # 强制覆盖
    assert pol.enabled is False


def test_decide_dspark_short_output_skip():
    """铁律 3：max_tokens < threshold → off"""
    pol = decide_dspark(
        config=_cfg(short=20),
        policies=DEFAULT_POLICIES,
        task_category="sql_generation",
        max_tokens=10,
    )
    assert pol.mode == "off"


def test_decide_dspark_no_draft_path():
    """铁律 4：draft_model_path=None → off"""
    pol = decide_dspark(
        config=DSparkConfig(draft_model_path=None),
        policies=DEFAULT_POLICIES,
        task_category="sql_generation",
        max_tokens=500,
    )
    assert pol.mode == "off"


def test_decide_dspark_applies_known_policy():
    """sql_generation → aggressive (K=8, p=0.75)"""
    pol = decide_dspark(
        config=_cfg(),
        policies=DEFAULT_POLICIES,
        task_category="sql_generation",
        max_tokens=500,
    )
    assert pol.mode == "aggressive"
    assert pol.n_draft == 8
    assert pol.draft_p_min == 0.75
    assert pol.enabled is True


def test_decide_dspark_unknown_category_falls_back_to_conservative():
    """铁律 5：未知类别 → conservative 兜底（**修复 2 后必须 enabled=True**）"""
    pol, reason = _decide_dspark_with_reason(
        config=_cfg(),
        policies=DEFAULT_POLICIES,
        task_category="nonexistent",
        max_tokens=500,
    )
    assert pol.mode == "conservative"
    assert reason == REASON_APPLIED_DEFAULT
    # 关键：fallback 的 K/p_min 必须从 policy_for_mode 派生（即 K=2, p=0.90）
    # 否则会变成 n_draft=1, draft_p_min=1.0 → enabled=False（这就是 bug 2）
    assert pol.n_draft == 2
    assert pol.draft_p_min == pytest.approx(0.90)
    assert pol.enabled is True  # **修复 2 关键验证**


def test_decide_with_reason_consistency_between_paths():
    """**修复 3 加固**：_decide_dspark_with_reason 5 关顺序与 reason
    （不要分两个独立函数维护，避免漂移）。

    这里测 5 关每关的 reason 字符串，锁死顺序：
        off-no-runtime > off-global > off-local-only > off-short > off-no-draft > applied-default > applied
    """
    cfg = _cfg()
    policies = DEFAULT_POLICIES
    local_only = frozenset({"intent"})

    # 1. runtime 未初始化
    _, reason = _decide_dspark_with_reason(
        config=None, policies=policies, task_category="sql_generation", max_tokens=500,
        local_only_tasks=local_only,
    )
    assert reason == REASON_OFF_NO_RUNTIME

    # 2. 全局关闭
    _, reason = _decide_dspark_with_reason(
        config=DSparkConfig(enable_global=False, draft_model_path="/x"),
        policies=policies, task_category="sql_generation", max_tokens=500, local_only_tasks=local_only,
    )
    assert reason == REASON_OFF_GLOBAL

    # 3. 敏感任务（**注意**：短输出但本地任务 → 优先本地，铁律 3 排序）
    _, reason = _decide_dspark_with_reason(
        config=cfg, policies=policies, task_category="intent", max_tokens=10, local_only_tasks=local_only,
    )
    assert reason == REASON_OFF_LOCAL_ONLY

    # 4. 短输出
    _, reason = _decide_dspark_with_reason(
        config=cfg, policies=policies, task_category="sql_generation", max_tokens=10, local_only_tasks=local_only,
    )
    assert reason == REASON_OFF_SHORT

    # 5. 无草稿模型
    _, reason = _decide_dspark_with_reason(
        config=DSparkConfig(enable_global=True, draft_model_path=None),
        policies=policies, task_category="sql_generation", max_tokens=500, local_only_tasks=local_only,
    )
    assert reason == REASON_OFF_NO_DRAFT

    # 6. 未知类别
    _, reason = _decide_dspark_with_reason(
        config=cfg, policies=policies, task_category="unknown_cat", max_tokens=500, local_only_tasks=local_only,
    )
    assert reason == REASON_APPLIED_DEFAULT

    # 7. 正常
    _, reason = _decide_dspark_with_reason(
        config=cfg, policies=policies, task_category="sql_generation", max_tokens=500, local_only_tasks=local_only,
    )
    assert reason == REASON_APPLIED


def test_decide_for_task_engine_path_consistency():
    """**修复 3 加固**：engine.route_request() 路径（生产）= 同一份决策。

    模拟 api.decide_for_task 行为（取 runtime + 调 _decide_dspark_with_reason）：
    """
    from agent.llm.dspark import api as dspark_api
    from agent.llm.dspark.config import DSparkConfig
    cfg = DSparkConfig(draft_model_path="/tmp/x.gguf", enable_global=True, short_output_threshold=20)
    dspark_api.init_dspark_runtime(config=cfg, yaml_path=None, local_only_tasks=["intent"])
    try:
        # 1. 正常 sql_generation → applied
        _, reason = dspark_api.decide_for_task("sql_generation", 500)
        assert reason == REASON_APPLIED
        # 2. intent → off-local-only
        _, reason = dspark_api.decide_for_task("intent", 500)
        assert reason == REASON_OFF_LOCAL_ONLY
        # 3. 短输出 non-local → off-short
        _, reason = dspark_api.decide_for_task("sql_generation", 10)
        assert reason == REASON_OFF_SHORT
    finally:
        dspark_api.reset_dspark_runtime()


# ---- V0.5: 草稿模型路径持久化（POST /dspark/draft-model-path）-------------


def test_draft_model_path_set_then_persisted(tmp_path, monkeypatch):
    """用户从 UI 设置路径 → 运行时生效 + 写入 dspark.json。"""
    from agent.llm.dspark import api as dspark_api

    # 用 tmp_path 隔离 dspark.json
    monkeypatch.setenv("EAIDE_DSPARK_PERSIST_PATH", str(tmp_path / "dspark.json"))
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))

    # 启动时无持久化 → 路径为 None
    cfg = DSparkConfig(draft_model_path=None)
    rt = dspark_api.init_dspark_runtime(config=cfg, yaml_path=None, local_only_tasks=["intent"])
    assert rt.config.draft_model_path is None

    # 模拟 POST /dspark/draft-model-path
    rt.set_draft_model_path("/models/qwen2.5-0.1b.gguf")
    dspark_api._save_persisted_path("/models/qwen2.5-0.1b.gguf")

    # 验证文件已写入
    persisted_file = tmp_path / "dspark.json"
    assert persisted_file.exists()
    import json
    data = json.loads(persisted_file.read_text(encoding="utf-8"))
    assert data["draft_model_path"] == "/models/qwen2.5-0.1b.gguf"

    dspark_api.reset_dspark_runtime()


def test_draft_model_path_load_overrides_env(tmp_path, monkeypatch):
    """持久化路径优先于 env var 默认值（用户在 UI 保存过就以 UI 为准）。"""
    from agent.llm.dspark import api as dspark_api
    import json

    persist = tmp_path / "dspark.json"
    persist.write_text(
        json.dumps({"draft_model_path": "/ui/saved/path.gguf"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("EAIDE_DSPARK_PERSIST_PATH", str(persist))

    # 模拟 main.py 启动流程
    persisted = dspark_api._load_persisted_path()
    cfg = DSparkConfig(
        draft_model_path=persisted or "/env/default/path.gguf",  # env 默认
        enable_global=True,
        short_output_threshold=20,
    )
    rt = dspark_api.init_dspark_runtime(
        config=cfg,
        yaml_path=None,
        local_only_tasks=["intent"],
        persisted_draft_path=persisted,
    )
    # 持久化路径胜出
    assert rt.config.draft_model_path == "/ui/saved/path.gguf"
    dspark_api.reset_dspark_runtime()


def test_draft_model_path_empty_means_disabled(tmp_path, monkeypatch):
    """空串或 None → 全局禁用 DSpark。"""
    from agent.llm.dspark import api as dspark_api

    monkeypatch.setenv("EAIDE_DSPARK_PERSIST_PATH", str(tmp_path / "dspark.json"))
    cfg = DSparkConfig(draft_model_path="/old/path.gguf")
    rt = dspark_api.init_dspark_runtime(config=cfg, yaml_path=None, local_only_tasks=["intent"])
    assert rt.config.draft_model_path == "/old/path.gguf"

    # 用户在 UI 选「清空」→ 后端收到 None
    rt.set_draft_model_path(None)
    dspark_api._save_persisted_path(None)

    assert rt.config.draft_model_path is None
    # 下次启动读到的也是空
    persisted = dspark_api._load_persisted_path()
    assert persisted is None

    dspark_api.reset_dspark_runtime()


def test_draft_model_path_corrupt_json_fallback(tmp_path, monkeypatch):
    """dspark.json 损坏 → 落回 None（不抛异常，优雅降级）。"""
    from agent.llm.dspark import api as dspark_api

    persist = tmp_path / "dspark.json"
    persist.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("EAIDE_DSPARK_PERSIST_PATH", str(persist))

    persisted = dspark_api._load_persisted_path()
    assert persisted is None  # 损坏 → None

    dspark_api.reset_dspark_runtime()


def test_draft_model_path_missing_key_fallback(tmp_path, monkeypatch):
    """dspark.json 缺 draft_model_path 字段 → None。"""
    from agent.llm.dspark import api as dspark_api
    import json

    persist = tmp_path / "dspark.json"
    persist.write_text(json.dumps({"other_key": "x"}), encoding="utf-8")
    monkeypatch.setenv("EAIDE_DSPARK_PERSIST_PATH", str(persist))

    persisted = dspark_api._load_persisted_path()
    assert persisted is None

    dspark_api.reset_dspark_runtime()



# ---- RoutingDecision 4 字段 --------------------------------------------


def test_routing_decision_default_dspark_fields():
    """默认 4 字段：off"""
    rd = RoutingDecision(request_id="r1")
    assert rd.speculative_enabled is False
    assert rd.n_draft == 1
    assert rd.draft_p_min == 1.0
    assert rd.draft_model is None
    assert rd.dspark_reason == "off-no-dspark"


def test_routing_decision_trace_dict_includes_dspark():
    """trace_dict 包含 4 字段 + reason"""
    rd = RoutingDecision(
        request_id="r2",
        task_category=TaskCategory.MEDIUM,
        sensitivity=Sensitivity.PUBLIC,
        speculative_enabled=True,
        n_draft=8,
        draft_p_min=0.75,
        draft_model="/models/draft/q.gguf",
        dspark_reason="applied",
    )
    td = rd.trace_dict()
    assert td["speculative_enabled"] is True
    assert td["n_draft"] == 8
    assert td["draft_p_min"] == 0.75
    assert td["draft_model"] == "/models/draft/q.gguf"
    assert td["dspark_reason"] == "applied"


# ---- DSparkEngine 环形 buffer + stats ---------------------------------


def test_engine_record_and_recent():
    """record + recent 顺序"""
    e = DSparkEngine(_max_history=10)
    for i in range(5):
        e.record(make_record(
            task_category=f"cat{i}",
            decision=_rd(i),
            reason="applied",
            max_tokens=200,
        ))
    items = e.recent(limit=10)
    assert len(items) == 5
    assert items[0].task_category == "cat0"
    assert items[4].task_category == "cat4"


def test_engine_circular_buffer_caps_history():
    """超出 max_history 自动丢最早的"""
    e = DSparkEngine(_max_history=3)
    for i in range(5):
        e.record(make_record(
            task_category=f"cat{i}",
            decision=_rd(i),
            reason="applied",
            max_tokens=200,
        ))
    items = e.recent(limit=10)
    assert len(items) == 3
    # 只保留最近 3 条
    assert items[0].task_category == "cat2"
    assert items[2].task_category == "cat4"


def test_engine_stats_aggregates_reasons():
    """stats 汇总：total / pct / per_category / per_reason"""
    e = DSparkEngine()
    e.record(make_record(task_category="sql", decision=_rd(0, speculative_enabled=True, n_draft=8, draft_p_min=0.75), reason="applied", max_tokens=500))
    e.record(make_record(task_category="sql", decision=_rd(2, speculative_enabled=True, n_draft=8, draft_p_min=0.75), reason="applied", max_tokens=500))
    e.record(make_record(task_category="intent", decision=_rd(1, speculative_enabled=False, n_draft=1, draft_p_min=1.0), reason="off-local-only", max_tokens=100))
    stats = e.stats()
    assert stats["total_decisions"] == 3
    assert stats["dspark_enabled_pct"] == pytest.approx(66.7, abs=0.2)
    assert stats["per_category"]["sql"] == 2
    assert stats["per_reason"]["off-local-only"] == 1


def test_engine_stats_empty():
    """空 buffer → 0 / 空 dict"""
    e = DSparkEngine()
    stats = e.stats()
    assert stats["total_decisions"] == 0
    assert stats["dspark_enabled_pct"] == 0.0
    assert stats["per_category"] == {}
    assert stats["per_reason"] == {}


# ---- SPECULATIVE_OFF 哨兵 ----------------------------------------------


def test_speculative_off_sentinel():
    """SPECULATIVE_OFF 永远是 off（sentinel）"""
    assert SPECULATIVE_OFF.mode == "off"
    assert SPECULATIVE_OFF.enabled is False


# ---- V0.6: 审计落库（POST /dspark/draft-model-path 与 /dspark/config）-----


import asyncio
import json


async def _read_audit_entries(db_path) -> list[dict]:
    """读 audit.sqlite 所有条目（_isolate 自动注入 tmp_path）。

    如果 audit 表尚不存在（从未有 audit 调用写入过），返回空列表。
    """
    import aiosqlite
    from agent.audit.store import SCHEMA_CREATE_TABLE, SCHEMA_INDEXES
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript(SCHEMA_CREATE_TABLE + SCHEMA_INDEXES)
        cur = await db.execute(
            "SELECT action, payload, ts FROM audit ORDER BY id ASC"
        )
        rows = await cur.fetchall()
    return [
        {"action": a, "payload": json.loads(p), "ts": t}
        for (a, p, t) in rows
    ]


@pytest.mark.asyncio
async def test_draft_model_path_endpoint_emits_audit(tmp_path, monkeypatch):
    """POST /dspark/draft-model-path → audit.sqlite 落 1 条 dspark_config_change。

    payload 含 actor_type='system' / event_type / changed_fields / old / new。
    """
    from agent.llm.dspark import api as dspark_api
    monkeypatch.setenv("EAIDE_DSPARK_PERSIST_PATH", str(tmp_path / "dspark.json"))
    audit_db = tmp_path / "audit.sqlite"
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(audit_db))

    cfg = DSparkConfig(draft_model_path=None)
    dspark_api.init_dspark_runtime(config=cfg, yaml_path=None, local_only_tasks=["intent"])
    try:
        # 调用 endpoint（async）
        resp = await dspark_api.set_draft_model_path(
            dspark_api.DraftModelPathBody(path="/models/qwen.gguf")
        )
        assert resp["ok"] is True
        assert resp["draft_model_path"] == "/models/qwen.gguf"

        # 验证 audit.sqlite（用 endpoint 实际写入的绝对路径，避免 chdir 不稳）
        entries = await _read_audit_entries(audit_db)
        dspark_entries = [e for e in entries if e["action"] == "dspark_config_change"]
        assert len(dspark_entries) == 1, f"expected 1 audit entry, got {len(dspark_entries)}"
        p = dspark_entries[0]["payload"]
        assert p["actor_type"] == "system"
        assert p["event_type"] == "dspark_draft_model_change"
        assert p["changed_fields"] == ["draft_model_path"]
        assert p["old"]["draft_model_path"] is None
        assert p["new"]["draft_model_path"] == "/models/qwen.gguf"
    finally:
        dspark_api.reset_dspark_runtime()


@pytest.mark.asyncio
async def test_config_endpoint_emits_audit_with_diff(tmp_path, monkeypatch):
    """POST /dspark/config 多字段更新 → audit 落 changed_fields + old/new 快照。

    验证所有改动字段都被记录，方便事后审计"谁改了什么"。
    """
    from agent.llm.dspark import api as dspark_api
    monkeypatch.setenv("EAIDE_DSPARK_PERSIST_PATH", str(tmp_path / "dspark.json"))
    audit_db = tmp_path / "audit.sqlite"
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(audit_db))

    cfg = DSparkConfig(
        draft_model_path=None,
        context_size=4096,
        gpu_layers=0,
        enable_global=True,
        short_output_threshold=20,
    )
    dspark_api.init_dspark_runtime(config=cfg, yaml_path=None, local_only_tasks=["intent"])
    try:
        # 一次性改 3 个字段
        body = dspark_api.DSparkConfigUpdateBody(
            context_size=8192,
            gpu_layers=16,
            enable_global=False,
        )
        resp = await dspark_api.update_config(body)
        assert resp["ok"] is True
        assert resp["config"]["context_size"] == 8192
        assert resp["config"]["gpu_layers"] == 16
        assert resp["config"]["enable_global"] is False

        # 验证 audit.sqlite 落 1 条
        entries = await _read_audit_entries(audit_db)
        dspark_entries = [e for e in entries if e["action"] == "dspark_config_change"]
        assert len(dspark_entries) == 1
        p = dspark_entries[0]["payload"]
        assert p["actor_type"] == "system"
        assert p["event_type"] == "dspark_config_change"
        assert set(p["changed_fields"]) == {"context_size", "gpu_layers", "enable_global"}
        # old / new 快照对比
        assert p["old"]["context_size"] == 4096
        assert p["new"]["context_size"] == 8192
        assert p["old"]["gpu_layers"] == 0
        assert p["new"]["gpu_layers"] == 16
        assert p["old"]["enable_global"] is True
        assert p["new"]["enable_global"] is False
        # 未改动的字段不应出现在快照里
        assert "draft_model_path" not in p["old"]
        assert "short_output_threshold" not in p["old"]
    finally:
        dspark_api.reset_dspark_runtime()


@pytest.mark.asyncio
async def test_config_endpoint_rejects_empty_body(tmp_path, monkeypatch):
    """POST /dspark/config 空 body → 400 + 不落 audit（避免噪音）。"""
    from agent.llm.dspark import api as dspark_api
    monkeypatch.setenv("EAIDE_DSPARK_PERSIST_PATH", str(tmp_path / "dspark.json"))
    audit_db = tmp_path / "audit.sqlite"
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(audit_db))

    cfg = DSparkConfig()
    dspark_api.init_dspark_runtime(config=cfg, yaml_path=None, local_only_tasks=["intent"])
    try:
        body = dspark_api.DSparkConfigUpdateBody()  # 全 None
        with pytest.raises(Exception) as exc_info:
            await dspark_api.update_config(body)
        assert "no fields to update" in str(exc_info.value).lower() or exc_info.value.status_code == 400

        # 不应有任何 dspark audit 条目
        entries = await _read_audit_entries(audit_db)
        dspark_entries = [e for e in entries if e["action"] == "dspark_config_change"]
        assert len(dspark_entries) == 0
    finally:
        dspark_api.reset_dspark_runtime()
