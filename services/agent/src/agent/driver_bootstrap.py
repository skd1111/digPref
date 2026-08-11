"""数据库驱动引导 —— 从 config/driver/ 加载离线 wheel 包。

打包部署时，所有数据库驱动 wheel 统一放置在：
  <安装目录>/config/driver/

本模块在 Agent 启动最早期执行，将该目录下的 .whl
加入 sys.path，使后续 import aiomysql / asyncpg 等正常解析。

查找优先级：
  1. $EAIDE_DRIVER_DIR（显式指定）
  2. <exe所在目录>/config/driver/（PyInstaller 打包）
  3. <项目根>/config/driver/（开发模式）
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_LOADED = False


def _find_driver_dir() -> Path | None:
    """按优先级查找 driver 目录。"""
    # 1. 环境变量
    env = os.environ.get("EAIDE_DRIVER_DIR")
    if env:
        p = Path(env)
        if p.is_dir():
            return p

    # 2. PyInstaller 打包: exe 所在目录/config/driver/
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        p = exe_dir / "config" / "driver"
        if p.is_dir():
            return p

    # 3. 开发模式: 项目根/config/driver/
    #    本文件位于 services/agent/src/agent/driver_bootstrap.py
    project_root = Path(__file__).resolve().parents[4]
    p = project_root / "config" / "driver"
    if p.is_dir():
        return p

    return None


def _ensure_extracted(driver_dir: Path) -> Path:
    """将 .whl 解压到 _site/ 子目录（含 C 扩展的包不能直接从 zip 导入）。

    仅当 _site/ 不存在或 wheel 有更新时才重新解压。
    """
    site_dir = driver_dir / "_site"
    marker = site_dir / ".extracted"

    wheels = sorted(driver_dir.glob("*.whl"))
    # 比较最新 wheel 的修改时间与 marker
    if marker.exists() and wheels:
        newest_whl = max(w.stem for w in wheels)  # 简单用名称判断
        if marker.read_text(encoding="utf-8").strip() == newest_whl:
            return site_dir

    # 解压所有 wheel
    import zipfile

    site_dir.mkdir(parents=True, exist_ok=True)
    for whl in wheels:
        try:
            with zipfile.ZipFile(whl, "r") as zf:
                zf.extractall(site_dir)
        except Exception as exc:
            logger.warning("解压 %s 失败: %s", whl.name, exc)

    # 写入 marker
    if wheels:
        marker.write_text(max(w.stem for w in wheels), encoding="utf-8")

    return site_dir


def load_drivers() -> None:
    """将 config/driver/ 下所有 .whl 解压并加入 sys.path（仅执行一次）。"""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    driver_dir = _find_driver_dir()
    if driver_dir is None:
        logger.debug("未找到 config/driver/ 目录，跳过离线驱动加载")
        return

    wheels = list(driver_dir.glob("*.whl"))
    if not wheels:
        logger.debug("config/driver/ 为空，跳过")
        return

    site_dir = _ensure_extracted(driver_dir)
    site_str = str(site_dir)
    if site_str not in sys.path:
        sys.path.insert(0, site_str)

    logger.info("已从 %s 加载 %d 个离线驱动包", driver_dir, len(wheels))
