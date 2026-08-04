"""rpa.click —— 点击当前页签中匹配选择器的元素。

安全措施：
  - 点击前验证页面仍然存在（防止 TOCTOU —— 其他协程可能已关闭页面）
  - 如果指定了 expected_domain，验证当前页面 URL 域名匹配（防止重定向攻击）
"""
from __future__ import annotations

from urllib.parse import urlparse

from mcp_server_rpa.browser import get_context, has_pages


async def run(args: dict) -> dict:
    ctx = await get_context()
    if not has_pages():
        return {"ok": False, "error": "没有打开的页面，请先调用 rpa.navigate"}
    try:
        page = ctx.pages[-1]
    except IndexError:
        return {"ok": False, "error": "页面已被关闭（TOCTOU：has_pages 之后页面被移除）"}

    # 域名验证（如果调用方指定了 expected_domain）
    expected_domain = args.get("expected_domain")
    if expected_domain:
        try:
            current_url = page.url
            actual_domain = urlparse(current_url).netloc
            if actual_domain != expected_domain:
                return {
                    "ok": False,
                    "error": f"页面域名不匹配：期望 {expected_domain}，实际 {actual_domain}（可能发生了重定向）",
                }
        except Exception:
            pass  # 无法获取 URL 时不强制校验

    try:
        await page.click(args["selector"])
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": f"点击失败: {exc}"}
