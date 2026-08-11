"""Phase 1B · 路径沙箱 —— 所有文件类工具的强制入口。

设计目标（CLAUDE.md 红线 §1 安全底线）：
  1. 路径必须落在 allowed_roots 白名单内
  2. 规范化路径（resolve strict=False，软链接也解析）
  3. 拒绝 Windows 保留名（CON / PRN / AUX / NUL / COM1-9 / LPT1-9）
  4. 拒绝 UNC 路径（\\\\server\\share）防止 SMB 攻击
  5. 拒绝 null byte（\\x00）注入
  6. 拒绝空路径 / 纯空白 / 长度超限
  7. must_exist=True 时文件不存在抛 FileNotFoundError（不是 PermissionError）

V0 范围：核心规则 5 项 + 1-2-3 项；V1 扩展：
  - 路径必须在某个 allowed_root 内（V0 跳过此规则，仅作记录）
  - 实际上 V0 也执行仅文件存在性检查
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from agent.builtin.models import PathOutOfBoundsError, PathSecurityError

# Windows 保留名（不区分大小写）
_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)

# 路径最大长度（防止极端路径攻击）
_MAX_PATH_LEN = 4096


def validate_path(
    p: str,
    *,
    allowed_roots: list[str] | None = None,
    must_exist: bool = True,
) -> Path:
    """规范化 + 解析符号链接 + 校验。

    Parameters
    ----------
    p : str
        待校验路径。
    allowed_roots : list[str] | None
        允许的根目录列表（绝对路径）。V0 简化：仅检查存在性，不强制白名单。
        V1 起强制（来自 settings.builtin_allowed_paths）。
    must_exist : bool
        True 时路径不存在抛 FileNotFoundError。

    Returns
    -------
    Path
        解析后的绝对路径（resolve strict=False）。

    Raises
    ------
    PathSecurityError
        黑名单命中（Windows 保留名 / UNC / null byte / 空 / 超长）。
    PathOutOfBoundsError
        不在 allowed_roots 内（V0 暂不执行）。
    FileNotFoundError
        must_exist=True 且路径不存在。
    """
    # ---- 1. 基础校验 ----
    if not p or not p.strip():
        raise PathSecurityError("empty path")
    if "\x00" in p:
        raise PathSecurityError("null byte in path")
    if len(p) > _MAX_PATH_LEN:
        raise PathSecurityError(f"path too long ({len(p)} > {_MAX_PATH_LEN})")

    # ---- 2. UNC 路径拒绝（Windows） ----
    if p.startswith("\\\\") or p.startswith("//"):
        raise PathSecurityError("UNC path not allowed")

    # ---- 3. Windows 保留名拒绝 ----
    if sys.platform == "win32":
        # 取 basename 第一段（去掉扩展名），检查是否是保留名
        basename = os.path.basename(p.rstrip("/\\")).split(".")[0].upper()
        if basename in _WINDOWS_RESERVED_NAMES:
            raise PathSecurityError(f"Windows reserved name: {basename}")

    # ---- 4. 规范化路径 ----
    try:
        # expanduser 处理 ~ ; resolve(strict=False) 解析符号链接
        resolved = Path(p).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathSecurityError(f"path resolve failed: {exc}") from exc

    # ---- 5. allowed_roots 校验（V1 强制，V0 仅日志） ----
    if allowed_roots:
        try:
            resolved_roots = [Path(r).expanduser().resolve(strict=False) for r in allowed_roots]
        except (OSError, RuntimeError):
            resolved_roots = []
        in_allowed = any(_is_within(resolved, root) for root in resolved_roots)
        if not in_allowed:
            raise PathOutOfBoundsError(f"path {resolved} not in allowed_roots {allowed_roots}")

    # ---- 6. 存在性检查 ----
    if must_exist and not resolved.exists():
        raise FileNotFoundError(f"path does not exist: {resolved}")

    return resolved


def _is_within(path: Path, root: Path) -> bool:
    """检查 path 是否在 root 内（防路径前缀绕过）。

    例: path=/etc/abc, root=/etc → True
        path=/etc2, root=/etc → False（不是简单的字符串前缀）
    """
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False
