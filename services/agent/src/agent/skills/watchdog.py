"""SkillWatchdog —— Phase 2D V1 YAML 热加载。

设计（参照 biznav.hot_reload 模式）：
    - 监听 `%APPDATA%\\eaide\\skills\\*.yaml`
    - watchfiles 检测到 YAML 变更 → 防自激（written_by_pid + mtime 双校验）→
      读 YAML → loader.load_one → emit `skill_matched` SSE 事件
    - 防自激原理：loader.write_yaml() 后调 mark_written() 登记"本进程写过"；
      watchfiles 回调里看到当前 YAML mtime 与本进程上次写入一致 → 跳过 reload。

不在 V1 内（V1.5 补）：
    - 多项目隔离（按 project_name 分目录）
    - YAML 删除事件（watchfiles 兜底漏 reload，需 DELETE 监听）
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from watchfiles import awatch

from .events import EVT_SKILL_MATCHED, emit_skill_event
from .loader import SkillLoader

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 防自激：本进程最近一次"自己写 YAML"的 (pid, mtime) 表
# ---------------------------------------------------------------------------
_written_by_pid: dict[str, tuple[int, float]] = {}


def mark_yaml_written(yaml_path: str | Path) -> None:
    """loader.write_yaml() / share.import_zip() 后调用：登记"这次是本进程写的"。

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


def reload_yaml_to_loader(
    yaml_path: str | Path,
    loader: SkillLoader,
    project_name: str = "default",
) -> str | None:
    """读 YAML → loader.load_one → emit `skill_matched` SSE 事件。

    Returns:
        新加载的 skill.id；失败返 None。
    """
    p = Path(yaml_path)
    if not p.exists():
        emit_skill_event(
            EVT_SKILL_MATCHED,
            {
                "project_name": project_name,
                "yaml_path": str(p),
                "success": False,
                "error": f"file not found: {p}",
                "ts": int(time.time() * 1000),
            },
        )
        return None
    try:
        if project_name == "default":
            skill = loader.load_one(p)
        else:
            skill = loader.load_one_for_project(p, project_name)
    except Exception as e:
        emit_skill_event(
            EVT_SKILL_MATCHED,
            {
                "project_name": project_name,
                "yaml_path": str(p),
                "success": False,
                "error": str(e),
                "ts": int(time.time() * 1000),
            },
        )
        logger.warning("[skill_watchdog] load_one failed: %s", e)
        return None

    skill_id = skill.id if skill else None
    emit_skill_event(
        EVT_SKILL_MATCHED,
        {
            "project_name": project_name,
            "yaml_path": str(p),
            "success": skill is not None,
            "skill_id": skill_id,
            "ts": int(time.time() * 1000),
        },
    )
    logger.info(
        "[skill_watchdog] %s: skill_id=%s success=%s",
        project_name,
        skill_id,
        skill is not None,
    )
    return skill_id


# ---------------------------------------------------------------------------
# Watcher 启动入口（由 main.py lifespan 调用）
# ---------------------------------------------------------------------------


class SkillWatchdog:
    """单项目 Skill YAML 热加载器。

    V1 增量：
      - `project_name='default'` 时监听根目录 `<dir>/*.yaml`（共享 skill）
      - `project_name='xxx'` 时监听 `<dir>/<project_name>/*.yaml`（项目专属）
        并调 loader.load_one_for_project()

    Usage:
        watchdog = SkillWatchdog(skills_dir, loader, project_name='default')
        await watchdog.start()
        ...
        await watchdog.stop()
    """

    def __init__(
        self,
        skills_dir: str | Path,
        loader: SkillLoader,
        project_name: str = "default",
        debounce_ms: int = 300,
    ) -> None:
        self._dir = Path(skills_dir)
        self._loader = loader
        self._project_name = project_name
        # 'default' 走根目录；其他走子目录（V1 多项目隔离）
        self._watch_dir = self._dir if project_name == "default" else self._dir / project_name
        self._debounce_ms = debounce_ms
        self._task: asyncio.Task | None = None
        self._stop_event: asyncio.Event | None = None

    async def start(self) -> None:
        self._watch_dir.mkdir(parents=True, exist_ok=True)
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(
            self._watch_loop(), name=f"skill-watchdog-{self._project_name}"
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

    async def _watch_loop(self) -> None:
        """watchfiles 异步包装。"""
        if not self._watch_dir.exists():
            self._watch_dir.mkdir(parents=True, exist_ok=True)

        try:
            async for _changes in awatch(
                str(self._watch_dir),
                stop_event=self._stop_event,
                step=self._debounce_ms,
                recursive=False,
            ):
                if self._stop_event.is_set():
                    break
                # 扫描 watch_dir 所有 *.yaml（watchfiles 返回的 changes 集合信息不一定准，
                # 简化做法：直接 glob 当前目录）
                for yaml_path in self._watch_dir.glob("*.yaml"):
                    if not yaml_path.exists():
                        continue
                    if _is_self_written(yaml_path):
                        logger.debug("[skill_watchdog] 跳过自写入 YAML: %s", yaml_path)
                        continue
                    self._reload(yaml_path)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("[skill_watchdog] watcher crashed: %s", e)

    def _reload(self, yaml_path: Path) -> str | None:
        """按 project 模式路由：委托 `reload_yaml_to_loader`（复用事件 emit + 校验）。"""
        return reload_yaml_to_loader(yaml_path, self._loader, self._project_name)
