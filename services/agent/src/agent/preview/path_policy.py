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


# 运行时追加的白名单根目录（BUGFIX #175）：用户在预览按钮确认后由
# SessionManager 写入（持久化在 preview.db，启动时回载）。
_RUNTIME_ROOTS: list[Path] = []


def add_runtime_root(root: str | Path) -> None:
    """追加运行时白名单根目录（去重，规范化）。"""
    resolved = Path(root).expanduser().resolve(strict=False)
    if resolved not in _RUNTIME_ROOTS:
        _RUNTIME_ROOTS.append(resolved)


def clear_runtime_roots() -> None:
    """清空运行时白名单（测试用）。"""
    _RUNTIME_ROOTS.clear()


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
        roots.extend(r for r in _RUNTIME_ROOTS if r not in roots)
        return roots

    # 默认：~/.eaide/projects + home 直接子目录 + 运行时追加根目录（#175）
    home = Path.home().resolve(strict=False)
    defaults: list[Path] = [home / ".eaide" / "projects"]
    try:
        for child in home.iterdir():
            if child.is_dir() and not child.name.startswith("."):
                defaults.append(child)
    except OSError:
        pass
    defaults.extend(r for r in _RUNTIME_ROOTS if r not in defaults)
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
        f"请在预览按钮弹窗中确认加入白名单，或在配置 preview_allowed_paths "
        f"中加入该目录（默认仅允许 ~/.eaide/projects 与用户 home 直接子目录）。"
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
