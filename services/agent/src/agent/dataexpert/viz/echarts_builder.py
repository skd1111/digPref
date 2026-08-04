"""Phase 7 V0 · ECharts 配置生成器 —— 生成前端 ECharts option JSON。"""
from __future__ import annotations

from typing import Any


def build_echarts_option(
    chart_type: str,
    columns: list[str],
    rows: list[list[Any]],
    x_index: int = 0,
    y_index: int = 1,
) -> dict[str, Any]:
    """生成 ECharts option 配置。

    Args:
        chart_type: 图表类型（bar/line/pie/scatter）。
        columns: 列名。
        rows: 数据行。
        x_index: X 轴列索引。
        y_index: Y 轴列索引。

    Returns:
        ECharts option dict（可直接 JSON 序列化传给前端）。
    """
    x_data = [str(row[x_index]) if x_index < len(row) else "" for row in rows]
    y_data = [_to_num(row[y_index]) if y_index < len(row) else 0 for row in rows]
    x_name = columns[x_index] if x_index < len(columns) else ""
    y_name = columns[y_index] if y_index < len(columns) else ""

    base: dict[str, Any] = {
        "tooltip": {"trigger": "axis" if chart_type != "pie" else "item"},
        "grid": {"left": "3%", "right": "4%", "bottom": "3%", "containLabel": True},
    }

    if chart_type == "pie":
        pie_data = [{"name": x, "value": y} for x, y in zip(x_data, y_data)]
        base.update({
            "series": [{
                "type": "pie",
                "radius": "60%",
                "data": pie_data,
                "emphasis": {"itemStyle": {"shadowBlur": 10}},
            }],
        })
    elif chart_type == "scatter":
        scatter_data = [[x, y] for x, y in zip(x_data, y_data)]
        base.update({
            "xAxis": {"type": "value", "name": x_name},
            "yAxis": {"type": "value", "name": y_name},
            "series": [{"type": "scatter", "data": scatter_data}],
        })
    else:
        # bar / line
        base.update({
            "xAxis": {"type": "category", "data": x_data, "name": x_name},
            "yAxis": {"type": "value", "name": y_name},
            "series": [{
                "type": chart_type,
                "data": y_data,
                "itemStyle": {"borderRadius": [2, 2, 0, 0]} if chart_type == "bar" else {},
            }],
        })

    return base


def _to_num(val: Any) -> float | int:
    """安全转数值。"""
    try:
        if isinstance(val, (int, float)):
            return val
        return float(val)
    except (TypeError, ValueError):
        return 0
