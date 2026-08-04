"""Phase 7 V0 · CSV 导出 —— UTF-8-BOM（防 Excel 中文乱码）。"""
from __future__ import annotations

import csv
import hashlib
import tempfile
from pathlib import Path
from typing import Any

from agent.dataexpert.export.watermark import embed_watermark_metadata, mask_pii_columns


def export_csv(
    columns: list[str],
    rows: list[list[Any]],
    *,
    title: str = "数据报表",
    operator: str = "current_user",
    output_path: str | None = None,
) -> dict[str, Any]:
    """导出 CSV 文件（UTF-8-BOM，防 Excel 中文乱码）。

    Args:
        columns: 列名。
        rows: 数据行。
        title: 报表标题。
        operator: 操作人（水印用）。
        output_path: 输出路径（None 则用临时文件）。

    Returns:
        {"path": str, "md5": str, "row_count": int, "watermark": str}
    """
    # PII 脱敏
    masked_rows = mask_pii_columns(columns, rows)

    if output_path is None:
        output_path = str(Path(tempfile.gettempdir()) / f"eaide_export_{title}.csv")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # UTF-8-BOM 写入（Excel 打开不乱码）
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(columns)
        for row in masked_rows:
            writer.writerow(row)

    # 计算 MD5
    file_bytes = Path(output_path).read_bytes()
    md5 = hashlib.md5(file_bytes).hexdigest()

    # 水印元数据
    meta = embed_watermark_metadata({
        "path": output_path,
        "md5": md5,
        "row_count": len(masked_rows),
        "format": "csv",
    }, operator)

    return meta
