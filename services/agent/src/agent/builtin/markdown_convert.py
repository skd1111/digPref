"""Phase 1B V5 · file_to_markdown 工具（markitdown 文件转 Markdown）。

工具清单：
    - file_to_markdown  把 docx / pdf / pptx / xlsx / html / 图片等文件转换为 Markdown

执行通道：
    1. 主通道：进程内 markitdown 库（agent 包正式依赖，跨机交付可用）
    2. 可选覆盖：外部 markitdown CLI（仅当 builtin_markitdown_executable 显式配置时启用）
    3. V9 兜底：Docling（可选依赖，安装：agent[parse-full]）—— markitdown 失败 /
       结果为空时自动降级，表格结构还原更强；缺失时静默跳过不崩溃

只读转换（低风险）：不写任何文件，仅读取源文件并返回 Markdown 文本。
"""

from __future__ import annotations

import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from pathlib import Path

from agent.builtin.models import ToolResult
from agent.config import settings


def _library_convert_impl(path: str) -> str:
    """markitdown 库真实转换（测试可 monkeypatch 模拟卡死）。"""
    from markitdown import MarkItDown  # 可选依赖，延迟导入（mypy overrides 已豁免）

    result = MarkItDown().convert(path)
    return str(result.text_content or "")


def _convert_with_library(path: str, *, timeout_sec: float) -> str:
    """进程内 markitdown 库转换，带超时保护。

    markitdown 对畸形 / 超大文件可能卡死不返回，直接同步调用会把
    dispatcher 的 to_thread 工作线程永久占住（阻塞流程）。故套一层
    独立线程 + future.result(timeout)；超时后工作线程可能残留运行
    （不可强杀），但调用方立即拿回控制权，不阻塞主流程。
    """
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="markitdown")
    try:
        return executor.submit(_library_convert_impl, path).result(timeout=timeout_sec)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _convert_with_cli(path: str, *, timeout_sec: float) -> str:
    """调用外部 markitdown CLI（argv 列表直传，无 shell，无注入面）。

    Windows 下子进程 stdout 默认用系统代码页（cp936），中文会乱码；
    通过 PYTHONIOENCODING=utf-8 强制子进程以 UTF-8 输出。
    """
    executable = settings.builtin_markitdown_executable
    if not executable or not Path(executable).exists():
        raise FileNotFoundError(
            f"markitdown executable not found: {executable!r} "
            "(set EAIDE_BUILTIN_MARKITDOWN_EXECUTABLE)"
        )
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [executable, path],
        capture_output=True,
        timeout=timeout_sec,
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"markitdown_cli_failed: exit={proc.returncode} {stderr[:500]}")
    return (proc.stdout or b"").decode("utf-8", errors="replace")


def _docling_available() -> bool:
    """可选依赖 docling（extra parse-full）是否可用。"""
    try:
        import docling  # noqa: F401

        return True
    except ImportError:
        return False


def _docling_convert_impl(path: str) -> str:
    """Docling 真实转换（测试可 monkeypatch；延迟导入，缺失不影响主链路）。"""
    from docling.document_converter import DocumentConverter

    result = DocumentConverter().convert(path)
    return str(result.document.export_to_markdown() or "")


def _docling_fallback(path: str) -> str | None:
    """V9 兜底：docling 可用且转换出非空结果时返回 Markdown，否则 None。"""
    if not _docling_available():
        return None
    try:
        text = _docling_convert_impl(path)
    except Exception:
        return None
    return text if text.strip() else None


def _docling_result(text: str, source: str) -> ToolResult:
    return ToolResult(
        ok=True,
        content=text,
        meta={"backend": "docling", "chars": len(text), "source": source},
        risk_level="read",
    )


def builtin_file_to_markdown(
    *,
    path: str,
    timeout_sec: float | None = None,
) -> ToolResult:
    """把文件转换为 Markdown 文本（docx / pdf / pptx / xlsx / html / epub / 图片等）。

    主通道为进程内 markitdown 库；仅当库不可用且显式配置了
    builtin_markitdown_executable 时才走外部 CLI（见模块 docstring）。
    返回 content = Markdown 全文；meta 含 backend（library / cli）与 chars。
    """
    target = (path or "").strip()
    if not target:
        return ToolResult(ok=False, error="empty_path", risk_level="read")
    src = Path(target)
    if not src.is_file():
        return ToolResult(
            ok=False,
            error="not_found",
            hint=f"文件不存在或不是普通文件: {target}",
            risk_level="read",
        )

    timeout = timeout_sec or settings.builtin_markitdown_timeout_sec
    backend = "library"
    try:
        try:
            markdown = _convert_with_library(target, timeout_sec=timeout)
        except ImportError:
            if not settings.builtin_markitdown_executable:
                # V9 兜底：docling 可用时顶上，不再直接报 unavailable
                fallback = _docling_fallback(target)
                if fallback is not None:
                    return _docling_result(fallback, target)
                return ToolResult(
                    ok=False,
                    error="markitdown_unavailable",
                    hint="markitdown 未安装：执行 `uv sync --all-packages`，或用 "
                    "EAIDE_BUILTIN_MARKITDOWN_EXECUTABLE 指定外部 CLI，或安装 agent[parse-full] 走 Docling",
                    risk_level="read",
                )
            backend = "cli"
            markdown = _convert_with_cli(target, timeout_sec=timeout)
    except (subprocess.TimeoutExpired, FuturesTimeoutError):
        return ToolResult(
            ok=False,
            error="timed_out",
            hint=f"markitdown 转换超时（{timeout}s），文件可能过大或格式异常；"
            "可换其他方式读取该文件",
            risk_level="read",
        )
    except Exception as exc:
        # V9 兜底：markitdown 转换异常时尝试 Docling，失败则保留原错误
        fallback = _docling_fallback(target)
        if fallback is not None:
            return _docling_result(fallback, target)
        return ToolResult.from_exception(exc, risk_level="read")

    # V9 兜底：markitdown 输出为空（复杂排版可能提不出内容）时尝试 Docling
    if not markdown.strip():
        fallback = _docling_fallback(target)
        if fallback is not None:
            return _docling_result(fallback, target)

    return ToolResult(
        ok=True,
        content=markdown,
        meta={"backend": backend, "chars": len(markdown), "source": target},
        risk_level="read",
    )
