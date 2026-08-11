"""expert_teams —— 专家团资产（系统一等资产，不以 Skill 形式存在）。

定位：多个虚拟专家角色组成的智能体工作流资产（如尽职调查专家团）。
- 团级：id / name / 适用场景 / 触发关键词
- 成员级：名称 / 角色定位 / 职责 / 关注点 / 输出 / 独立 prompt

存储：%APPDATA%\\eaide\\expert_teams\\*.yaml（与 skills 同模式：
启动一次扫描 + 导入触发 load_one()，无 watchdog）。
"""

from __future__ import annotations

from agent.expert_teams.models import ExpertMember, ExpertTeam
from agent.expert_teams.schema import validate_expert_team_yaml, validate_no_dsn

__all__ = [
    "ExpertMember",
    "ExpertTeam",
    "validate_expert_team_yaml",
    "validate_no_dsn",
]
