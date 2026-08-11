"""ops —— 运营工作台业务记录（Phase 2H）。

业务记录卡片：做完一笔业务后，AI 根据会话 + 功能点 + Skill 经验
自动生成可审计的小卡片，供事后检查与统计（统计报表由数据专家模式承接）。
"""

from .models import BusinessRecord

__all__ = ["BusinessRecord"]
