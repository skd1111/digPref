"""Phase 7 V0 · 图表类型推荐 —— 按数据特征自动推荐。

安全红线（CLAUDE.md §2）：
  - 走 LMRouter task='chart_reco'（_LOCAL_ONLY_TASKS，强制本地）
  - 图表推荐会看数据样本（可能含 PII），永不出云

推荐规则（design §4.3）：
  - 时间序列 → 折线图
  - 分类对比 → 柱状图
  - 占比 → 饼图
  - 相关性 → 散点图
"""

from __future__ import annotations

from typing import Any


def recommend_chart(
    columns: list[str],
    dtypes: list[str],
    row_count: int,
    *,
    sample_values: dict[str, list] | None = None,
) -> dict[str, Any]:
    """按数据特征推荐图表类型。

    V0：基于规则（列类型 + 行数）。V1：走 LMRouter task='chart_reco'（本地）。

    Args:
        columns: 列名列表。
        dtypes: 列类型列表（与 columns 对齐）。
        row_count: 行数。
        sample_values: 样本值（可选，用于更精确推荐）。

    Returns:
        {"chart_type": "bar"|"line"|"pie"|"scatter",
         "x_index": int, "y_index": int, "reason": str}
    """
    if not columns or len(columns) < 2:
        return {"chart_type": "bar", "x_index": 0, "y_index": 0, "reason": "数据不足，默认柱状图"}

    # 找第一个数值列作为 Y 轴
    y_index = -1
    for i, dt in enumerate(dtypes):
        if _is_numeric_dtype(dt):
            y_index = i
            break
    if y_index < 0:
        y_index = 1 if len(columns) > 1 else 0

    # X 轴：第一个非数值列（分类/时间）
    x_index = 0
    for i, dt in enumerate(dtypes):
        if not _is_numeric_dtype(dt) and i != y_index:
            x_index = i
            break

    # 推荐逻辑
    x_dtype = dtypes[x_index] if x_index < len(dtypes) else "object"

    # 时间序列 → 折线
    if _is_time_dtype(x_dtype) or _looks_like_time(columns[x_index]):
        return {
            "chart_type": "line",
            "x_index": x_index,
            "y_index": y_index,
            "reason": f"X 轴 '{columns[x_index]}' 为时间类型 → 折线图",
        }

    # 分类少（≤ 8 个）→ 柱状图
    if row_count <= 8:
        return {
            "chart_type": "bar",
            "x_index": x_index,
            "y_index": y_index,
            "reason": f"分类数 {row_count} ≤ 8 → 柱状图",
        }

    # 占比场景（列名含"率"/"比"/"占比"）→ 饼图
    y_name = columns[y_index].lower()
    if any(kw in y_name for kw in ("率", "比", "占比", "percent", "ratio", "rate")):
        if row_count <= 10:
            return {
                "chart_type": "pie",
                "x_index": x_index,
                "y_index": y_index,
                "reason": f"Y 轴 '{columns[y_index]}' 含占比语义 → 饼图",
            }

    # 两个数值列 → 散点图
    numeric_count = sum(1 for dt in dtypes if _is_numeric_dtype(dt))
    if numeric_count >= 2 and row_count > 8:
        # 找第二个数值列
        y2 = -1
        for i, dt in enumerate(dtypes):
            if _is_numeric_dtype(dt) and i != y_index:
                y2 = i
                break
        if y2 >= 0:
            return {
                "chart_type": "scatter",
                "x_index": y_index,
                "y_index": y2,
                "reason": "多数值列 + 大行数 → 散点图（相关性分析）",
            }

    # 默认柱状图
    return {
        "chart_type": "bar",
        "x_index": x_index,
        "y_index": y_index,
        "reason": "默认推荐柱状图",
    }


def _is_numeric_dtype(dt: str) -> bool:
    dt_lower = dt.lower()
    return any(k in dt_lower for k in ("int", "float", "decimal", "number", "double", "numeric"))


def _is_time_dtype(dt: str) -> bool:
    dt_lower = dt.lower()
    return any(k in dt_lower for k in ("date", "time", "timestamp"))


def _looks_like_time(col_name: str) -> bool:
    name_lower = col_name.lower()
    return any(
        kw in name_lower for kw in ("date", "time", "month", "year", "day", "日期", "时间", "月")
    )
