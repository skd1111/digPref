"""Phase 2F 路径护栏（path_guard）。

**V3 用户语义**（来自对话：「如果打开了一个项目（文件夹），那么当前智能体可以操作的
也只有这个目录，除非用户手动指定，或者需要操作其他路径的时候要主动问用户」）：

  - Agent 只能读写 `opened_projects` 内的路径（及它们的子目录）
  - Agent 想读其他路径 → 抛 PathOutsideProjectsError → 由 tool_runner 走 HITL
    弹确认框让用户批准 + 加到 opened_projects
  - 进程内动态生效；不持久化（重启 Agent 后清空）

约定：
  - `opened_projects` 是绝对路径列表（resolve 过的）
  - 读路径永远不被拦截（agent 看代码 / 配置文件）—— 除非明确是 sensitive
  - 写路径严格校验
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class PathOutsideProjectsError(PermissionError):
    """Agent 想访问 opened_projects 之外的路径。"""

    def __init__(self, path: str, opened_projects: list[str]):
        self.path = path
        self.opened_projects = list(opened_projects)
        super().__init__(
            f"path {path!r} is outside opened_projects {opened_projects}. "
            f"Agent should request user approval before accessing it."
        )


# 进程内的 opened_projects 列表（绝对路径）
_opened: list[Path] = []


def _load_from_env() -> list[Path]:
    """从环境变量 EAIDE_AGENT_OPENED_PROJECTS 加载（分号分隔绝对路径）。"""
    raw = os.environ.get("EAIDE_AGENT_OPENED_PROJECTS", "")
    out: list[Path] = []
    for token in raw.split(os.pathsep):
        token = token.strip()
        if not token:
            continue
        try:
            p = Path(token).resolve()
            if p.exists():
                out.append(p)
        except OSError:
            continue
    return out


def init_opened_projects(extra: list[str] | None = None) -> list[str]:
    """启动时调用一次：从环境变量 + 显式传入初始化 opened_projects 列表。"""
    global _opened
    _opened = _load_from_env()
    for p in extra or []:
        try:
            resolved = Path(p).resolve()
            if resolved.exists() and resolved not in _opened:
                _opened.append(resolved)
        except OSError:
            continue
    logger.info(
        "path_guard initialised with %d project(s): %s", len(_opened), [str(p) for p in _opened]
    )
    return [str(p) for p in _opened]


def get_opened_projects() -> list[str]:
    """返回当前 opened_projects 列表（绝对路径字符串）。"""
    return [str(p) for p in _opened]


def add_opened_project(folder: str) -> bool:
    """运行时追加一个项目（去重）。"""
    global _opened
    try:
        resolved = Path(folder).resolve()
    except OSError as e:
        logger.warning("add_opened_project resolve failed folder=%s err=%s", folder, e)
        return False
    if not resolved.exists():
        logger.warning("add_opened_project path not found: %s", resolved)
        return False
    if resolved in _opened:
        return False
    _opened.append(resolved)
    logger.info("path_guard added project: %s (total=%d)", resolved, len(_opened))
    return True


def remove_opened_project(folder: str) -> bool:
    """运行时移除一个项目。"""
    global _opened
    try:
        resolved = Path(folder).resolve()
    except OSError:
        return False
    if resolved in _opened:
        _opened.remove(resolved)
        return True
    return False


def is_within_opened(path: str | Path) -> bool:
    """检查 path 是否在某个 opened_project 内（含子目录）。"""
    try:
        target = Path(path).resolve()
    except OSError:
        return False
    for root in _opened:
        try:
            target.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def check(path: str | Path, *, operation: str = "write") -> None:
    """校验 path 是否在 opened_projects 内；不在则抛 PathOutsideProjectsError。

    Args:
        path: 任意路径
        operation: 'read' / 'write' / 'execute'（仅用于日志；当前 read 也走校验）
    """
    if is_within_opened(path):
        return
    logger.info(
        "path_guard blocked operation=%s path=%s (opened=%s)",
        operation,
        path,
        [str(p) for p in _opened],
    )
    raise PathOutsideProjectsError(str(path), [str(p) for p in _opened])
