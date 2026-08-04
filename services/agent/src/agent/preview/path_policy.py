"""Phase 15 V0 · 预览项目路径白名单（安全红线 §5.2）。

Vite 子进程只能在允许的路径下启动项目：
  - 显式配置 `settings.preview_allowed_paths`（绝对路径列表）
  - 未配置时默认：`~/.eaide/projects/**` + 用户 home 的直接子目录
    （如 `C:/Users/alice/proj`，不包含 `C:/Users/alice/AppData/...` 等深层）

实现复用 builtin.path_sandbox 的规范化语义（resolve strict=False +
防前缀绕过 relative_to），但与 Builtin 工具白名单互相独立。
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from agent.config import settings


class PreviewPathNotAllowedError(RuntimeError):
    """项目根不在 preview_allowed_paths 白名单内。"""


def resolve_allowed_roots() -> list[Path]:
    """解析白名单根目录（未配置时用默认 home 规则）。"""
    configured = list(settings.preview_allowed_paths or [])
    if configured:
        roots: list[Path] = []
        for raw in configured:
            try:
                roots.append(Path(raw).expanduser().resolve(strict=False))
            except OSError:
                continue
        return roots

    # 默认：~/.eaide/projects + home 直接子目录
    home = Path.home().resolve(strict=False)
    defaults: list[Path] = [home / ".eaide" / "projects"]
    try:
        for child in home.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                defaults.append(child)
    except OSError:
        pass
    return defaults


def validate_project_path(project_root: str | Path) -> Path:
    """校验项目根是否在白名单内，返回规范化路径。

    Raises
    ------
    PreviewPathNotAllowedError
        项目根不在白名单。
    """
    resolved = Path(project_root).expanduser().resolve(strict=False)
    for root in resolve_allowed_roots():
        try:
            resolved.relative_to(root)
            return resolved
        except ValueError:
            continue
    raise PreviewPathNotAllowedError(
        f"项目路径不在预览白名单内: {resolved}。"
        f"请在配置 preview_allowed_paths 中加入该目录"
        f"（默认仅允许 ~/.eaide/projects 与用户 home 直接子目录）。"
    )


def is_allowed(project_root: str | Path, roots: Iterable[Path] | None = None) -> bool:
    """只读判断（测试用：roots 为空时用默认白名单）。"""
    try:
        resolved = Path(project_root).expanduser().resolve(strict=False)
    except OSError:
        return False
    candidates = list(roots) if roots is not None else resolve_allowed_roots()
    return any(_within(resolved, r) for r in candidates)


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
