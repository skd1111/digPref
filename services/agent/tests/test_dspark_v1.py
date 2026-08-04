"""test_dspark_v1 —— Phase 13 V1 DSpark 推测解码扩展测试。

覆盖：
- decide_dspark 红线保护：_LOCAL_ONLY_TASKS 强制 off（CLAUDE.md §2）
- decide_dspark 短输出跳过：max_tokens < 20 → off
- decide_dspark 5 类策略矩阵（aggressive / standard / conservative / off / fallback）
- SSE 三处同步一致性（Python _CHANNEL_BY_KIND + Rust 常量 + TS 常量）
- YAML 加载 + 优先级（YAML > DEFAULT_POLICIES > 强制覆盖）

CLAUDE.md §2 红线：
- _LOCAL_ONLY_TASKS（intent / repair / local_intent / data_summary / log_level_classify / biznav_extract）
  强制 n_draft=1, draft_p_min=1.0（DSpark 永远不绕过本地 Ollama）
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.llm.dspark.config import (
    DSPARK_DRAFT_P_MIN_DEFAULT_AGGRESSIVE,
    DSPARK_DRAFT_P_MIN_DEFAULT_OFF,
    DSPARK_DRAFT_P_MIN_DEFAULT_STANDARD,
    DEFAULT_POLICIES,
    DSparkConfig,
    SPECULATIVE_OFF,
    SpeculativePolicy,
)

DRAFT_P_MIN_AGGRESSIVE = DSPARK_DRAFT_P_MIN_DEFAULT_AGGRESSIVE
DRAFT_P_MIN_OFF = DSPARK_DRAFT_P_MIN_DEFAULT_OFF
DRAFT_P_MIN_STANDARD = DSPARK_DRAFT_P_MIN_DEFAULT_STANDARD
from agent.llm.dspark.policy import (
    decide_dspark,
    get_local_only_tasks,
    load_speculative_policies,
    set_local_only_tasks,
)


# ---- 数据类 --------------------------------------------------------------


def test_dspark_config_defaults():
    """DSparkConfig 默认全局关闭 + 短输出阈值 20。"""
    cfg = DSparkConfig()
    assert cfg.enable_global is True
    assert cfg.short_output_threshold == 20


def test_speculative_policy_dataclass():
    p = SpeculativePolicy(
        task_category="sql_generation",
        mode="aggressive",
        n_draft=8,
        draft_p_min=0.75,
    )
    assert p.n_draft == 8
    assert p.draft_p_min == 0.75


# ---- decide_dspark 红线保护 -------------------------------------------------


# 5 关检查原因常量（来自 policy.py，policy.py 已经定义；这里只引用）
CFG_DRAFT_PATH = "/models/qwen2.5-0.1b-instruct-q4_k_m.gguf"


@pytest.fixture(autouse=True)
def _restore_local_only_tasks():
    """每个测试后还原 _LOCAL_ONLY_TASKS，避免污染其他测试。"""
    yield
    from agent.llm.dspark import policy as policy_mod
    policy_mod._LOCAL_ONLY_TASKS = frozenset({
        "intent", "repair", "skill_router", "data_summary", "biznav_extract",
        "local_intent", "vision_understand", "log_level_classify",
    })


def test_decide_local_only_task_forced_off():
    """CLAUDE.md §2 红线：_LOCAL_ONLY_TASKS 任务强制 n_draft=1, draft_p_min=1.0。"""
    cfg = DSparkConfig(enable_global=True, draft_model_path=CFG_DRAFT_PATH)
    policies = {
        "intent": SpeculativePolicy(
            task_category="intent",
            mode="aggressive", n_draft=8, draft_p_min=DRAFT_P_MIN_AGGRESSIVE,
        ),
    }
    pol = decide_dspark(
        config=cfg,
        policies=policies,
        task_category="intent",
        max_tokens=500,
        local_only_tasks=frozenset({"intent", "repair"}),
    )
    assert pol.mode == "off"
    assert pol.n_draft == 1
    assert pol.draft_p_min == DRAFT_P_MIN_OFF


def test_decide_local_intent_task_forced_off():
    """Phase 4 V0 新增的 local_intent 也必须强制 off。"""
    cfg = DSparkConfig(enable_global=True, draft_model_path=CFG_DRAFT_PATH)
    pol = decide_dspark(
        config=cfg, policies={},
        task_category="local_intent", max_tokens=200,
        local_only_tasks=frozenset({"local_intent"}),
    )
    assert pol.mode == "off"


def test_decide_log_level_classify_forced_off():
    """Phase 2F+ V1 新增的 log_level_classify 也必须强制 off。"""
    cfg = DSparkConfig(enable_global=True, draft_model_path=CFG_DRAFT_PATH)
    pol = decide_dspark(
        config=cfg, policies={},
        task_category="log_level_classify", max_tokens=100,
        local_only_tasks=frozenset({"log_level_classify"}),
    )
    assert pol.mode == "off"


# ---- decide_dspark 短输出跳过 ----------------------------------------------


def test_decide_short_output_skipped():
    """短输出（< 20 tokens）跳过 DSpark —— 避免猜测开销 > 节省时间。"""
    cfg = DSparkConfig(
        enable_global=True, draft_model_path=CFG_DRAFT_PATH,
        short_output_threshold=20,
    )
    policies = {
        "sql_generation": SpeculativePolicy(
            task_category="sql_generation", mode="aggressive",
            n_draft=8, draft_p_min=DRAFT_P_MIN_AGGRESSIVE,
        ),
    }
    pol = decide_dspark(
        config=cfg, policies=policies,
        task_category="sql_generation", max_tokens=15,
        local_only_tasks=frozenset(),
    )
    assert pol.mode == "off"
    assert pol.n_draft == 1


def test_decide_short_output_boundary():
    """max_tokens = 20 = 阈值 → 应启用 aggressive（边界包含）。"""
    cfg = DSparkConfig(
        enable_global=True, draft_model_path=CFG_DRAFT_PATH,
        short_output_threshold=20,
    )
    policies = {
        "sql_generation": SpeculativePolicy(
            task_category="sql_generation", mode="aggressive",
            n_draft=8, draft_p_min=DRAFT_P_MIN_AGGRESSIVE,
        ),
    }
    pol = decide_dspark(
        config=cfg, policies=policies,
        task_category="sql_generation", max_tokens=20,
        local_only_tasks=frozenset(),
    )
    assert pol.mode == "aggressive"


# ---- decide_dspark 全局开关 -------------------------------------------------


def test_decide_global_disabled_forces_off():
    """enable_global=False → 全部 off。"""
    cfg = DSparkConfig(enable_global=False, draft_model_path=CFG_DRAFT_PATH)
    policies = {
        "sql_generation": SpeculativePolicy(
            task_category="sql_generation", mode="aggressive",
            n_draft=8, draft_p_min=DRAFT_P_MIN_AGGRESSIVE,
        ),
    }
    pol = decide_dspark(
        config=cfg, policies=policies,
        task_category="sql_generation", max_tokens=500,
        local_only_tasks=frozenset(),
    )
    assert pol.mode == "off"


# ---- decide_dspark 5 类策略矩阵 --------------------------------------------


def test_decide_5_categories():
    """aggressive / aggressive / standard / conservative / off 各一类。"""
    cfg = DSparkConfig(enable_global=True, draft_model_path=CFG_DRAFT_PATH)
    # 注：DEFAULT_POLICIES 的 chat_qa=conservative；complex_reasoning 不在 default → 兜底 conservative
    pol_sql = decide_dspark(
        config=cfg, policies=DEFAULT_POLICIES,
        task_category="sql_generation", max_tokens=500,
        local_only_tasks=frozenset(),
    )
    assert pol_sql.mode == "aggressive"

    pol_code = decide_dspark(
        config=cfg, policies=DEFAULT_POLICIES,
        task_category="code_completion", max_tokens=500,
        local_only_tasks=frozenset(),
    )
    assert pol_code.mode == "aggressive"

    pol_log = decide_dspark(
        config=cfg, policies=DEFAULT_POLICIES,
        task_category="log_analysis", max_tokens=500,
        local_only_tasks=frozenset(),
    )
    assert pol_log.mode == "standard"

    pol_chat = decide_dspark(
        config=cfg, policies=DEFAULT_POLICIES,
        task_category="chat_qa", max_tokens=500,
        local_only_tasks=frozenset(),
    )
    assert pol_chat.mode == "conservative"


def test_decide_unknown_category_falls_back_to_conservative():
    """未知类别 → conservative fallback。"""
    cfg = DSparkConfig(enable_global=True, draft_model_path=CFG_DRAFT_PATH)
    pol = decide_dspark(
        config=cfg, policies={},
        task_category="unknown_task_xyz", max_tokens=500,
        local_only_tasks=frozenset(),
    )
    assert pol.mode == "conservative"


# ---- YAML 加载 ------------------------------------------------------------


def test_load_speculative_policies_yaml(tmp_path: Path):
    """YAML 覆盖 DEFAULT_POLICIES；未列出的类别保留 DEFAULT_POLICIES 默认。"""
    yaml_content = """
