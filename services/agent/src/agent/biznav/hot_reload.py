"""biznav.hot_reload —— Phase 2G V1.3 YAML 热加载。

设计：
    - 监听项目根目录下 `<project_root>/.eaide/features/{project_name}.yaml`
    - watchfiles 检测到 YAML 变更 → 防自激（written_by_pid + mtime 双校验）→
      读 YAML → storage.upsert → emit SSE `biznav_yaml_reloaded`
    - 防自激原理：FeatureStorage.upsert() 同步重写 YAML 时记录
      `_written_by_pid[yaml_path] = (pid, mtime)`，watchfiles 回调里看到
      当前 YAML mtime 与最近一次"自己写的"一致 → 跳过。
    - YAML 解析失败：捕获 `yaml.YAMLError` → emit `biznav_yaml_reloaded`
      但 `success=False` + error 字段，DB 不动。

不在 V1.3 内（V1.5 补）：
    - 多 yaml 文件监听（一个项目多个 yaml）
    - YAML 与 DB 冲突时合并策略（同 id source='manual' 优先）
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from .events import EVT_YAML_RELOADED, emit_biznav_event
from .import_export import FeatureImportError, FeatureIO
from .storage import FeatureStorage

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 防自激：当前进程最近一次"自己写 YAML"的 (pid, mtime) 表
# ---------------------------------------------------------------------------
_written_by_pid: dict[str, tuple[int, float]] = {}


def mark_yaml_written(yaml_path: str | Path) -> None:
    """FeatureIO.export_yaml() 后调用：登记"这次是本进程写的 YAML"。

    watchfiles 回调里若发现 mtime 与本进程上次写入一致 → 跳过 reload。
    """
    p = Path(yaml_path)
    if p.exists():
        _written_by_pid[str(p.resolve())] = (os.getpid(), p.stat().st_mtime)


def _is_self_written(yaml_path: Path) -> bool:
    """判断当前 YAML 变更是否由本进程刚刚写入。"""
    resolved = str(yaml_path.resolve())
    record = _written_by_pid.get(resolved)
    if record is None:
        return False
    pid, mtime = record
    if pid != os.getpid():
        return False
    if not yaml_path.exists():
        return False
    return yaml_path.stat().st_mtime == mtime


# ---------------------------------------------------------------------------
# 主 reload 函数
# ---------------------------------------------------------------------------


async def reload_yaml_to_db(
    yaml_path: str | Path,
    project_name: str,
    storage: FeatureStorage,
) -> int:
    """读 YAML → upsert 到 DB → emit SSE `biznav_yaml_reloaded`。

    Returns:
        写入的 feature 数量（成功时）；失败抛 FeatureImportError。
    """
    p = Path(yaml_path)
    if not p.exists():
        emit_biznav_event(
            EVT_YAML_RELOADED,
            {
                "project_name": project_name,
                "yaml_path": str(p),
                "success": False,
                "error": f"file not found: {p}",
                "ts": int(time.time() * 1000),
            },
        )
        raise FileNotFoundError(f"YAML not found: {p}")

    # YAML → DB（FeatureIO.sync_yaml_to_db 内部 from_yaml + 合并；不存在则新增）
    try:
        yaml_text = p.read_text(encoding="utf-8")
        report = FeatureIO.sync_yaml_to_db(yaml_text, project_name, storage)
    except FeatureImportError as e:
        emit_biznav_event(
            EVT_YAML_RELOADED,
            {
                "project_name": project_name,
                "yaml_path": str(p),
                "success": False,
                "error": str(e),
                "ts": int(time.time() * 1000),
            },
        )
        logger.warning("[biznav.hot_reload] YAML parse failed: %s", e)
        raise

    emit_biznav_event(
        EVT_YAML_RELOADED,
        {
            "project_name": project_name,
            "yaml_path": str(p),
            "success": True,
            "inserted": report.inserted,
            "updated": report.updated,
            "skipped": report.skipped,
            "conflicts": len(report.conflicts),
            "ts": int(time.time() * 1000),
        },
    )
    logger.info(
        "[biznav.hot_reload] %s: inserted=%d updated=%d skipped=%d conflicts=%d",
        project_name,
        report.inserted,
        report.updated,
        report.skipped,
        len(report.conflicts),
    )
    return report.inserted + report.updated


# ---------------------------------------------------------------------------
# Watcher 启动入口（由 main.py lifespan 调用）
# ---------------------------------------------------------------------------


class YamlHotReloader:
    """单项目 YAML 热加载器。

    Usage:
        reloader = YamlHotReloader(project_root, project_name, storage)
        await reloader.start()
        ...
        await reloader.stop()
    """

    def __init__(
        self,
        project_root: str | Path,
        project_name: str,
        storage: FeatureStorage,
        debounce_ms: int = 300,
    ) -> None:
        self._project_root = Path(project_root)
        self._project_name = project_name
        self._storage = storage
        self._debounce_ms = debounce_ms
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # watchfiles 在 sync 线程跑；事件回调通过 run_coroutine_threadsafe 调度
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def yaml_path(self) -> Path:
        return self._project_root / ".eaide" / "features" / f"{self._project_name}.yaml"

    def _ensure_yaml_parent(self) -> None:
        self.yaml_path.parent.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        """启动 watcher（在主 event loop 里）。"""
        self._ensure_yaml_parent()
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(
            self._watch_loop(), name=f"biznav-reload-{self._project_name}"
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _watch_loop(self) -> None:
        """watchfiles 异步包装。"""
        try:
            from watchfiles import awatch
        except ImportError:
            logger.warning("[biznav.hot_reload] watchfiles 未安装，跳过 hot_reload 功能")
            return

        yaml_path = self.yaml_path
        watch_dir = yaml_path.parent
        if not watch_dir.exists():
            self._ensure_yaml_parent()

        try:
            async for _changes in awatch(
                str(watch_dir),
                stop_event=self._stop_event,
                step=self._debounce_ms,
                recursive=False,
            ):
                if self._stop_event.is_set():
                    break
                if not yaml_path.exists():
                    continue
                if _is_self_written(yaml_path):
                    # 本进程刚刚写入 → 跳过
                    logger.debug("[biznav.hot_reload] 跳过自写入 YAML: %s", yaml_path)
                    continue
                try:
                    await reload_yaml_to_db(yaml_path, self._project_name, self._storage)
                except FileNotFoundError:
                    pass  # 已 emit；watch 继续
                except FeatureImportError:
                    pass  # 已 emit；watch 继续
                except Exception as e:
                    logger.exception("[biznav.hot_reload] reload 失败: %s", e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[biznav.hot_reload] watcher crashed: %s", e)
