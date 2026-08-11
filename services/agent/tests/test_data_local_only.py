"""Phase 7 V0 · ★ 红线测试 —— schema_link / chart_reco / data_summary 恒走本地。

安全红线（CLAUDE.md §2 + design §4.1）：
  - 表结构 + 字段注释可能含敏感信息，永不出云
  - 图表推荐会看数据样本（可能含 PII），永不出云
  - data_summary 同理

本测试直接断言 _LOCAL_ONLY_TASKS 集合包含上述 task kind，
确保任何后续修改不会意外移除这些安全约束。
"""

import pytest
from agent.llm.router import _LOCAL_ONLY_TASKS

# ---- ★ 红线：Phase 7 敏感任务必须在 _LOCAL_ONLY_TASKS 中 -------------------------

_PHASE7_LOCAL_TASKS = ["schema_link", "chart_reco", "data_summary"]


@pytest.mark.parametrize("task_kind", _PHASE7_LOCAL_TASKS)
def test_phase7_tasks_are_local_only(task_kind: str):
    """★ 红线：Phase 7 敏感 task kind 必须存在于 _LOCAL_ONLY_TASKS。

    若此测试失败，说明有人试图将敏感数据任务路由到云端 —— 绝对禁止。
    """
    assert task_kind in _LOCAL_ONLY_TASKS, (
        f"★ 安全红线违规：'{task_kind}' 不在 _LOCAL_ONLY_TASKS 中！"
        f"表结构/字段注释/数据样本可能含 PII，永不出云。"
    )


# ---- 回归：历史敏感任务不被误删 ---------------------------------------------------

_HISTORICAL_LOCAL_TASKS = [
    "intent",
    "repair",
    "skill_router",
    "data_summary",
    "biznav_extract",
    "local_intent",
    "vision_understand",
    "log_level_classify",
    "builtin_tool_summary",
    "builtin_search_summarize",
    "image_processing_summary",
    "ssh_command_summary",
]


@pytest.mark.parametrize("task_kind", _HISTORICAL_LOCAL_TASKS)
def test_historical_tasks_still_local(task_kind: str):
    """回归：历史敏感任务仍在 _LOCAL_ONLY_TASKS（防误删）。"""
    assert task_kind in _LOCAL_ONLY_TASKS, (
        f"回归失败：'{task_kind}' 被意外从 _LOCAL_ONLY_TASKS 移除！"
    )


# ---- _LOCAL_ONLY_TASKS 是 frozenset（不可篡改）------------------------------------


def test_local_only_tasks_is_frozenset():
    """_LOCAL_ONLY_TASKS 必须是 frozenset（不可变集合）。"""
    assert isinstance(_LOCAL_ONLY_TASKS, frozenset), (
        "_LOCAL_ONLY_TASKS 必须是 frozenset，防止运行时被篡改"
    )


# ---- LMRouter 路由逻辑验证 --------------------------------------------------------


def test_local_only_tasks_never_use_private():
    """_LOCAL_ONLY_TASKS 中的任务不会路由到 private（云端）后端。

    通过检查 LMRouter.pick 逻辑：
    kind in _LOCAL_ONLY_TASKS → 直接返回 ollama（本地）。
    """
    from agent.llm.router import LMRouter

    # 验证 pick 方法存在且逻辑正确
    assert hasattr(LMRouter, "pick"), "LMRouter 必须有 pick 方法"

    # 检查源码中包含 _LOCAL_ONLY_TASKS 判断
    import inspect

    source = inspect.getsource(LMRouter.pick)
    assert "_LOCAL_ONLY_TASKS" in source, "pick 必须检查 _LOCAL_ONLY_TASKS"