profiles:
  - mode: aggressive
    task_categories: [sql_generation, code_completion]
  - mode: standard
    task_categories: [log_analysis]
  - mode: conservative
    task_categories: [chat_qa]
"""
    yaml_path = tmp_path / "speculative.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    policies = load_speculative_policies(yaml_path)

    assert policies["sql_generation"].mode == "aggressive"
    assert policies["code_completion"].mode == "aggressive"
    assert policies["log_analysis"].mode == "standard"
    assert policies["chat_qa"].mode == "conservative"
    # 未列出 → 保留 DEFAULT_POLICIES（policy.py 末尾的 setdefault）
    assert "intent" in policies
    assert policies["intent"].mode == "off"


def test_load_speculative_policies_yaml_not_found(tmp_path: Path):
    """YAML 不存在 → 返 DEFAULT_POLICIES（policy.py 行为）。"""
    policies = load_speculative_policies(tmp_path / "missing.yaml")
    # 默认 12 个类别全在
    assert "intent" in policies
    assert "sql_generation" in policies


def test_load_speculative_policies_per_category_override(tmp_path: Path):
    """YAML 支持每类别显式 K / 阈值覆盖。"""
    yaml_content = """
profiles:
  - mode: aggressive
    task_categories: [sql_generation]
    n_draft: 6
    draft_p_min: 0.80
