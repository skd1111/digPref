"""rpa.navigate —— 在新页签中打开 URL。

每次导航创建新页签，但限制最大页签数量，超出时关闭最旧的页签。
"""

from __future__ import annotations

from mcp_server_rpa.browser import get_context
from mcp_server_rpa.config import Settings
from mcp_server_rpa.safety.domain_whitelist import assert_allowed

# 最大保持打开的页签数量（防止内存无限增长）
_MAX_PAGES = 10


async def run(args: dict) -> dict:
    s = Settings()
    url = args["url"]

    # 安全校验：域名白名单检查
    assert_allowed(url)

    ctx = await get_context()

    # 限制页签数量：超过上限时关闭最旧的页签
    if len(ctx.pages) >= _MAX_PAGES:
        oldest = ctx.pages[0]
        # 不关闭最后一个页签（Playwright 要求至少一个页签打开）
        if len(ctx.pages) > 1:
            await oldest.close()

    page = await ctx.new_page()
    await page.goto(url, timeout=s.tool_timeout_sec * 1000)
    return {
        "ok": True,
        "url": page.url,
        "title": await page.title(),
    }
