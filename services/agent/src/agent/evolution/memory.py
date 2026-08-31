"""evolution.memory —— 经验检索与注入（L1，设计文档 §3.3）。

注入通道：`tools/loop.py::_merge_extra_rules` 的 EXTRA_RULES 通道
（与双模式纪律 / 活跃 skill 规范并列，各自非空才拼）。

同步实现：工具循环编排是同步组装提示词，检索用同步 sqlite3 只读
小查询（与 llm/router.py::load_enabled_local_backend 同风格），
失败返空串绝不阻塞任务执行。
"""

from __future__ import annotations

from typing import Any

from agent.config import settings
from agent.evolution import storage


def experience_addon(state: dict[str, Any]) -> str:
    """按当前任务上下文检索经验并拼成注入片段。

    上下文取自图状态：`intent_analysis.intent_category`（细分类型）+
    `active_skill_id`。开关关闭 / 无经验 / 检索失败 → 返空串（不注入）。
    """
    if not settings.evolution_enabled:
        return ""
    analysis = state.get("intent_analysis")
    category = ""
    if isinstance(analysis, dict):
        category = str(analysis.get("intent_category") or "")
    skill_id = str(state.get("active_skill_id") or "")
    experiences = storage.retrieve_experiences_sync(category, skill_id)
    return storage.format_experience_snippet(experiences)
