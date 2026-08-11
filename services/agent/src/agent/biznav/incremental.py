"""biznav.incremental —— Phase 2G V1.3 文件变更 → 反向索引受影响 features。

设计：
    - 监听 project_root 下的源文件（与 codenav 一致，但**独立 watchfiles**；
      V1.5 才会合并到 codenav.watcher pool，V1.3 用独立 watcher 保持模块解耦）
    - 文件变更 → JOIN biznav storage 反向索引 feature_file_index
      → emit SSE `biznav_feature_affected`
    - 同一文件 300ms 内多次变更合并为一次（debounce）

不在 V1.3 内（V1.5 补）：
    - 复用 codenav.watcher pool（避免双 watcher 资源浪费）
    - 自动修正 affected feature（read file content → LLM 推断需不需要改）
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

try:
    from watchfiles import Change, awatch

    _WATCHFILES_AVAILABLE = True
except ImportError:
    _WATCHFILES_AVAILABLE = False
    Change = None  # type: ignore
    awatch = None  # type: ignore

from .events import EVT_FEATURE_AFFECTED, emit_biznav_event
from .storage import FeatureStorage

logger = logging.getLogger(__name__)


_DEBOUNCE_SECONDS = 0.3
_IGNORE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        "target",
        "dist",
        "build",
        ".venv",
        ".idea",
        ".vscode",
        ".eaide",  # biznav YAML 自己写在 .eaide 下
    }
)


def _should_skip(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    return any(p in _IGNORE_DIRS for p in parts)


class AffectedFeaturesWatcher:
    """单项目 incremental watcher。

    Usage:
        watcher = AffectedFeaturesWatcher(project_root, project_name, storage)
        await watcher.start()
        ...
        await watcher.stop()
    """

    def __init__(
        self,
        project_root: str | Path,
        project_name: str,
        storage: FeatureStorage,
    ) -> None:
        self._project_root = Path(project_root)
        self._project_name = project_name
        self._storage = storage
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(), name=f"biznav-incremental-{self._project_name}"
        )

    async def stop(self) -> None:
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
        if not _WATCHFILES_AVAILABLE:
            logger.warning(
                "[biznav.incremental] watchfiles 未安装，跳过增量监听: %s",
                self._project_name,
            )
            return
        if not self._project_root.exists():
            logger.warning("[biznav.incremental] project_root 不存在: %s", self._project_root)
            return

        debounce_buf: dict[str, Change] = {}
        try:
            async for changes in awatch(
                str(self._project_root),
                stop_event=self._stop_event,
                step=50,
                recursive=True,
            ):
                for change, path in changes:
                    if _should_skip(path):
                        continue
                    debounce_buf[path] = change
                # 等 debounce 窗口
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=_DEBOUNCE_SECONDS)
                    break
                except asyncio.TimeoutError:
                    pass
                if not debounce_buf:
                    continue
                changed = list(debounce_buf.keys())
                debounce_buf.clear()
                try:
                    await self._handle_changes(changed)
                except Exception as e:
                    logger.exception("[biznav.incremental] handle_changes: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[biznav.incremental] watcher crashed: %s", e)

    async def _handle_changes(self, changed_paths: list[str]) -> None:
        """JOIN 反向索引 → emit biznav_feature_affected。"""
        # 一个 batch 一个 event（不要每文件 emit，避免前端 SSE 风暴）
        affected: dict[str, list[str]] = {}  # feature_id → [file_path, ...]
        for path in changed_paths:
            # 转相对路径（feature_file_index 存的是 POSIX 正斜杠路径）
            try:
                rel = Path(path).resolve().relative_to(self._project_root.resolve()).as_posix()
            except ValueError:
                rel = Path(path).as_posix()  # 越界路径 fallback 用绝对
            try:
                features = self._storage.find_features_by_file(rel, self._project_name)
            except Exception as e:
                logger.warning("[biznav.incremental] find_features_by_file 失败: %s", e)
                continue
            for f in features:
                affected.setdefault(f.id, []).append(rel)

        if not affected:
            return

        emit_biznav_event(
            EVT_FEATURE_AFFECTED,
            {
                "project_name": self._project_name,
                "affected": [
                    {"feature_id": fid, "files": paths} for fid, paths in affected.items()
                ],
                "ts": int(time.time() * 1000),
            },
        )
        logger.info(
            "[biznav.incremental] %s: %d 个 feature 受 %d 个文件变更影响",
            self._project_name,
            len(affected),
            len(changed_paths),
        )
