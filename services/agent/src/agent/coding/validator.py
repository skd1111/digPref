"""Phase 18 分层验证器 —— Auto-Repair 循环的确定性验证来源。

层级（spec §3.2）：
    L1 内置快检（零配置）：语法解析 —— .py 用 ast；.json parse；.yaml safe_load；
       .ts/.tsx 若 tsc 可用则 --noEmit。未知扩展名跳过（不阻断）。
    L2 项目验证命令：.eaide/config/agent.yaml::validate_command（支持
       {changed_files} 占位符），subprocess 执行并捕获输出喂给 repair。
    L3 降级：无 L2 配置时 level="syntax_only"（repair 上限随之降低）。

level 语义：本次验证的能力上限 —— "full"（L2 可用）/ "syntax_only"（仅 L1）。
"""

from __future__ import annotations

import ast
import json
import logging
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

ValidatorLevel = Literal["full", "syntax_only", "unverified"]

_CONFIG_REL = Path(".eaide") / "config" / "agent.yaml"
_MAX_OUTPUT = 2000  # 喂给 repair 的输出截断上限


@dataclass
class ValidationResult:
    ok: bool
    level: ValidatorLevel
    error: str | None = None


class CodingValidator:
    def __init__(
        self,
        project_root: Path | str,
        toolchain_config: dict[str, str] | None = None,
        timeout_sec: int = 120,
    ):
        self.project_root = Path(project_root)
        self._toolchain_config = toolchain_config or {}
        self._timeout = timeout_sec

    # ---- 对外入口 ----
    def validate(self, changed_files: Sequence[Path | str]) -> ValidationResult:
        files = [Path(f) for f in changed_files]
        level: ValidatorLevel = "full" if self._validate_command() else "syntax_only"

        # L1：语法快检（失败立即返回，不浪费 L2 时间）
        for f in files:
            err = self._syntax_check(f)
            if err:
                return ValidationResult(ok=False, level=level, error=f"{f.name}: {err}")

        # L2：项目验证命令
        cmd = self._validate_command()
        if cmd:
            cmd = cmd.replace("{changed_files}", " ".join(str(f.name) for f in files))
            try:
                proc = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=str(self.project_root),
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                )
            except subprocess.TimeoutExpired:
                return ValidationResult(
                    ok=False, level=level, error=f"validate_command 超时（{self._timeout}s）: {cmd}"
                )
            if proc.returncode != 0:
                detail = (proc.stdout or "") + "\n" + (proc.stderr or "")
                return ValidationResult(
                    ok=False,
                    level=level,
                    error=f"validate_command 失败(exit={proc.returncode}):\n{detail.strip()[:_MAX_OUTPUT]}",
                )
        return ValidationResult(ok=True, level=level)

    # ---- L1：语法快检 ----
    def _syntax_check(self, f: Path) -> str | None:
        if not f.is_file():
            return None  # 文件不存在不阻断（可能已被后续步骤删除）
        suffix = f.suffix.lower()
        try:
            source = f.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return f"读取失败: {exc}"

        if suffix == ".py":
            try:
                ast.parse(source, filename=str(f))
            except SyntaxError as exc:
                return f"语法错误 line {exc.lineno}: {exc.msg}"
        elif suffix == ".json":
            try:
                json.loads(source)
            except json.JSONDecodeError as exc:
                return f"JSON 解析失败: {exc.msg} (pos {exc.pos})"
        elif suffix in (".yaml", ".yml"):
            try:
                import yaml

                yaml.safe_load(source)
            except ImportError:
                return None
            except Exception as exc:
                return f"YAML 解析失败: {exc}"
        elif suffix in (".ts", ".tsx"):
            return self._tsc_check(f)
        return None

    def _tsc_check(self, f: Path) -> str | None:
        from agent.coding.toolchain import resolve_toolchain

        tsc = resolve_toolchain("tsc", self._toolchain_config)
        if not tsc.available or not tsc.path:
            return None  # tsc 不可用 → 跳过（降级不阻断）
        try:
            proc = subprocess.run(
                [tsc.path, "--noEmit", str(f)],
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("tsc check failed to run: %s", exc)
            return None
        if proc.returncode != 0:
            return f"tsc 错误:\n{(proc.stdout + proc.stderr).strip()[:_MAX_OUTPUT]}"
        return None

    # ---- L2：读取项目验证命令 ----
    def _validate_command(self) -> str | None:
        cfg = self.project_root / _CONFIG_REL
        if not cfg.is_file():
            return None
        try:
            import yaml

            data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        except Exception:
            return None
        cmd = data.get("validate_command")
        return str(cmd).strip() if cmd else None
