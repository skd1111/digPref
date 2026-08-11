"""Phase 7 V0 · 数字水印 + PII 脱敏调用。

安全红线（design §6）：
  - 导出文件嵌入隐形水印（操作人 + 时间 + IP），泄露可溯源
  - 导出前必过 Phase 4 PII 脱敏引擎（卡号/身份证/手机/金额形态）
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

from agent.config import settings


def generate_watermark_text(
    operator: str = "current_user",
    ip: str = "127.0.0.1",
) -> str:
    """生成水印文本（操作人 + 时间 + IP）。

    Args:
        operator: 操作人。
        ip: 客户端 IP。

    Returns:
        水印文本字符串。
    """
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"EAIDE | {operator} | {ts} | {ip}"


def embed_watermark_metadata(
    export_meta: dict[str, Any], operator: str = "current_user"
) -> dict[str, Any]:
    """在导出元数据中嵌入水印信息。

    Args:
        export_meta: 导出元数据字典。
        operator: 操作人。

    Returns:
        追加水印字段后的元数据。
    """
    if not settings.data_export_watermark:
        return export_meta

    export_meta["watermark"] = generate_watermark_text(operator)
    export_meta["watermark_hash"] = hashlib.sha256(export_meta["watermark"].encode()).hexdigest()[
        :16
    ]
    return export_meta


def mask_pii_columns(columns: list[str], rows: list[list[Any]]) -> list[list[Any]]:
    """PII 脱敏（V0 简化版：按列名关键字检测）。

    生产环境应调用 Phase 4 PII 脱敏引擎（更精确的正则 + NER）。
    V0：按列名包含 'phone'/'mobile'/'id_card'/'card_no' 等关键字做简单遮罩。

    Args:
        columns: 列名列表。
        rows: 数据行。

    Returns:
        脱敏后的数据行。
    """
    if not settings.data_require_mask_on_export:
        return rows

    pii_keywords = (
        "phone",
        "mobile",
        "id_card",
        "idcard",
        "card_no",
        "bank_card",
        "手机",
        "身份证",
        "卡号",
        "电话",
    )

    # 找出需要脱敏的列索引
    mask_indices: set[int] = set()
    for i, col in enumerate(columns):
        col_lower = col.lower()
        if any(kw in col_lower for kw in pii_keywords):
            mask_indices.add(i)

    if not mask_indices:
        return rows

    # 脱敏：保留前 3 后 4，中间用 * 替代
    masked_rows: list[list[Any]] = []
    for row in rows:
        new_row = list(row)
        for idx in mask_indices:
            if idx < len(new_row):
                new_row[idx] = _mask_value(str(new_row[idx]))
        masked_rows.append(new_row)

    return masked_rows


def _mask_value(val: str) -> str:
    """遮罩单个值：保留前 3 后 4，中间 *。"""
    if len(val) <= 7:
        return val[:1] + "*" * (len(val) - 1)
    return val[:3] + "*" * (len(val) - 7) + val[-4:]
