"""Office 文件预览（OfficeCLI 渲染引擎 → HTML/PNG）。"""

from agent.office_preview.api import router as office_preview_router

__all__ = ["office_preview_router"]
