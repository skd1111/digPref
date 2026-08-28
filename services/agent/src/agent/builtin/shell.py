"""Phase 1B V2 · shell 工具（Python 原生兜底实现）。

用途：Agent 独立运行（无 Tauri 运行时注入）时，dispatcher 对 `builtin_shell`
走本实现；桌面壳集成时可切换到 Rust 端 `builtin_shell`（Tauri Command）。

安全策略（与 Rust 端 execute_shell 严格镜像）：
    1. 危险操作符拦截（; & | < > ` $ ( ) 换行 等）
    2. 首 token 白名单（支持 `git*` 通配前缀）
    3. 长度上限 4096
    4. 超时强杀（subprocess.TimeoutExpired → timed_out=True）
"""

from __future__ import annotations

import asyncio
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from agent.builtin.models import ToolResult

# 危险操作符 —— 出现即拒绝（防止命令注入 / 管道 / 重定向）
DANGEROUS_SHELL_CHARS: tuple[str, ...] = (
    ";",
    "&",
    "|",
    "<",
    ">",
    "`",
    "$",
    "(",
    ")",
    "{",
    "}",
    "\n",
    "\r",
    "\x00",
)

# shell 命令最大字节数
SHELL_MAX_BYTES: int = 4096

# 危险操作符的可操作替代建议（根治 BUGFIX #165）。
# 背景：此前拦截只回一句 "dangerous_operator: '&' not allowed"，不告诉模型该怎么办。
# 实测模型被这种沉默反馈带进沟里 —— 一次任务里连试 22 轮 shell 变体（换 python /
# 写 .bat 包装 / cmd /c / ^ 转义空格），把 24 轮编排预算烧光，PPT 一页没做。
# 拦截时附一条明确的替代路径，能省掉后面一整串瞎试。
_OPERATOR_HINTS: dict[str, str] = {
    "&": "禁止 `&` / `&&` 串联：拆成多次 builtin_shell 调用，每次一条命令。",
    "|": "禁止管道：先用 builtin_shell 取全量输出，再用 builtin_grep 过滤。",
    ";": "禁止 `;` 串联：拆成多次 builtin_shell 调用。",
    ">": "禁止重定向：用 builtin_write_file 写文件。",
    "<": "禁止输入重定向：用 builtin_read_file 读内容后作为参数传入。",
    "(": "禁止子 shell / 括号分组（含 cmd 的 `if exist (...)`）："
    "目录与文件存在性用 builtin_stat_file / builtin_list_dir 判断。",
    ")": "禁止子 shell / 括号分组：存在性判断用 builtin_stat_file / builtin_list_dir。",
    "`": "禁止命令替换：分两步做 —— 先取输出，再把结果作为参数传入。",
    "$": "禁止变量展开 / 命令替换：需要环境变量请显式写出完整值。",
}

# 通用提示：这些坑在 Windows 上格外常见（路径含空格 + cmd 引号规则）
_GENERAL_SHELL_HINT: str = (
    "另外：列目录用 builtin_list_dir、查文件用 builtin_find、读文件用 builtin_read_file —— "
    "它们不受 shell 引号规则影响，路径含空格也不会出错，优先用它们而不是 dir / where / type。"
)


def _operator_hint(ch: str) -> str:
    """拦截某个危险操作符时给出的可操作建议。"""
    specific = _OPERATOR_HINTS.get(ch)
    return f"{specific} {_GENERAL_SHELL_HINT}" if specific else _GENERAL_SHELL_HINT


def _first_token(command: str) -> str:
    """取命令首 token（白名单校验用）。

    Windows 必须用 ``posix=False``（根治 BUGFIX #166）：POSIX 模式把 ``\\`` 当转义符，
    无引号的 Windows 路径会被啃成不可辨认的样子 ——

        输入  C:\\Users\\79834\\AppData\\Local\\Enterprise AI IDE\\python.exe
        posix=True  → 'C:Users79834AppDataLocalEnterprise'   ← 审计里的报错原文
        posix=False → 'C:\\Users\\79834\\AppData\\Local\\Enterprise'

    后果是模型给了正确的白名单前缀也会被判 command_not_allowed。
    ``posix=False`` 会保留外层引号，这里手动剥掉。
    """
    try:
        parts = shlex.split(command, posix=sys.platform != "win32")
    except ValueError:
        # 引号不配对等词法错误：退化成按空白切分，让白名单照常判断
        parts = command.split()
    if not parts:
        return ""
    return parts[0].strip('"').strip("'")


