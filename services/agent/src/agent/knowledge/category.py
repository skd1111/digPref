"""knowledge.category —— 从数据字典读取知识库分类类型（实时生效）。

分类类型存储在 dict_items（dict.db）中，category='知识库分类' 分组。
每次调用实时查库，无缓存；dict.db 不可用 / 无分类条目 → 返回空列表。

用途：
- ingestion._auto_classify 构造 LLM 分类 prompt
- 前端 KbPanel 动态渲染上传/筛选下拉
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_kb_categories() -> list[dict[str, Any]]:
    """从数据字典读取知识库分类类型（实时生效）。

    返回 ``[{"key": "finance", "label": "财务/财税", "description": "..."}]``。
    dict.db 不可用或无分类条目 → 返回空列表（best-effort，不抛异常）。
    """
    try:
        from agent.datadict.storage import DictStorage
        from agent.paths import data_root

        db_path = str(data_root() / "dict.db")
        storage = DictStorage(db_path)
        items = storage.list(category="知识库分类")
        return [
            {
                "key": item.key,
                "label": item.label,
                "value": item.value,
                "description": item.description,
            }
            for item in items
        ]
    except Exception as exc:
        logger.debug("get_kb_categories failed: %s", exc)
        return []
