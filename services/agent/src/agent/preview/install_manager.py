"""Phase 15 V0 · 后台依赖安装（node_modules 缺失时自动 npm/pnpm/yarn/bun install）。

安装进度通过事件机制推送（InstallProgressEvent）。stderr 出错时标记失败。
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent.preview import events as preview_events
from agent.preview.framework_detector import get_package_manager
from agent.preview.models import now_ms
from agent.preview.vite_manager import _sanitized_env

_PROGRESS_KEYWORDS = {
    "added": 70,
    "removed": 70,
    "changed": 75,
    "up to date": 90,
    "audited": 95,
    "found": 96,
    "packages in": 10,
    "resolving": 8,
    "reify": 30,
    "fetching": 40,
    "building": 60,
}


async def ensure_dependencies(
    project_path: str | Path,
    session_id: str,
    *,
    spawner: Callable[..., Any] | None = None,
) -> bool:
    """确保项目依赖已安装。

    node_modules 已存在 → 直接返回 True（跳过安装）。
    缺失 → 后台执行 install，边执行边 emit InstallProgressEvent。
    失败 → emit 错误事件并返回 False（调用方把 session 标 errored）。
    """
    root = Path(project_path)
    if (root / "node_modules").exists():
        _emit_progress(session_id, 100, "node_modules 已存在，跳过安装")
        return True

    manager = get_package_manager(root)
    cmd = shutil.which(manager)
    if not cmd:
        _emit_progress(session_id, 0, f"未找到包管理器 {manager}（请安装 Node.js ≥ 18）")
        preview_events.emit_event_sync(
            preview_events.EVT_PREVIEW_BUILD_ERROR,
            {
                "session_id": session_id,
                "error": f"未找到包管理器 {manager}，无法安装依赖",
                "file": None,
                "line": None,
                "column": None,
                "timestamp": now_ms(),
            },
        )
        return False

    env = _sanitized_env()
    if spawner is not None:
        proc = spawner([cmd, "install"], env=env, cwd=str(root))
    else:
        proc = await asyncio.create_subprocess_exec(
            cmd,
            "install",
            cwd=str(root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL,
        )

    _emit_progress(session_id, 5, f"通过 {manager} 安装依赖…")

    async def _drain(stream: asyncio.StreamReader | None, is_error: bool) -> None:
        if stream is None:
            return
        progress = 10
        while True:
            raw = await stream.readline()
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            lower = line.lower()
            for kw, pct in _PROGRESS_KEYWORDS.items():
                if kw in lower:
                    progress = max(progress, pct)
                    _emit_progress(session_id, progress, line[:120])
                    break
            if is_error and ("error" in lower or "failed" in lower):
                _emit_progress(session_id, 0, line[:120])

    tasks = [
        asyncio.create_task(_drain(proc.stdout, False)),
        asyncio.create_task(_drain(proc.stderr, True)),
    ]
    await asyncio.gather(*tasks, return_exceptions=True)
    rc = await proc.wait()
    if rc != 0:
        preview_events.emit_event_sync(
            preview_events.EVT_PREVIEW_BUILD_ERROR,
            {
                "session_id": session_id,
                "error": f"依赖安装失败（{manager} install exit={rc}）",
                "file": None,
                "line": None,
                "column": None,
                "timestamp": now_ms(),
            },
        )
        return False
    _emit_progress(session_id, 100, "依赖安装完成")
    return True


def _emit_progress(session_id: str, progress: int, message: str) -> None:
    preview_events.emit_event_sync(
        preview_events.EVT_PREVIEW_INSTALL_PROGRESS,
        {
            "session_id": session_id,
            "progress": max(0, min(100, progress)),
            "message": message,
            "timestamp": now_ms(),
        },
    )
