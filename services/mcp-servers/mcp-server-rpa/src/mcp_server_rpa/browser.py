"""Playwright 浏览器封装 —— 单实例、多页签、线程安全。

借鉴 VSCode 的 ExtensionHost 进程模型：
    - 浏览器实例在进程生命周期内保持存活
    - 使用 asyncio.Lock 保护全局状态，避免并发工具调用竞态
    - 支持优雅关闭（server shutdown 时调用 cleanup()）
"""

from __future__ import annotations

import asyncio

from playwright.async_api import Browser, BrowserContext, async_playwright

from mcp_server_rpa.config import Settings

# ---- 全局状态（受 _LOCK 保护）------------------------------------------------
_browser: Browser | None = None
_ctx: BrowserContext | None = None
_LOCK = asyncio.Lock()


async def get_context() -> BrowserContext:
    """获取或创建共享的浏览器上下文（线程安全）。

    首次调用时启动 Chromium，应用配置的 user_agent。
    后续调用复用同一实例。
    """
    global _browser, _ctx
    async with _LOCK:
        if _ctx is None:
            s = Settings()
            pw = await async_playwright().start()
            _browser = await pw.chromium.launch(headless=True)
            # 应用配置的 User-Agent（之前从未被传递）
            _ctx = await _browser.new_context(
                user_agent=s.user_agent,
                viewport={"width": 1280, "height": 720},
            )
        return _ctx


async def cleanup() -> None:
    """关闭浏览器实例，释放所有资源。

    由 MCP 服务器 shutdown 时调用。
    """
    global _browser, _ctx
    async with _LOCK:
        if _browser is not None:
            await _browser.close()
            _browser = None
            _ctx = None


def has_pages() -> bool:
    """检查浏览器是否有可用的页签（供工具调用前校验）。"""
    return _ctx is not None and len(_ctx.pages) > 0
