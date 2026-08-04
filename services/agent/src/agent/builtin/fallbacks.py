"""Phase 1B V3 · Rust 工具 Python 兜底实现。

Agent 独立运行（无 Tauri 注入）时，以下只读 Rust 工具改走本地 Python 实现：
stat_file / find / glob / hash / base64 / mkdir。高危工具（delete / move / shell）
的 Python 兜底在 dispatcher._exec_python_fallback 中已有。
"""
from __future__ import annotations

import base64 as _base64
import fnmatch
import glob as _glob
import hashlib
import os
import re
from pathlib import Path
from typing import Any

from agent.builtin.models import ToolResult
from agent.builtin.path_sandbox import validate_path


def _validate(p: str, *, must_exist: bool = True) -> Path:
    return validate_path(p, must_exist=must_exist)


def builtin_stat_file_py(*, path: str) -> ToolResult:
    """stat_file Python 兜底：文件 / 目录元数据。"""
    try:
        p = _validate(path)
        st = p.stat()
        return ToolResult(
            ok=True,
            content={
                "path": str(p),
                "size": st.st_size,
                "mtime": st.st_mtime,
                "ctime": st.st_ctime,
                "is_dir": p.is_dir(),
                "is_file": p.is_file(),
            },
            meta={"size": st.st_size},
            risk_level="read",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="read")


def builtin_find_py(
    *,
    path: str,
    pattern: str,
    is_regex: bool = False,
    max_depth: int = 10,
) -> ToolResult:
    """find Python 兜底：按文件名模式递归查找。"""
    try:
        root = _validate(path)
        if not root.is_dir():
            return ToolResult(
                ok=False,
                error="not_a_directory",
                hint=str(root),
                risk_level="read",
            )
        matcher = re.compile(pattern) if is_regex else None
        hits: list[str] = []
        root_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            current = Path(dirpath)
            depth = len(current.parts) - root_depth
            if depth >= max_depth:
                dirnames[:] = []
            for name in filenames + dirnames:
                full = str(current / name)
                if matcher is not None:
                    if matcher.search(name):
                        hits.append(full)
                elif fnmatch.fnmatch(name, pattern):
                    hits.append(full)
            if len(hits) >= 1000:
                break
        return ToolResult(
            ok=True,
            content=hits,
            meta={"hit_count": len(hits), "truncated": len(hits) >= 1000},
            risk_level="read",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="read")


def builtin_glob_py(*, pattern: str, base_dir: str = ".") -> ToolResult:
    """glob Python 兜底：glob 模式匹配（支持 ** 递归）。"""
    try:
        base = _validate(base_dir, must_exist=True)
        joined = str(base / pattern) if not os.path.isabs(pattern) else pattern
        hits = sorted(_glob.glob(joined, recursive=True))
        hits = hits[:1000]
        return ToolResult(
            ok=True,
            content=hits,
            meta={"hit_count": len(hits), "truncated": len(hits) >= 1000},
            risk_level="read",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="read")


def builtin_hash_py(*, path: str, algorithm: str = "sha256") -> ToolResult:
    """hash Python 兜底：文件哈希。"""
    try:
        p = _validate(path)
        if algorithm not in {"md5", "sha1", "sha256", "blake2b"}:
            return ToolResult(
                ok=False,
                error="unsupported_algorithm",
                hint="md5 / sha1 / sha256 / blake2b",
                risk_level="read",
            )
        h = hashlib.new(algorithm)
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return ToolResult(
            ok=True,
            content=h.hexdigest(),
            meta={"algorithm": algorithm, "path": str(p)},
            risk_level="read",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="read")


def builtin_base64_py(
    *,
    mode: str,
    data: str | None = None,
    path: str | None = None,
) -> ToolResult:
    """base64 Python 兜底：字符串 / 文件内容编解码。"""
    try:
        if mode not in {"encode", "decode"}:
            return ToolResult(
                ok=False,
                error="invalid_mode",
                hint="encode / decode",
                risk_level="read",
            )
        raw: bytes
        if path:
            p = _validate(path)
            raw = p.read_bytes()
        elif data is not None:
            raw = data.encode("utf-8")
        else:
            return ToolResult(
                ok=False,
                error="missing_input",
                hint="需要 data 或 path",
                risk_level="read",
            )
        if mode == "encode":
            out = _base64.b64encode(raw).decode("ascii")
        else:
            out = _base64.b64decode(raw).decode("utf-8", errors="replace")
        return ToolResult(ok=True, content=out, meta={"mode": mode}, risk_level="read")
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="read")


def builtin_mkdir_py(*, path: str, parents: bool = False) -> ToolResult:
    """mkdir Python 兜底：创建目录（可选递归创建父目录）。

    风险等级 medium（影响文件系统）→ dispatcher 按 require_hitl_for_write 走 HITL。
    """
    try:
        p = _validate(path, must_exist=False)
        if p.exists():
            if p.is_dir():
                return ToolResult(
                    ok=True,
                    content={"path": str(p), "created": False},
                    meta={"created": False},
                    risk_level="medium",
                )
            return ToolResult(
                ok=False,
                error="exists_not_dir",
                hint=str(p),
                risk_level="medium",
            )
        if parents:
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.mkdir()
        return ToolResult(
            ok=True,
            content={"path": str(p), "created": True},
            meta={"created": True, "parents": parents},
            risk_level="medium",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="medium")
