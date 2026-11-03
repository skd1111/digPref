"""knowledge.entity_types —— 从数据字典读取实体类型（实时生效）。

实体类型存储在 dict_items（dict.db）中，category='实体类型' 分组。
每次调用实时查库，无缓存；dict.db 不可用 / 无条目 → 返回空列表。

用途：
- ingestion._extract_entities 构造 LLM 实体抽取 prompt
- 前端按类型分组展示实体标签
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def get_entity_types() -> list[dict[str, Any]]:
    """从数据字典读取实体类型（实时生效）。

    返回 ``[{"key": "person", "label": "人名", "description": "..."}]``。
    dict.db 不可用或无条目 → 返回空列表（best-effort，不抛异常）。
    """
    try:
        from agent.datadict.storage import DictStorage
        from agent.paths import data_root

        db_path = str(data_root() / "dict.db")
        storage = DictStorage(db_path)
        items = storage.list(category="实体类型")
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
        logger.debug("get_entity_types failed: %s", exc)
        return []
