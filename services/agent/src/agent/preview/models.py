"""Phase 15 V0 · 预览引擎数据模型（Pydantic）。

枚举 / 请求 / 响应 / SSE 事件载荷。与实现文档 §2 严格一致。
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Framework(str, Enum):
    """前端框架（自动检测结果 / 用户手动指定）。"""

    VUE = "vue"  # Vue 3 / Vue 2
    REACT = "react"  # React 16 / 17 / 18
    SVELTE = "svelte"  # Svelte 3 / 4 / 5
    HTML = "html"  # 纯静态 HTML


class PreviewStatus(str, Enum):
    """预览会话生命周期状态。"""

    STARTING = "starting"  # 正在启动 Vite 子进程
    RUNNING = "running"  # 运行中
    INSTALLING = "installing"  # 后台安装 node_modules
    STOPPED = "stopped"  # 已停止
    ERRORED = "errored"  # 错误（启动失败 / 子进程崩溃且重启失败）


class DeviceMode(str, Enum):
    """预览设备模式（前端面板切换）。"""

    DESKTOP = "desktop"  # 100% 无限制
    TABLET = "tablet"  # 768x1024
    MOBILE = "mobile"  # 375x667
    CUSTOM = "custom"  # 用户自定义 width x height


# ---- 端口范围（实现文档 §5.3）-------------------------------------------

PORT_RANGE_START = 5173
PORT_RANGE_END = 5200  # 预留 128 槽位
PORT_RANGE_EXTENDED_END = 5300  # 全占时扩展范围（设计 §9 风险缓解）


class StartPreviewRequest(BaseModel):
    """POST /preview/start 请求体。"""

    project_path: str = Field(..., description="项目根目录绝对路径")
    entry_file: str | None = Field(None, description="入口文件相对路径")
    framework: Framework | None = Field(None, description="手动指定框架（默认自动检测）")
    port: int | None = Field(None, description="手动指定端口（默认自动分配）")


class PreviewSession(BaseModel):
    """预览会话响应模型。"""

    id: str
    project_path: str
    entry_file: str
    framework: Framework
    port: int
    url: str
    status: PreviewStatus
    created_at: int
    last_active_at: int
    pid: int | None = None  # Vite 子进程 PID
    install_progress: int = 0  # 0-100
    config_path: str | None = None
    error: str | None = None


class HmrStatusEvent(BaseModel):
    """SSE 事件：HMR 连接状态。"""

    session_id: str
    status: str  # 'connected' / 'disconnected' / 'reconnecting'
    timestamp: int


class BuildErrorEvent(BaseModel):
    """SSE 事件：Vite 编译错误。"""

    session_id: str
    error: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    timestamp: int


class InstallProgressEvent(BaseModel):
    """SSE 事件：后台依赖安装进度。"""

    session_id: str
    progress: int  # 0-100
    message: str
    timestamp: int


def now_ms() -> int:
    """当前 Unix 毫秒。"""
    from datetime import datetime, timezone

    return int(datetime.now(timezone.utc).timestamp() * 1000)
