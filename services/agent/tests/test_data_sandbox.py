"""Phase 7 V0 · 沙箱安全测试 —— 恶意脚本 100% 拦截。

验收硬门槛（design §11）：
  - Python 沙箱 100% 拦截恶意系统调用（os.system / open / socket 全封）
"""

import pytest
from agent.dataexpert.sandbox.policy import (
    SandboxViolationError,
    is_safe,
    validate_ast,
)

# ---- 恶意 import 全封 ----------------------------------------------------------

_MALICIOUS_IMPORTS = [
    "import os",
    "import sys",
    "import subprocess",
    "import socket",
    "import requests",
    "import shutil",
    "import pathlib",
    "import importlib",
    "import ctypes",
    "from os import system",
    "from subprocess import run",
    "from socket import socket",
    "import http.client",
    "import urllib.request",
]


@pytest.mark.parametrize("script", _MALICIOUS_IMPORTS)
def test_blocked_imports(script: str):
    """恶意 import 100% 拦截。"""
    with pytest.raises(SandboxViolationError):
        validate_ast(script)


# ---- 恶意调用全封 ----------------------------------------------------------------

_MALICIOUS_CALLS = [
    "open('/etc/passwd')",
    "exec('import os')",
    "eval('1+1')",
    "compile('x=1', '', 'exec')",
    "__import__('os')",
    "globals()",
    "breakpoint()",
]


@pytest.mark.parametrize("script", _MALICIOUS_CALLS)
def test_blocked_calls(script: str):
    """恶意内置函数调用 100% 拦截。"""
    with pytest.raises(SandboxViolationError):
        validate_ast(script)


# ---- 沙箱逃逸属性全封 ------------------------------------------------------------

_ESCAPE_ATTRS = [
    "x.__subclasses__",
    "x.__globals__",
    "x.__builtins__",
    "x.__class__.__mro__",
    "func.__code__",
]


@pytest.mark.parametrize("script", _ESCAPE_ATTRS)
def test_blocked_attrs(script: str):
    """沙箱逃逸属性访问 100% 拦截。"""
    with pytest.raises(SandboxViolationError):
        validate_ast(script)


# ---- 白名单模块允许 ----------------------------------------------------------------

_ALLOWED_SCRIPTS = [
    "import pandas as pd\ndf = pd.DataFrame({'a': [1,2,3]})",
    "import numpy as np\narr = np.array([1,2,3])",
    "import math\nresult = math.sqrt(16)",
    "from datetime import datetime\nnow = datetime.now()",
    "import polars as pl",
]


@pytest.mark.parametrize("script", _ALLOWED_SCRIPTS)
def test_allowed_scripts(script: str):
    """白名单模块正常通过。"""
    validate_ast(script)  # 不应抛异常


# ---- is_safe 接口 ------------------------------------------------------------------


def test_is_safe_returns_tuple():
    """is_safe 返回 (bool, str) 元组。"""
    ok, err = is_safe("import pandas as pd")
    assert ok is True
    assert err == ""

    ok2, err2 = is_safe("import os")
    assert ok2 is False
    assert "os" in err2


def test_syntax_error():
    """语法错误被捕获。"""
    ok, err = is_safe("def foo(")
    assert ok is False
    assert "语法错误" in err


# ---- DataFrame 链路（缺口 8：SQL 结果注入沙箱） ----------------------------


async def test_sandbox_with_df_input_ref(tmp_path, monkeypatch):
    """df_input_ref 注入 df 变量；脚本输出 result → 新 Parquet ref。"""
    import pandas as pd
    from agent.config import settings

    monkeypatch.setattr(settings, "data_result_dir", str(tmp_path / "results"))
    from agent.dataexpert.sandbox.executor import run
    from agent.dataexpert.storage import save_result_parquet

    df = pd.DataFrame({"a": [1, 2, 3]})
    ref = save_result_parquet(df, "t_src")

    res = await run("result = df.assign(b=df['a'] * 2)", df_input_ref=ref)
    assert res.ok, res.error
    assert res.out_df_ref and res.out_df_ref != ref
    out = pd.read_parquet(res.out_df_ref)
    assert list(out.columns) == ["a", "b"]
    assert int(out["b"].sum()) == 12


async def test_sandbox_df_input_ref_must_exist():
    """不存在的 df_input_ref → 报错不崩溃。"""
    from agent.dataexpert.sandbox.executor import run

    res = await run("x = 1", df_input_ref="no/such/file.parquet")
    assert res.ok is False
    assert res.error
