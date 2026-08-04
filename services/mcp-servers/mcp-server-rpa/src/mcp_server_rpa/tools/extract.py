"""rpa.extract —— 从当前页签提取文本 / HTML / 属性。"""
from __future__ import annotations

from mcp_server_rpa.browser import get_context, has_pages


async def run(args: dict) -> dict:
    ctx = await get_context()
    if not has_pages():
        return {"ok": False, "error": "没有打开的页面，请先调用 rpa.navigate"}

    page = ctx.pages[-1]
    selector = args["selector"]
    mode = args.get("mode", "text")

    if mode == "text":
        data = await page.locator(selector).inner_text()
    elif mode == "html":
        data = await page.locator(selector).inner_html()
    elif mode == "attr":
        # 明确校验 attr 参数是否存在（之前直接 KeyError）
        attr_name = args.get("attr")
        if not attr_name:
            return {"ok": False, "error": "mode='attr' 需要提供 'attr' 参数（属性名）"}
        data = await page.locator(selector).get_attribute(attr_name)
        if data is None:
            return {"ok": False, "error": f"选择器 {selector!r} 没有属性 {attr_name!r}"}
    else:
        return {"ok": False, "error": f"未知的提取模式: {mode!r}，支持: text, html, attr"}

    return {"ok": True, "data": data[:8000], "truncated": len(str(data)) > 8000}
