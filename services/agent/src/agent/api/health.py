"""/health + /version endpoints — used by Docker healthcheck + EAIDE Tauri 启动器。

`/version` 让 Tauri 在启动时核对 Agent 是不是「匹配当前 EXE 的代码」。
未来如果新 EXE 装上但 Agent 复用旧版，可靠 /version 对比强制重启（修复 404/老接口问题）。
"""

from __future__ import annotations

import os
import time

from fastapi import APIRouter

router = APIRouter(tags=["health"])

# 进程启动时记录的 build 标记（pid + 启动秒数 + 进程名）。
# 给 Tauri 一个「每次启动都不一样」的指纹，用来发现「是不是旧 Agent 没死」。
_START_TIME = time.time()
_START_PID = os.getpid()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/version")
async def version() -> dict:
    """EAIDE Agent 版本指纹 —— 给 Tauri 用来判断要重用还是强杀重启。
    pid + boot_time 组合保证同一 Agent 二进制重启后指纹也会变。
    """
    return {
        "service": "eaide-agent",
        "pid": _START_PID,
        "boot_time": _START_TIME,
        "uptime_s": int(time.time() - _START_TIME),
        # endpoints 列表给 Tauri 一个「它应该有这些路由」的探针；
        # 任何一项缺失 = 这个 Agent 是老版，强制 kill + 重起。
        "endpoints": [
            "/health",
            "/version",
            "/router/backends",
            "/router/backends/{name}",
            "/router/backends/test-connection",
            "/router/reload-context",
            "/codenav/jump",
            "/codenav/explain",
            "/codenav/llm-config",
            "/codenav/llm-backend",
            "/codenav/opened-projects",
        ],
    }
