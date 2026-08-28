"""Phase 1B V9 · OfficeCLI 运行时封装（Office 读写 / 渲染引擎，2026-08-25）。

OfficeCLI（iOfficeAI，Apache 2.0）是单二进制、无需安装 Office 的办公套件：
docx / xlsx / pptx 读/改/建 + 内置高保真 HTML/PNG 渲染引擎，专为 AI Agent
设计（确定性 --json 输出 + 结构化错误码，供上层自愈）。

二进制定位（三级回退，与 config/biz_dict 同策略）：
    1. 显式覆盖：settings.builtin_officecli_executable
    2. 捆绑二进制：vendor/officecli/<平台二进制>（cwd 缺失时回退 PyInstaller _MEIPASS）
    3. PATH 中的 officecli

安全约束：
    - argv 列表直传子进程，无 shell，无注入面
    - 强制 OFFICECLI_SKIP_UPDATE=1（企业内网：禁止后台自动更新访问外网）
    - 超时保护：subprocess.run(timeout)，超时即终止子进程
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent.config import settings

# 捆绑二进制的相对目录（spec datas: ('vendor/officecli', 'vendor/officecli')）
_VENDOR_REL_DIR = "vendor/officecli"

# 各平台二进制文件名（与 GitHub Releases 制品名一致）
_PLATFORM_BINARIES: dict[str, tuple[str, ...]] = {
    "win32": ("officecli-win-x64.exe", "officecli-win-arm64.exe"),
    "darwin": ("officecli-mac-arm64", "officecli-mac-x64"),
    "linux": ("officecli-linux-x64", "officecli-linux-arm64"),
}

# OfficeCLI 支持的三种办公文档后缀
OFFICE_SUFFIXES = frozenset({".docx", ".xlsx", ".pptx"})


@dataclass
class OfficeCliOutcome:
    """OfficeCLI 子进程执行结果（统一返回类型）。

    Attributes:
        ok: 退出码为 0 且（as_json 时）输出可解析或无需解析。
        data: --json 解析后的结构；非 JSON 命令为 stdout 原文。
        error: 错误码（officecli_not_installed / timed_out / OfficeCLI 结构化错误码）。
        message: 人类可读错误描述。
        suggestion: OfficeCLI 返回的修正建议（拼写纠正 / 合法取值范围等，供自愈）。
        exit_code: 子进程退出码（超时 / 未安装时为 -1）。
    """

    ok: bool = False
    data: Any = None
    error: str | None = None
    message: str | None = None
    suggestion: str | None = None
    exit_code: int = -1
    raw_stdout: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _bundled_candidates() -> list[Path]:
    """捆绑二进制候选路径：cwd 优先，缺失回退 PyInstaller _MEIPASS。"""
    names = _PLATFORM_BINARIES.get(sys.platform, ())
    roots = [Path.cwd()]
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        roots.append(Path(meipass))
    # 开发态从子目录启动时 cwd 不可靠：从模块位置向上推导仓库根
    try:
        repo_root = Path(__file__).resolve().parents[5]
        if repo_root not in roots:
            roots.append(repo_root)
    except IndexError:
        pass
    return [root / _VENDOR_REL_DIR / name for root in roots for name in names]


def resolve_officecli_exe() -> str | None:
    """按三级回退定位 OfficeCLI 二进制；全部缺失返 None。"""
    override = (settings.builtin_officecli_executable or "").strip()
    if override and Path(override).is_file():
        return override
    for candidate in _bundled_candidates():
        if candidate.is_file():
            return str(candidate)
    return shutil.which("officecli")


def _parse_json_output(stdout: str) -> Any | None:
    """尝试解析 stdout 为 JSON；失败返 None（调用方按文本处理）。"""
    text = stdout.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _child_env() -> dict[str, str]:
    """子进程环境白名单（不继承 Agent 敏感变量；内网禁外联更新）。

    .NET 运行时依赖 TEMP / USERPROFILE 等基础变量（实测缺失时临时文件
    落到 C:\\WINDOWS 导致 Access denied），故保留最小系统变量集。
    """
    env = {
        "PATH": os.environ.get("PATH", ""),
        # Windows 下子进程 stdout 默认系统代码页（cp936），强制 UTF-8 防中文乱码
        "PYTHONIOENCODING": "utf-8",
        # 内网红线：禁止后台自动更新探测外网
        "OFFICECLI_SKIP_UPDATE": "1",
    }
    for key in ("TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "SystemRoot", "HOME"):
        val = os.environ.get(key)
        if val:
            env[key] = val
    return env


def _run_impl(exe: str, cmd: list[str], timeout_sec: float) -> subprocess.CompletedProcess[bytes]:
    """子进程真实执行（独立线程里跑，外层套超时兜底）。"""
    return subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout_sec,
        check=False,
        env=_child_env(),
    )


def run_officecli(
    args: Sequence[str],
    *,
    as_json: bool = True,
    timeout_sec: float | None = None,
) -> OfficeCliOutcome:
    """执行 OfficeCLI 命令（argv 列表直传，无 shell）。

    Args:
        args: 命令参数（不含可执行文件本身，如 ["view", "a.docx", "outline"]）。
        as_json: True 时追加 --json 并解析 stdout；False 按纯文本返回
            （view html / view screenshot 等写文件命令用）。
        timeout_sec: 超时秒数（缺省用 settings.builtin_officecli_timeout_sec）。

    Returns:
        OfficeCliOutcome；失败时 error / message / suggestion 尽量保留
        OfficeCLI 的结构化错误信息，供 Agent 自愈。
    """
    exe = resolve_officecli_exe()
    if not exe:
        return OfficeCliOutcome(
            ok=False,
            error="officecli_not_installed",
            message="OfficeCLI 二进制未找到（捆绑目录与 PATH 均无）",
            suggestion="运行 infra/scripts/fetch-officecli.ps1 下载捆绑二进制，"
            "或用 EAIDE_BUILTIN_OFFICECLI_EXECUTABLE 指定已有二进制路径",
        )

    cmd = [exe, *args]
    if as_json and "--json" not in args:
        cmd.append("--json")
    timeout = timeout_sec or settings.builtin_officecli_timeout_sec

    try:
        # 双层超时兜底：subprocess timeout 负责杀进程；future timeout 防极端卡死
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="officecli")
        try:
            proc = executor.submit(_run_impl, exe, cmd, timeout).result(timeout=timeout + 10)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
    except (subprocess.TimeoutExpired, FuturesTimeoutError):
        return OfficeCliOutcome(
            ok=False,
            error="timed_out",
            message=f"OfficeCLI 执行超时（{timeout}s）",
            suggestion="文件可能过大或格式异常；可先用 office_read outline 探查结构",
        )
    except Exception as exc:
        return OfficeCliOutcome(ok=False, error="spawn_failed", message=str(exc))

    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
    outcome = OfficeCliOutcome(exit_code=proc.returncode, raw_stdout=stdout)

    if as_json:
        parsed = _parse_json_output(stdout)
        if parsed is not None:
            outcome.data = parsed
            # OfficeCLI 失败时也输出 JSON：{"success": false, "error": {...}}
            if isinstance(parsed, dict) and parsed.get("success") is False:
                err = parsed.get("error") or {}
                if isinstance(err, dict):
                    outcome.error = str(err.get("code") or "officecli_error")
                    outcome.message = str(err.get("error") or stderr[:500] or "unknown error")
                    outcome.suggestion = err.get("suggestion") or None
                else:
                    outcome.error = "officecli_error"
                    outcome.message = str(err)
                return outcome
        elif proc.returncode != 0:
            outcome.error = "officecli_failed"
            outcome.message = (stderr or stdout)[:1000] or f"exit={proc.returncode}"
            return outcome
    elif proc.returncode != 0:
        outcome.error = "officecli_failed"
        outcome.message = (stderr or stdout)[:1000] or f"exit={proc.returncode}"
        return outcome

    outcome.ok = True
    if not as_json:
        outcome.data = stdout
    return outcome
