"""Phase 7 V0 · PDF 报表导出 —— Jinja2 HTML 模板 + WeasyPrint + 水印。

design §4.3：
  - Jinja2 HTML 模板 + WeasyPrint（前端 HTML/CSS 画样式，后端渲染 PDF）
  - 支持 Logo / 页眉页脚 / 数据表 / 图表截图 / 数字水印
  - 符合金融监管格式

降级策略：WeasyPrint 不可用时降级为 HTML 文件输出。
"""
from __future__ import annotations

import hashlib
import tempfile
import time
from pathlib import Path
from typing import Any

from agent.dataexpert.export.watermark import embed_watermark_metadata, mask_pii_columns


# PDF HTML 模板（Jinja2）
_PDF_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body { font-family: "Microsoft YaHei", "SimHei", sans-serif; margin: 40px; color: #333; }
  h1 { text-align: center; color: #2F5496; font-size: 20px; border-bottom: 2px solid #2F5496; padding-bottom: 10px; }
  .meta { text-align: right; font-size: 11px; color: #888; margin-bottom: 20px; }
  table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 16px; }
  th { background: #2F5496; color: #fff; padding: 8px 6px; text-align: left; }
  td { padding: 6px; border-bottom: 1px solid #ddd; }
  tr:nth-child(even) { background: #f5f7fa; }
  .negative { color: #e53e3e; font-weight: bold; }
  .watermark { position: fixed; bottom: 10px; right: 10px; font-size: 9px; color: #ccc; }
  .footer { margin-top: 30px; font-size: 10px; color: #999; text-align: center; border-top: 1px solid #ddd; padding-top: 8px; }
</style>
</head>
<body>
  <h1>{{ title }}</h1>
  <div class="meta">生成时间：{{ generated_at }} | 操作人：{{ operator }} | 共 {{ row_count }} 行</div>
  <table>
    <thead><tr>{% for col in columns %}<th>{{ col }}</th>{% endfor %}</tr></thead>
    <tbody>
    {% for row in rows %}
      <tr>{% for cell in row %}<td{% if cell is number and cell < 0 %} class="negative"{% endif %}>{{ cell }}</td>{% endfor %}</tr>
    {% endfor %}
    </tbody>
  </table>
  <div class="watermark">{{ watermark }}</div>
  <div class="footer">Enterprise AI IDE · 数据专家报表 · 机密文件，请勿外传</div>
</body>
</html>"""


def export_pdf(
    columns: list[str],
    rows: list[list[Any]],
    *,
    title: str = "数据报表",
    operator: str = "current_user",
    output_path: str | None = None,
) -> dict[str, Any]:
    """导出 PDF 报表。

    Args:
        columns: 列名。
        rows: 数据行。
        title: 报表标题。
        operator: 操作人。
        output_path: 输出路径。

    Returns:
        {"path": str, "md5": str, "row_count": int, "watermark": str}
    """
    # PII 脱敏
    masked_rows = mask_pii_columns(columns, rows)

    # 水印
    from agent.dataexpert.export.watermark import generate_watermark_text
    watermark_text = generate_watermark_text(operator)

    # 渲染 HTML
    html_content = _render_html(columns, masked_rows, title, operator, watermark_text)

    if output_path is None:
        output_path = str(Path(tempfile.gettempdir()) / f"eaide_export_{title}.pdf")
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    # 尝试 WeasyPrint 渲染 PDF
    try:
        from weasyprint import HTML
        HTML(string=html_content).write_pdf(output_path)
        fmt = "pdf"
    except ImportError:
        # WeasyPrint 不可用 → 降级为 HTML 文件
        output_path = output_path.replace(".pdf", ".html")
        Path(output_path).write_text(html_content, encoding="utf-8")
        fmt = "html (WeasyPrint 不可用，降级)"

    # 计算 MD5
    file_bytes = Path(output_path).read_bytes()
    md5 = hashlib.md5(file_bytes).hexdigest()

    meta = embed_watermark_metadata({
        "path": output_path,
        "md5": md5,
        "row_count": len(masked_rows),
        "format": fmt,
    }, operator)

    return meta


def _render_html(
    columns: list[str],
    rows: list[list[Any]],
    title: str,
    operator: str,
    watermark: str,
) -> str:
    """渲染 HTML（V0 简化版，不依赖 Jinja2 也能工作）。"""
    try:
        from jinja2 import Template
        tmpl = Template(_PDF_TEMPLATE)
        return tmpl.render(
            title=title,
            columns=columns,
            rows=rows,
            operator=operator,
            generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            row_count=len(rows),
            watermark=watermark,
        )
    except ImportError:
        # Jinja2 不可用 → 手动拼接
        return _manual_html(columns, rows, title, operator, watermark)


def _manual_html(
    columns: list[str], rows: list[list[Any]],
    title: str, operator: str, watermark: str,
) -> str:
    """手动拼接 HTML（Jinja2 不可用时的兜底）。"""
    th = "".join(f"<th>{c}</th>" for c in columns)
    trs = []
    for row in rows:
        tds = "".join(f"<td>{v}</td>" for v in row)
        trs.append(f"<tr>{tds}</tr>")
    tbody = "\n".join(trs)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>body{{font-family:sans-serif;margin:40px}}table{{width:100%;border-collapse:collapse}}
th{{background:#2F5496;color:#fff;padding:8px}}td{{padding:6px;border-bottom:1px solid #ddd}}</style>
</head><body><h1>{title}</h1><p>生成时间：{ts} | 操作人：{operator} | 共 {len(rows)} 行</p>
<table><thead><tr>{th}</tr></thead><tbody>{tbody}</tbody></table>
<div style="font-size:9px;color:#ccc">{watermark}</div></body></html>"""