# ANSI 转义序列（pwsh 的彩色报错会把它们混进 stderr）
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]|\[[0-9;]+m")


def _strip_ansi(text: str) -> str:
    """剥掉 ANSI 转义码（根治 BUGFIX #166）。

    pwsh 输出彩色错误，未剥离时 error 字段长这样：
    ``exit_code=1: [31;1mResourceUnavailable: ...`` —— 噪声挤占了有效信息，
    模型还得先看懂控制码。
    """
    return _ANSI_RE.sub("", text)


def _stderr_digest(stderr: str, stdout: str, limit: int = 300) -> str:
    """从 stderr（空则退回 stdout）里取一段摘要放进 error 字段。

    命令失败时模型首先读 error —— 把真实原因（"python3 不是内部或外部命令"）
    摆到 error 里，而不是让它自己去 content.stderr 里翻。
    """
    text = (stderr or "").strip() or (stdout or "").strip()
    if not text:
        return ""
    flat = " ".join(_strip_ansi(text).split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _win_shell_argv(command: str) -> list[str]:
    """Windows 平台 shell 选择：装了 pwsh 用 pwsh，否则回退 cmd。

    为什么优先 pwsh（2026-08-27）：cmd 的引号规则是这次 BUGFIX #165 的直接推手 ——
    路径含空格（``Enterprise AI IDE``）时模型反复在引号 / ``^`` 转义上翻车，
    连试 22 轮。pwsh 的引号处理规则一致得多，且 ``-File`` / ``&`` 调用语义清晰。

    ``-NoProfile`` 必须带：用户 profile 可能改编码 / 加别名 / 打印横幅，
    污染 stdout 会让模型读到垃圾。``-NonInteractive`` 防止命令等待输入挂死到超时。

    探测结果缓存 —— shell 工具可能被高频调用，不必每次都查 PATH。
    """
    global _WIN_PWSH_PATH
    if _WIN_PWSH_PATH is None:
        # pwsh = PowerShell 7+（独立安装）；不回退 powershell.exe（5.1）——
        # 它的编码默认值与参数解析和 pwsh 有差异，混用反而多一种不确定性。
        _WIN_PWSH_PATH = shutil.which("pwsh") or ""
    if _WIN_PWSH_PATH:
        return [_WIN_PWSH_PATH, "-NoProfile", "-NonInteractive", "-Command", command]
    return ["cmd", "/C", command]


# pwsh 探测结果缓存：None = 未探测，"" = 未安装，其他 = 可执行文件路径
_WIN_PWSH_PATH: str | None = None


def current_shell_name() -> str:
    """当前 shell 名称：``pwsh`` / ``cmd`` / ``sh``。

    暴露给模型（进 ToolResult.content 与工具描述）—— 不同 shell 的内建命令与
    别名差异很大，模型不知道就会写出跑不通的命令。
    """
    if sys.platform != "win32":
        return "sh"
    _win_shell_argv("")  # 触发探测（结果缓存）
    return "pwsh" if _WIN_PWSH_PATH else "cmd"


# 各 shell 的写法提醒 —— 拼进工具描述，让模型一开始就写对
_SHELL_SYNTAX_NOTES: dict[str, str] = {
    "pwsh": (
        "Shell is PowerShell 7 (pwsh). Note: `where` is an alias for Where-Object — use "
        "`Get-Command <name>` to locate an executable. `dir`/`type` are aliases for "
        "Get-ChildItem/Get-Content with different output formats — prefer builtin_list_dir / "
        "builtin_read_file. `echo a b` prints two lines (arguments, not one string); quote it "
        'as `echo "a b"` if you need one line.'
    ),
    "cmd": (
        "Shell is Windows cmd. Quoting is fragile with paths containing spaces — wrap the whole "
        "path in double quotes and do NOT use `^` escaping. Prefer builtin_list_dir / "
        "builtin_read_file / builtin_stat_file over dir / type / if-exist."
    ),
    "sh": "Shell is POSIX sh. Use forward slashes and standard quoting.",
}


def shell_syntax_note() -> str:
    """当前 shell 的写法提醒（供 registry 工具描述拼接）。"""
    return _SHELL_SYNTAX_NOTES.get(current_shell_name(), "")


async def builtin_shell(
    command: str = "",
    *,
    argv: list[str] | None = None,
    cwd: str | None = None,
    allowed_prefixes: list[str] | None = None,
    timeout_sec: int = 30,
    allow_nonzero_exit: bool = False,
) -> ToolResult:
    """执行白名单命令（critical 风险 —— 永远需要 HITL 审批后调用）。

    两种调用形式，**优先用 argv**：

    ``argv``（推荐，根治 BUGFIX #166）
        直接以参数数组执行，**完全绕过 shell** —— 没有引号规则、没有转义、
        没有操作符解释。带空格的路径原样放进一个元素即可：

            argv=["C:\\\\Program Files\\\\python.exe", "guard.py"]

        这是唯一能可靠调用「路径含空格的可执行文件」的方式。此前只有 command
        形式，模型为了拼对引号连试 22 轮：cmd 下直接调用不成立，pwsh 下唯一正确的
        ``& "路径"`` 写法又被危险操作符拦截 —— 被逼进了死角。
        argv 形式同时消除了整个操作符注入面（数组元素不会被 shell 解释）。

    ``command``（兼容保留）
        单条命令字符串，经 shell 执行。需要 shell 特性（内建命令、通配符展开）
        时才用；不得含危险操作符。

    Args:
        command: 命令字符串。与 ``argv`` 二选一。
        argv: 参数数组（首元素为可执行文件）。与 ``command`` 二选一，优先生效。
        cwd: 工作目录。**必须用它来切目录** —— ``cd`` 只影响当前那一次调用的
            子进程，下一次调用又是全新进程，所以 ``cd x`` 后再调用等于没切
            （实测模型在此白花 3 轮）。
        allowed_prefixes: 首 token 白名单，支持 ``git*`` 通配前缀。
        timeout_sec: 超时秒数，超时强杀并返回 ``exit_code=124``。
        allow_nonzero_exit: 非零退出码是否算成功。默认 ``False``。
            置 ``True`` 用于「非零退出是正常语义」的命令 —— ``findstr`` / ``grep``
            无匹配返 1、``diff`` 有差异返 1 等。

    Returns:
        ToolResult。``ok`` 语义（BUGFIX #165）：**命令是否达成目标**，
        即进程正常启动 **且** ``exit_code == 0`` **且** 未超时。
    """
    # ---- argv 形式（推荐路径）：绕过 shell，无需操作符校验 ----
    if argv:
        if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
            return ToolResult(
                ok=False,
                error="invalid_argv: must be a list of strings",
                hint='形如 argv=["C:\\\\Program Files\\\\python.exe", "script.py"]',
                risk_level="critical",
            )
        argv = [a for a in argv if a != ""]
        if not argv:
            return ToolResult(ok=False, error="empty_command", risk_level="critical")
        total = sum(len(a) for a in argv)
        if total > SHELL_MAX_BYTES:
            return ToolResult(
                ok=False,
                error=f"command_too_long: {total} > {SHELL_MAX_BYTES}",
                risk_level="critical",
            )
        first = argv[0]
        display = " ".join(argv)
    else:
        trimmed = (command or "").strip()
        if not trimmed:
            return ToolResult(
                ok=False,
                error="empty_command",
                hint="传 command 字符串，或（推荐）传 argv 参数数组绕过 shell 引号规则。",
                risk_level="critical",
            )
        if len(trimmed) > SHELL_MAX_BYTES:
            return ToolResult(
                ok=False,
                error=f"command_too_long: {len(trimmed)} > {SHELL_MAX_BYTES}",
                risk_level="critical",
            )
        for ch in DANGEROUS_SHELL_CHARS:
            if ch in trimmed:
                return ToolResult(
                    ok=False,
                    error=f"dangerous_operator: {ch!r} not allowed in shell command",
                    hint=_operator_hint(ch),
                    risk_level="critical",
                )
        first = _first_token(trimmed)
        display = trimmed

    allowed = allowed_prefixes or []
    if allowed and not any(
        first.startswith(p.rstrip("*")) if p.endswith("*") else first == p for p in allowed
    ):
        return ToolResult(
            ok=False,
            error=f"command_not_allowed: {first} (allowed: {allowed})",
            hint=_GENERAL_SHELL_HINT,
            risk_level="critical",
        )

    # 工作目录校验：不存在就直接报错，别让子进程抛难懂的 OSError
    run_cwd: str | None = None
    if cwd:
        cwd_path = Path(cwd)
        if not cwd_path.is_dir():
            return ToolResult(
                ok=False,
                error=f"cwd_not_a_directory: {cwd}",
                hint="cwd 必须是已存在的目录。先用 builtin_stat_file / builtin_list_dir 确认。",
                risk_level="critical",
            )
        run_cwd = str(cwd_path)

    timeout = timeout_sec if timeout_sec and timeout_sec > 0 else 30
    # 实际 exec 的 argv：argv 形式原样执行（不经 shell）；command 形式包一层 shell
    if argv:
        exec_argv = list(argv)
    elif sys.platform == "win32":
        exec_argv = _win_shell_argv(display)
    else:
        exec_argv = ["/bin/sh", "-c", display]

    def _run() -> tuple[int, str, str, bool]:
        """同步执行（无流式上下文时走这条）。"""
        try:
            proc = subprocess.run(
                exec_argv,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
                cwd=run_cwd,
            )
            return (
                proc.returncode,
                proc.stdout or "",
                proc.stderr or "",
                False,
            )
        except subprocess.TimeoutExpired as exc:
            return (
                124,
                (exc.stdout or "").decode("utf-8", "replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or ""),
                (exc.stderr or "").decode("utf-8", "replace")
                if isinstance(exc.stderr, bytes)
                else (exc.stderr or ""),
                True,
            )
        except OSError as exc:
            raise exc

    # 执行过程可视化（阶段三）：有 call_id 上下文（dispatcher 绑定）时走异步流式，
    # 输出逐批推前端；无上下文（单测 / 直调）保持原一次性执行路径，行为不变。
    from agent.builtin.exec_context import current_call_id, current_run_id

    _call_id_ctx = current_call_id()
    if _call_id_ctx:
        try:
            exit_code, stdout, stderr, timed_out = await _run_streaming(
                exec_argv, timeout, _call_id_ctx, current_run_id(), run_cwd
            )
        except OSError as exc:
            return ToolResult(
                ok=False,
                error=f"spawn_failed: {type(exc).__name__}: {exc}",
                risk_level="critical",
            )
    else:
        try:
            exit_code, stdout, stderr, timed_out = await asyncio.to_thread(_run)
        except OSError as exc:
            return ToolResult(
                ok=False,
                error=f"spawn_failed: {type(exc).__name__}: {exc}",
                risk_level="critical",
            )

    content = {
        "command": display,
        # argv 形式时回显数组，让模型确认自己走的是免引号路径
        **({"argv": list(argv)} if argv else {}),
        **({"cwd": run_cwd} if run_cwd else {}),
        # 实际使用的 shell（2026-08-27）：模型必须知道自己在跟谁说话。
        # pwsh 把 where / dir / type 设成了别名（`where` → Where-Object，不是
        # where.exe），语法与 cmd 有实质差异；不告知就会写出跑不通的命令。
        # argv 形式不经 shell → 报 "none"。
        "shell": "none" if argv else current_shell_name(),
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": timed_out,
    }
    # ok = 命令达成目标（根治 BUGFIX #165），不是「进程成功启动」。
    # 超时一律算失败（即使 allow_nonzero_exit=True）—— 被强杀的命令没有产出。
    if timed_out:
        return ToolResult(
            ok=False,
            content=content,
            error=f"timeout: killed after {timeout}s",
            hint="命令超时。拆成更小步骤，或提高 timeout_sec（上限受 dispatcher 约束）。",
            risk_level="critical",
        )
    if exit_code != 0 and not allow_nonzero_exit:
        digest = _stderr_digest(stderr, stdout)
        return ToolResult(
            ok=False,
            content=content,
            error=f"exit_code={exit_code}" + (f": {digest}" if digest else ""),
            # argv 形式已经绕过了引号问题，再劝它换工具没意义 —— 改提真实排查方向
            hint=(
                "命令返回非零退出码。先确认可执行文件与参数是否正确；"
                "若非零退出是该命令的正常语义（findstr/grep 无匹配、diff 有差异），"
                "显式传 allow_nonzero_exit=true。"
                if argv
                else _GENERAL_SHELL_HINT
            ),
            risk_level="critical",
        )
    return ToolResult(
        ok=True,
        content=content,
        risk_level="critical",
        # UI 摘要（执行过程可视化）：前端工具卡副标题直接展示，大输出体仍只进 LLM 上下文
        ui={"summary": f"退出码 {exit_code}，输出 {len(stdout.splitlines())} 行"},
    )


# ---- 流式执行（执行过程可视化 · 阶段三）-----------------------------------
#
# 白名单 / 危险操作符拦截在入口已完成；超时强杀 / pwsh 优先等语义与 _run
# 严格镜像，唯一差异：输出逐批 emit shell_chunk（SSE → 前端输出面板）。

# 批处理窗口：行数或时间任一命中即发一批（throttle SSE 消息量，方案 20.2）
_CHUNK_FLUSH_LINES = 40
_CHUNK_FLUSH_SEC = 0.2
# 单条事件内容上限（防 base64 / 单行巨无霸输出把事件体炸掉）
_CHUNK_MAX_CHARS = 8192


async def _run_streaming(
    exec_argv: list[str],
    timeout: int,
    call_id: str,
    run_id: str | None,
    cwd: str | None = None,
) -> tuple[int, str, str, bool]:
    """流式执行：边跑边发 shell_chunk，结束帧带 exit_code。

    ``exec_argv`` 由调用方算好（argv 形式 = 原样；command 形式 = 已包好 shell），
    这里不再重复决定用哪个 shell —— 否则两条路径的语义迟早漂移（BUGFIX #166）。
    """
    from agent.builtin.events import emit_shell_chunk

    proc = await asyncio.create_subprocess_exec(
        *exec_argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout_buf: list[str] = []
    stderr_buf: list[str] = []

    async def _pump(stream: asyncio.StreamReader, buf: list[str], tag: str) -> None:
        pending: list[str] = []
        last_flush = time.monotonic()
        while True:
            try:
                line = await stream.readline()
            except Exception:
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace")
            buf.append(text)
            pending.append(text)
            if (
                len(pending) >= _CHUNK_FLUSH_LINES
                or time.monotonic() - last_flush >= _CHUNK_FLUSH_SEC
            ):
                await emit_shell_chunk(
                    call_id=call_id,
                    chunk="".join(pending)[:_CHUNK_MAX_CHARS],
                    stream=tag,
                    run_id=run_id,
                )
                pending.clear()
                last_flush = time.monotonic()
        if pending:
            await emit_shell_chunk(
                call_id=call_id,
                chunk="".join(pending)[:_CHUNK_MAX_CHARS],
                stream=tag,
                run_id=run_id,
            )

    pump_out = asyncio.create_task(_pump(proc.stdout, stdout_buf, "stdout"))
    pump_err = asyncio.create_task(_pump(proc.stderr, stderr_buf, "stderr"))

    timed_out = False
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        timed_out = True
        try:
            proc.kill()
        except OSError:
            pass
        await proc.wait()

    await asyncio.gather(pump_out, pump_err, return_exceptions=True)
    stdout = "".join(stdout_buf)
    stderr = "".join(stderr_buf)
    exit_code = 124 if timed_out else (proc.returncode if proc.returncode is not None else -1)
    # 结束帧（空 chunk + exit_code）：前端据此关闭该工具卡的输出流式态；
    # best-effort，发不出不影响结果（tool_result 仍带完整 stdout/stderr）
    try:
        await emit_shell_chunk(
            call_id=call_id, chunk="", stream="stdout", exit_code=exit_code, run_id=run_id
        )
    except Exception:
        pass
    return exit_code, stdout, stderr, timed_out