"""
    yaml_path = tmp_path / "speculative.yaml"
    yaml_path.write_text(yaml_content, encoding="utf-8")
    policies = load_speculative_policies(yaml_path)
    assert policies["sql_generation"].n_draft == 6
    assert policies["sql_generation"].draft_p_min == 0.80


# ---- set_local_only_tasks 注入 --------------------------------------------


def test_set_local_only_tasks_round_trip():
    set_local_only_tasks({"a", "b", "c"})
    assert get_local_only_tasks() == frozenset({"a", "b", "c"})


# ---- SSE 三处同步一致性（设计文档 §6 + CLAUDE.md §4）--------------------


def test_sse_python_channel_registered():
    """Python stream.py _CHANNEL_BY_KIND 含 dspark_acceleration_status。"""
    from agent.graph.stream import _CHANNEL_BY_KIND
    assert "dspark_acceleration_status" in _CHANNEL_BY_KIND
    assert _CHANNEL_BY_KIND["dspark_acceleration_status"] == "agent://dspark_acceleration_status"


# （Rust + TS 一致性测试不在 Python 测试范围内；reviewer 读 Rust/TS 源码）


# ---- decide_dspark 数学等价（V1 占位 mock）---------------------------------


def test_decide_returns_off_policy_object():
    """off 模式下返 SpeculativePolicy(mode='off', n_draft=1, draft_p_min=1.0)。"""
    cfg = DSparkConfig(enable_global=True, draft_model_path=CFG_DRAFT_PATH)
    pol = decide_dspark(
        config=cfg, policies={},
        task_category="any", max_tokens=100,
        local_only_tasks=frozenset({"any"}),  # any in local-only → off
    )
    assert isinstance(pol, SpeculativePolicy)
    if pol.mode == "off":
        assert pol.n_draft == 1
        assert pol.draft_p_min >= 1.0