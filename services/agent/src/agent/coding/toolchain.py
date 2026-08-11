"""Phase 18 工具链探测 —— Coding 框架验证器的编译能力来源。

解析顺序（spec §3.3）：
    1. 用户在设置页配置的路径（configured）
    2. PATH 环境变量（shutil.which）（path）
    3. 常见安装目录探测（Windows 预置候选）（probe）
    4. 全部失败 → unavailable（验证器降级为纯语法检查并告知用户）

探测结果按会话缓存，避免每次 repair 重复探测。
工具链路径配置持久化在单文件 JSON（settings.toolchain_config_path）。
"""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger(__name__)

ToolSource = Literal["configured", "path", "probe", "unavailable"]


@dataclass
class ToolchainResult:
    name: str
    path: str | None
    source: ToolSource
    available: bool


# 常见安装目录候选（Windows 优先；其他平台可扩充）
COMMON_PROBES: dict[str, list[str]] = {
    "python": [
        r"C:\Python3*\python.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python3*\python.exe"),
        r"C:\Program Files\Python3*\python.exe",
    ],
    "node": [
        r"C:\Program Files\nodejs\node.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\fnm_multishells\*\node.exe"),
        os.path.expandvars(r"%APPDATA%\nvm\*\node.exe"),
    ],
    "pnpm": [
        os.path.expandvars(r"%LOCALAPPDATA%\pnpm\pnpm.exe"),
        os.path.expandvars(r"%APPDATA%\npm\pnpm.cmd"),
    ],
    "java": [
        r"C:\Program Files\Java\*\bin\java.exe",
        r"C:\Program Files\Eclipse Adoptium\*\bin\java.exe",
    ],
    "javac": [
        r"C:\Program Files\Java\*\bin\javac.exe",
        r"C:\Program Files\Eclipse Adoptium\*\bin\javac.exe",
    ],
    "tsc": [
        os.path.expandvars(r"%APPDATA%\npm\tsc.cmd"),
    ],
}

# 会话级缓存：name → ToolchainResult
_cache: dict[str, ToolchainResult] = {}


def clear_cache() -> None:
    _cache.clear()


def resolve_toolchain(name: str, configured: dict[str, str] | None = None) -> ToolchainResult:
    """按 configured → path → probe 顺序解析工具路径。"""
    cached = _cache.get(name)
    if cached is not None:
        return cached

    result = _resolve_uncached(name, configured or {})
    _cache[name] = result
    if result.available:
        logger.info("toolchain resolved: %s → %s (%s)", name, result.path, result.source)
    else:
        logger.warning("toolchain unavailable: %s（验证降级为纯语法检查）", name)
    return result


def _resolve_uncached(name: str, configured: dict[str, str]) -> ToolchainResult:
    # 1. 用户配置路径
    cfg_path = configured.get(name)
    if cfg_path and os.path.isfile(cfg_path):
        return ToolchainResult(name=name, path=cfg_path, source="configured", available=True)

    # 2. PATH
    which = shutil.which(name)
    if which:
        return ToolchainResult(name=name, path=which, source="path", available=True)

    # 3. 常见安装目录
    for pattern in COMMON_PROBES.get(name, []):
        matches = sorted(glob.glob(pattern))
        if matches and os.path.isfile(matches[0]):
            return ToolchainResult(name=name, path=matches[0], source="probe", available=True)

    return ToolchainResult(name=name, path=None, source="unavailable", available=False)


# ---- 工具链路径配置持久化（单文件 JSON）----


def load_toolchain_config() -> dict[str, str]:
    from agent.config import settings

    path = settings.toolchain_config_path
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if v}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {}


def save_toolchain_config(paths: dict[str, str]) -> None:
    from agent.config import settings

    path = settings.toolchain_config_path
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(paths, f, ensure_ascii=False, indent=2)
    clear_cache()  # 配置变更后缓存失效
