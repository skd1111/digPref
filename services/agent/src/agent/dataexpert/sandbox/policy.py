"""Phase 7 V0 · 沙箱安全策略 —— RestrictedPython AST 校验 + 导入白名单。

安全红线（design §4.2）：
  - 白名单：仅 pandas / numpy / math / datetime / polars
  - 黑名单：禁 os / sys / subprocess / socket / requests / open（文件系统 + 网络全封）
  - AST 层校验：在子进程执行前先做静态分析，拦截恶意 import / 调用

验收硬门槛（design §11）：
  - Python 沙箱 100% 拦截恶意系统调用（os.system / open / socket 全封）
"""
from __future__ import annotations

import ast
from typing import Any


# 允许导入的模块白名单
ALLOWED_IMPORTS = frozenset({
    "pandas",
    "numpy",
    "math",
    "datetime",
    "polars",
    # 允许从白名单模块导入子模块
    "pandas.api",
    "numpy.linalg",
    "datetime.date",
    "datetime.datetime",
})

# 禁止的模块黑名单（优先级高于白名单）
BLOCKED_IMPORTS = frozenset({
    "os", "sys", "subprocess", "socket", "requests",
    "shutil", "pathlib", "glob", "io", "builtins",
    "importlib", "ctypes", "signal", "threading",
    "multiprocessing", "http", "urllib", "ftplib",
    "smtplib", "telnetlib", "pickle", "shelve",
    "marshal", "code", "codeop", "compile",
    "eval", "exec", "compileall", "py_compile",
})

# 禁止的内置函数调用
BLOCKED_CALLS = frozenset({
    "open", "exec", "eval", "compile", "__import__",
    "globals", "locals", "getattr", "setattr", "delattr",
    "breakpoint", "exit", "quit", "input",
    "memoryview", "bytearray",
})

# 禁止的属性访问（防沙箱逃逸）
BLOCKED_ATTRS = frozenset({
    "__subclasses__", "__bases__", "__mro__",
    "__class__", "__globals__", "__code__",
    "__builtins__", "__import__", "__loader__",
    "__spec__", "__dict__",
})


class SandboxViolationError(Exception):
    """沙箱安全策略违规。"""

    def __init__(self, message: str, node: ast.AST | None = None) -> None:
        self.node = node
        lineno = getattr(node, "lineno", "?") if node else "?"
        super().__init__(f"沙箱安全违规 (line {lineno}): {message}")


def validate_ast(script: str) -> None:
    """AST 层安全校验 —— 在子进程执行前静态分析。

    检查项：
      1. import 白名单校验
      2. 禁止的内置函数调用
      3. 禁止的属性访问（防沙箱逃逸）
      4. 禁止 f-string 中的表达式注入（V1 接力）

    Args:
        script: Python 源代码。

    Raises:
        SandboxViolationError: 检测到违规。
        SyntaxError: 语法错误。
    """
    tree = ast.parse(script)

    for node in ast.walk(tree):
        # 1. import 校验
        if isinstance(node, ast.Import):
            for alias in node.names:
                _check_import(alias.name, node)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            _check_import(module, node)

        # 2. 禁止的内置函数调用
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in BLOCKED_CALLS:
                    raise SandboxViolationError(
                        f"禁止调用内置函数 '{node.func.id}'", node
                    )
            elif isinstance(node.func, ast.Attribute):
                # 检查 os.system / subprocess.run 等
                attr_name = node.func.attr
                if attr_name in ("system", "popen", "exec", "execvp", "spawn"):
                    raise SandboxViolationError(
                        f"禁止调用危险方法 '.{attr_name}()'", node
                    )

        # 3. 禁止的属性访问
        elif isinstance(node, ast.Attribute):
            if node.attr in BLOCKED_ATTRS:
                raise SandboxViolationError(
                    f"禁止访问属性 '{node.attr}'（防沙箱逃逸）", node
                )


def _check_import(module_name: str, node: ast.AST) -> None:
    """校验 import 是否在白名单内。"""
    # 取顶层模块名
    top_level = module_name.split(".")[0] if module_name else ""

    # 黑名单优先
    if top_level in BLOCKED_IMPORTS or module_name in BLOCKED_IMPORTS:
        raise SandboxViolationError(
            f"禁止导入模块 '{module_name}'（安全黑名单）", node
        )

    # 白名单校验
    if top_level not in ALLOWED_IMPORTS and module_name not in ALLOWED_IMPORTS:
        raise SandboxViolationError(
            f"模块 '{module_name}' 不在白名单内"
            f"（允许：{', '.join(sorted(ALLOWED_IMPORTS))}）",
            node,
        )


def is_safe(script: str) -> tuple[bool, str]:
    """快速检查脚本是否安全（不抛异常版本）。

    Returns:
        (is_safe, error_message)
    """
    try:
        validate_ast(script)
        return True, ""
    except SandboxViolationError as e:
        return False, str(e)
    except SyntaxError as e:
        return False, f"语法错误: {e}"
