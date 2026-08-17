"""paths —— 运行时数据根的统一解析（BUGFIX #98：资产/运行库收敛到安装目录）。

用户要求：所有用户可见资产（skills / expert_teams）与运行库（*.db）
统一落在安装目录（与 config/ 同父级），不再散落 %APPDATA%\\eaide。

解析优先级：
  1. $EAIDE_DATA_ROOT —— Tauri 生产启动注入
     （Windows = 安装目录；macOS = ~/Library/Application Support/eaide）
  2. %APPDATA%\\eaide —— 未注入时的兜底（开发模式 / 独立运行）

旧 %APPDATA%\\eaide 数据已由用户手动清理，不做自动迁移（不留搬运代码）。

工作空间（workspace_dir）—— 底层规则（用户要求 2026-08-17）：
  智能体运行中创建的任何文件默认都落到当前工作空间内，并按类型自动
  分类建目录；仅当用户显式指定了输出目录时才尊重用户指定。
  解析优先级：$EAIDE_WORKSPACE_DIR > workspace.json 自定义 > 数据根/workspace。
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def data_root() -> Path:
    """运行时数据根目录（资产 + 运行库的统一父级）。"""
    if root := os.environ.get("EAIDE_DATA_ROOT"):
        return Path(root).expanduser()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "eaide"
    return Path.home() / ".eaide"


# ---------------------------------------------------------------------------
# 工作空间
# ---------------------------------------------------------------------------


def _workspace_config_path() -> Path:
    """自定义工作空间配置的持久化文件（与 llm-config.json 同机制）。

    Tauri 拉起 Agent 时注入 EAIDE_CONFIG_DIR=<安装目录>/config；
    开发模式 cwd = 项目根（config/ 同样存在）。
    """
    from agent.config import settings

    cfg_dir = os.environ.get("EAIDE_CONFIG_DIR")
    if cfg_dir:
        return Path(cfg_dir) / settings.workspace_config_path
    return Path(settings.workspace_config_path)


def load_workspace_override() -> str | None:
    """读用户在设置页自定义的工作空间路径（未配置/不可读返 None）。"""
    try:
        with open(_workspace_config_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    raw = data.get("path") if isinstance(data, dict) else None
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return None


def save_workspace_override(path: str | None) -> None:
    """写自定义工作空间路径（None / 空串 = 恢复默认，删掉自定义值）。"""
    target = _workspace_config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"path": path.strip()} if path and path.strip() else {}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def workspace_dir(*, ensure: bool = True) -> Path:
    """当前工作空间目录（智能体产出文件的默认落盘根）。

    优先级：
      1. $EAIDE_WORKSPACE_DIR（测试/部署显式注入）
      2. 设置页自定义（workspace.json）
      3. 默认 = 数据根/workspace（生产即安装目录/workspace）
    """
    raw = os.environ.get("EAIDE_WORKSPACE_DIR") or load_workspace_override()
    if raw:
        p = Path(raw).expanduser()
    else:
        p = data_root() / "workspace"
    resolved = p.resolve(strict=False)
    if ensure:
        resolved.mkdir(parents=True, exist_ok=True)
    return resolved


# 扩展名 → 分类（docs / data / images / other）
_IMAGE_EXTS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".webp",
        ".svg",
        ".ico",
        ".tiff",
        ".tif",
        ".heic",
    }
)
_DATA_EXTS = frozenset(
    {
        ".csv",
        ".tsv",
        ".xlsx",
        ".xls",
        ".parquet",
        ".json",
        ".sqlite",
        ".db",
        ".ndjson",
        ".jsonl",
        ".feather",
        ".arrow",
    }
)
_DOC_EXTS = frozenset(
    {
        ".md",
        ".txt",
        ".docx",
        ".doc",
        ".pdf",
        ".rtf",
        ".html",
        ".htm",
        ".pptx",
        ".ppt",
        ".log",
    }
)


def classify_category(filename: str) -> str:
    """按扩展名推断分类（docs / data / images / other）。"""
    ext = Path(filename).suffix.lower()
    if ext in _IMAGE_EXTS:
        return "images"
    if ext in _DATA_EXTS:
        return "data"
    if ext in _DOC_EXTS:
        return "docs"
    return "other"


def _category_subdir(category: str) -> str:
    from agent.config import settings

    return {
        "docs": settings.workspace_subdir_docs,
        "data": settings.workspace_subdir_data,
        "images": settings.workspace_subdir_images,
    }.get(category, settings.workspace_subdir_other)


def is_user_specified_output(path: str) -> bool:
    """判断路径是否为用户显式指定的输出位置。

    底层规则豁免条件：含目录分隔符的绝对路径视为用户指定；
    纯文件名（"报告.docx"）或相对片段 → 按默认规则落工作空间。
    """
    p = Path(path)
    return p.is_absolute()


def resolve_output_path(path: str, *, category: str | None = None) -> Path:
    """底层规则入口：把创建类文件的目标路径解析到当前工作空间内。

    - 用户显式指定（绝对路径）→ 原样返回（仅规范化）
    - 否则 → workspace/<分类子目录>/<文件名>，自动建目录；
      用户给的相对子目录（如 "sub/a.txt"）保留在工作空间内拼接。
    """
    p = Path(path).expanduser()
    if p.is_absolute():
        resolved = p.resolve(strict=False)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        return resolved

    ws = workspace_dir()
    # 相对路径：带目录片段时保留层级（仍在工作空间内）
    if len(p.parts) > 1:
        target = ws / p
        target.parent.mkdir(parents=True, exist_ok=True)
        return target.resolve(strict=False)

    cat = category or classify_category(p.name)
    target_dir = ws / _category_subdir(cat)
    target_dir.mkdir(parents=True, exist_ok=True)
    return (target_dir / p.name).resolve(strict=False)
