"""FileWatcher — watchfiles 监听文件变更 → 增量索引。

约束：
- 去抖 300ms：同一文件短时间多次变更合并为一次
- 忽略 .git / node_modules / __pycache__ / target / dist / build / .venv / .idea / .vscode

设计：start() 启动后台 task；stop() 取消。watcher 跟 indexer 解耦，watcher
持有 indexer 引用即可。
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from watchfiles import Change, awatch

from agent.codenav.indexer import _IGNORE_DIRS

if TYPE_CHECKING:
    from agent.codenav.indexer import WorkspaceIndexer

logger = logging.getLogger(__name__)

_DEBOUNCE_SECONDS = 0.3


class FileWatcher:
    def __init__(self, indexer: "WorkspaceIndexer", root_paths: list[str]):
        self.indexer = indexer
        self._root_paths = [str(p) for p in root_paths]
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        """启动 watchfiles 监听（后台 task）。"""
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="codenav-watcher")

    async def stop(self) -> None:
        """停止监听。"""
        if self._stop_event:
            self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        assert self._stop_event is not None
        debounce_buf: dict[str, Change] = {}
        try:
            async for changes in awatch(*self._root_paths, stop_event=self._stop_event, step=50):
                for change, path in changes:
                    if self._should_skip(path):
                        continue
                    debounce_buf[path] = change
                # 等待 debounce 窗口
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=_DEBOUNCE_SECONDS
                    )
                    break  # stop_event 被 set
                except asyncio.TimeoutError:
                    pass
                # 取出本轮所有变更 → 一次性更新
                if debounce_buf:
                    changed = list(debounce_buf.keys())
                    debounce_buf.clear()
                    try:
                        await self.indexer.incremental_update(changed)
                    except Exception as e:
                        logger.warning("incremental_update failed: %s", e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("FileWatcher crashed: %s", e)

    def _should_skip(self, path: str) -> bool:
        # 任意路径段在忽略列表 → 跳过
        parts = path.replace("\\", "/").split("/")
        return any(p in _IGNORE_DIRS for p in parts)
