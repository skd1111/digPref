"""Skills module —— Phase 2D 业务功能点模板（MCP tool 集 + system_prompt + few-shot）。

加载模式：启动一次扫描 + UI 导入触发 load_one()（无 watchdog）。
"""

# V0: 不在 __init__ 一次性 import 全部子模块，避免循环依赖。
# 测试 / 调用方按需 import 具体子模块。
from agent.skills.models import FewShotExample, Skill, SkillRoutingResult
from agent.skills.schema import validate_no_dsn, validate_skill_yaml

__all__ = [
    "FewShotExample",
    "Skill",
    "SkillRoutingResult",
    "validate_no_dsn",
    "validate_skill_yaml",
]
