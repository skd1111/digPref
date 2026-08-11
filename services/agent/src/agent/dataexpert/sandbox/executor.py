"""Phase 7 V0 · 受限子进程执行 Python —— 白/黑名单 + 内存/时限。

安全红线（design §4.2）：
  - 独立子进程执行（隔离）
  - policy.validate_ast(script) 先做 AST 白名单校验
  - 子进程 resource.setrlimit 限内存；asyncio.wait_for 限时
  - 输入/输出 DataFrame 走 Arrow IPC（父子进程零拷贝）

资源限制：
  - 内存 ≤ 2GB（settings.data_sandbox_mem_mb）
  - 执行 ≤ 30s（settings.data_sandbox_timeout）
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from agent.config import settings
from agent.dataexpert.models import SandboxResult
from agent.dataexpert.sandbox.policy import SandboxViolationError, validate_ast

# 子进程执行脚本模板
_WORKER_TEMPLATE = """
import sys
import json

# 资源限制（仅 Unix 有效；Windows 下靠 timeout 兜底）
try:
    import resource
    mem_bytes = {mem_mb} * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
except (ImportError, ValueError):
    pass  # Windows 无 resource 模块

# 执行用户脚本
import pandas as pd
import numpy as np

# 读取输入 DataFrame（如果有）
df_inputs = {{}}
input_path = r"{input_path}"
if input_path:
    try:
        df_inputs["df"] = pd.read_parquet(input_path)
    except Exception:
        pass

# 注入便捷函数
def load_result():
    return df_inputs.get("df", pd.DataFrame())

# 执行用户代码（df 已注入为上一步 SQL 结果 DataFrame，缺口 8）
_user_ns = {{"pd": pd, "np": np, "load_result": load_result, "df_inputs": df_inputs}}
if "df" in df_inputs:
    _user_ns["df"] = df_inputs["df"]
exec(compile(open(r"{script_path}", encoding="utf-8").read(), "<sandbox>", "exec"), _user_ns)

# 输出结果（result 优先；df 注入后始终存在，只能作兑底）
output_path = r"{output_path}"
if "result" in _user_ns and hasattr(_user_ns["result"], "to_parquet"):
    _user_ns["result"].to_parquet(output_path, index=False)
elif "df" in _user_ns and hasattr(_user_ns["df"], "to_parquet"):
    _user_ns["df"].to_parquet(output_path, index=False)

print(json.dumps({{"ok": True}}))
"""


async def run(
    script: str,
    df_inputs: dict[str, Any] | None = None,
    *,
    df_input_ref: str = "",
    mem_mb: int | None = None,
    timeout_s: int | None = None,
) -> SandboxResult:
    """受限子进程执行 Python 脚本。

    流程：
      1. policy.validate_ast(script) —— AST 白名单校验（禁 import os/sys/...）
      2. 写临时脚本 + 输入 Parquet（df_inputs 或 df_input_ref 直传，缺口 8）
      3. asyncio.create_subprocess_exec 启动子进程
      4. asyncio.wait_for 限时（默认 30s）
      5. 读取输出 Parquet → SandboxResult

    Args:
        script: Python 源代码。
        df_inputs: 输入 DataFrame 字典（可选）。
        df_input_ref: 上一步 SQL 结果的 Parquet 路径（可选，优先于 df_inputs；
            子进程内直接读盘，避免父进程重复加载大结果集）。
        mem_mb: 内存上限 MB（默认取 settings）。
        timeout_s: 超时秒数（默认取 settings）。

    Returns:
        SandboxResult(ok, out_df_ref, stdout, error, mem_peak, elapsed)。
    """
    if mem_mb is None:
        mem_mb = settings.data_sandbox_mem_mb
    if timeout_s is None:
        timeout_s = settings.data_sandbox_timeout

    # df_input_ref 预校验（存在性 + .parquet 后缀，防路径注入）
    if df_input_ref:
        ref_path = Path(df_input_ref)
        if not ref_path.is_file() or ref_path.suffix != ".parquet":
            return SandboxResult(
                ok=False,
                error=f"df_input_ref 无效（不存在或非 Parquet）: {df_input_ref}",
            )

    # 安全层 1：AST 校验
    try:
        validate_ast(script)
    except SandboxViolationError as e:
        return SandboxResult(ok=False, error=str(e))
    except SyntaxError as e:
        return SandboxResult(ok=False, error=f"语法错误: {e}")

    start = time.perf_counter()

    # 准备临时文件
    with tempfile.TemporaryDirectory(prefix="eaide_sandbox_") as tmp_dir:
        tmp = Path(tmp_dir)
        script_path = tmp / "user_script.py"
        input_path = tmp / "input.parquet"
        output_path = tmp / "output.parquet"

        # 写用户脚本
        script_path.write_text(script, encoding="utf-8")

        # 输入 DataFrame：df_input_ref 直传优先（子进程直接读盘），否则写 df_inputs
        has_input = False
        input_ref_str = ""
        if df_input_ref:
            input_ref_str = df_input_ref
            has_input = True
        elif df_inputs:
            for _key, df in df_inputs.items():
                if hasattr(df, "to_parquet"):
                    df.to_parquet(str(input_path), index=False)
                    has_input = True
                    break

        # 组装 worker 脚本
        worker_code = _WORKER_TEMPLATE.format(
            mem_mb=mem_mb,
            input_path=input_ref_str if df_input_ref else (str(input_path) if has_input else ""),
            script_path=str(script_path),
            output_path=str(output_path),
        )
        worker_path = tmp / "worker.py"
        worker_path.write_text(worker_code, encoding="utf-8")

        # 启动子进程
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                str(worker_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(tmp),
            )

            # 限时等待
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_s
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                elapsed = time.perf_counter() - start
                return SandboxResult(
                    ok=False,
                    error=f"执行超时（>{timeout_s}s），已强制终止",
                    elapsed_s=elapsed,
                )

            elapsed = time.perf_counter() - start
            stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            if proc.returncode != 0:
                return SandboxResult(
                    ok=False,
                    error=stderr_text or f"子进程退出码 {proc.returncode}",
                    stdout=stdout_text,
                    elapsed_s=elapsed,
                )

            # 读取输出
            out_ref = ""
            if output_path.exists():
                # 复制到持久化目录
                result_dir = Path(settings.data_result_dir)
                result_dir.mkdir(parents=True, exist_ok=True)
                import uuid

                out_name = f"sandbox_{uuid.uuid4().hex[:12]}.parquet"
                out_ref = str(result_dir / out_name)
                output_path.replace(out_ref)

            return SandboxResult(
                ok=True,
                out_df_ref=out_ref,
                stdout=stdout_text,
                elapsed_s=elapsed,
            )

        except Exception as e:
            elapsed = time.perf_counter() - start
            return SandboxResult(
                ok=False,
                error=f"沙箱执行异常: {type(e).__name__}: {e}",
                elapsed_s=elapsed,
            )
