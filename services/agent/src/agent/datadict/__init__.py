"""dict —— 数据字典（Phase 2H）。

公共参数单独维护（不进 Skill）：Skill 里写明「需要哪些公共参数，去对应字典查」。
例如授权有效期、大额现金限额、开户必要资料等。
"""

from .models import DictItem

__all__ = ["DictItem"]
