"""Phase 15 V0 · 前端实时预览引擎 —— V0 公开 API。

设计哲学：
  - 零配置启动：自动检测项目框架（Vue / React / Svelte / 纯 HTML）
  - 轻量隔离：每个预览会话独立端口（5173-5200 位图避让）+ 独立 Vite 子进程
  - 丝滑 HMR：复用 Vite 原生 ESM 热更新，SSE 三处同步 HMR 状态
  - 优雅降级：Node.js 不可用 / node_modules 缺失均给出明确错误
  - 物理隔离：preview.db 与 audit / router / knowledge 等 db 互不干扰

V0 范围：完整后端链路（框架检测 / 端口分配 / Vite 配置生成 / 子进程管理 /
依赖安装进度 / FastAPI 6 端点 / SSE 三处同步 3 新事件 / 审计落库）。
V1 接力：真实 Vite 二进制端到端 + 前端面板 + Monaco ▶️ 按钮。
"""

from __future__ import annotations

from agent.preview.api import router as preview_api_router
from agent.preview.events import (
    EVT_PREVIEW_BUILD_ERROR,
    EVT_PREVIEW_HMR_CONNECTED,
    EVT_PREVIEW_HMR_DISCONNECTED,
    EVT_PREVIEW_INSTALL_PROGRESS,
)
from agent.preview.framework_detector import (
    detect_framework,
    find_project_root,
    get_package_manager,
)
from agent.preview.models import (
    BuildErrorEvent,
    DeviceMode,
    Framework,
    HmrStatusEvent,
    InstallProgressEvent,
    PreviewSession,
    PreviewStatus,
    StartPreviewRequest,
)
from agent.preview.port_allocator import PortAllocator, get_default_allocator
from agent.preview.session_manager import (
    SessionManager,
    get_default_manager,
    reset_default_manager,
)
from agent.preview.vite_manager import (
    VitePreviewManager,
    get_default_vite_manager,
    reset_default_vite_manager,
)

__all__ = [
    "EVT_PREVIEW_BUILD_ERROR",
    "EVT_PREVIEW_HMR_CONNECTED",
    "EVT_PREVIEW_HMR_DISCONNECTED",
    "EVT_PREVIEW_INSTALL_PROGRESS",
    "BuildErrorEvent",
    "DeviceMode",
    "Framework",
    "HmrStatusEvent",
    "InstallProgressEvent",
    "PortAllocator",
    "PreviewSession",
    "PreviewStatus",
    "SessionManager",
    "StartPreviewRequest",
    "VitePreviewManager",
    "detect_framework",
    "find_project_root",
    "get_default_allocator",
    "get_default_manager",
    "get_default_vite_manager",
    "get_package_manager",
    "preview_api_router",
    "reset_default_manager",
    "reset_default_vite_manager",
]
