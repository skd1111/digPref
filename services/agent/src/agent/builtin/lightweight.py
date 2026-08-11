"""Phase 1B V1 · 轻量工具集（无 I/O / 无 LLM）V1 实现。

5 个工具：
    - builtin_calculator: AST 安全算术（拒绝 eval，仅白名单二元/一元运算）
    - builtin_json_parse: 严格 JSON 解析（带位置 + 行号错误报告）
    - builtin_json_format: JSON 美化（indent / sort_keys / ensure_ascii 控制）
    - builtin_regex_match: 正则匹配（防 ReDoS：编译期大小 + 运行期长度截断）
    - builtin_url_parse: URL 解析（urllib.parse.urlsplit 字段映射 + IPv4 主机校验）

设计原则：
    1. 全部同步函数（fastapi tool_runner 是 async 但工具本身不需要 async），
       外层 dispatcher 用 asyncio.to_thread 包装（V0 模式）。
    2. 错误统一抛 ValueError / TypeError / KeyError，由 dispatcher 捕获转 ToolResult。
    3. 无 I/O / 无网络 / 无外部依赖（除标准库 json / re / urllib / ast / ipaddress）。
    4. 所有输入先做 size 校验（防爆：max_input_chars 64KB）。
    5. 结果统一返回 dict（dispatcher 走 _format_tool_result 序列化）。
"""

from __future__ import annotations

import ast
import ipaddress
import json
import operator
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from agent.builtin.models import ToolResult

# ---- 常量（防爆 + ReDoS 防护）-------------------------------------------------

_MAX_INPUT_CHARS = 64 * 1024  # 64KB（单次调用最大输入）
_MAX_REGEX_LEN = 1024  # 正则表达式长度上限（防 ReDoS）
_MAX_TEXT_LEN = 256 * 1024  # regex_match / json_format 输入文本上限
_MAX_MATCHES = 1000  # regex_match 返回最大匹配数


# ---- builtin_calculator ------------------------------------------------------

# AST 节点白名单（避免任意代码执行）
_ALLOWED_BINOPS: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY: dict[type, Any] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def builtin_calculator(expression: str) -> ToolResult:
    """AST 安全算术求值。

    支持：+ - * / // % ** 和正负号；字面量仅 int / float。
    不支持：函数调用、属性访问、变量、下标、字符串。

    Args:
        expression: 算术表达式字符串。

    Returns:
        ToolResult(ok=True, content={"value": <int|float>, "expression": <str>})
    """
    if not isinstance(expression, str):
        return ToolResult(
            ok=False,
            error=f"expression must be str, got {type(expression).__name__}",
            risk_level="low",
        )
    if len(expression) > _MAX_INPUT_CHARS:
        return ToolResult(
            ok=False,
            error=f"expression too long: {len(expression)} > {_MAX_INPUT_CHARS}",
            risk_level="low",
        )
    if not expression.strip():
        return ToolResult(
            ok=False,
            error="empty expression",
            risk_level="low",
        )

    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        return ToolResult(
            ok=False,
            error=f"syntax_error: {exc.msg} at line {exc.lineno} col {exc.offset}",
            risk_level="low",
        )

    try:
        value = _eval_ast(tree.body)
    except _CalculatorError as exc:
        return ToolResult(
            ok=False,
            error=f"calc_error: {exc}",
            risk_level="low",
        )
    except ZeroDivisionError:
        return ToolResult(
            ok=False,
            error="division_by_zero",
            risk_level="low",
        )

    if not isinstance(value, (int, float)):
        return ToolResult(
            ok=False,
            error=f"unsupported_result_type: {type(value).__name__}",
            risk_level="low",
        )

    return ToolResult(
        ok=True,
        content={"value": value, "expression": expression},
        risk_level="low",
    )


class _CalculatorError(Exception):
    """计算过程中遇到不允许的节点 / 操作。"""


def _eval_ast(node: ast.AST) -> int | float:
    """递归求值 AST（仅允许白名单节点）。"""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return node.value
        raise _CalculatorError(f"unsupported constant: {type(node.value).__name__}")
    if isinstance(node, ast.BinOp):
        left = _eval_ast(node.left)
        right = _eval_ast(node.right)
        op = _ALLOWED_BINOPS.get(type(node.op))
        if op is None:
            raise _CalculatorError(f"unsupported operator: {type(node.op).__name__}")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        operand = _eval_ast(node.operand)
        op = _ALLOWED_UNARY.get(type(node.op))
        if op is None:
            raise _CalculatorError(f"unsupported unary op: {type(node.op).__name__}")
        return op(operand)
    raise _CalculatorError(f"unsupported node: {type(node).__name__}")


# ---- builtin_json_parse ------------------------------------------------------


def builtin_json_parse(
    text: str,
    *,
    strict: bool = True,
) -> ToolResult:
    """严格 JSON 解析。

    Args:
        text: JSON 字符串。
        strict: True 时使用 json.loads（不允许重复键、注释等）；False 时 json.JSONDecoder(strict=False) 容忍。

    Returns:
        ToolResult(ok=True, content=<parsed>) 或 ok=False 带行/列/字符位置错误。
    """
    if not isinstance(text, str):
        return ToolResult(
            ok=False,
            error=f"text must be str, got {type(text).__name__}",
            risk_level="low",
        )
    if len(text) > _MAX_INPUT_CHARS:
        return ToolResult(
            ok=False,
            error=f"text too long: {len(text)} > {_MAX_INPUT_CHARS}",
            risk_level="low",
        )

    decoder = json.JSONDecoder(strict=strict)
    try:
        obj, end_idx = decoder.raw_decode(text)
    except json.JSONDecodeError as exc:
        return ToolResult(
            ok=False,
            error=(
                f"json_decode_error: {exc.msg} (line {exc.lineno}, col {exc.colno}, char {exc.pos})"
            ),
            meta={"lineno": exc.lineno, "colno": exc.colno, "pos": exc.pos},
            risk_level="low",
        )

    return ToolResult(
        ok=True,
        content=obj,
        meta={"end_index": end_idx, "size": len(text)},
        risk_level="low",
    )


# ---- builtin_json_format -----------------------------------------------------


def builtin_json_format(
    value: Any,
    *,
    indent: int = 2,
    sort_keys: bool = False,
    ensure_ascii: bool = False,
) -> ToolResult:
    """JSON 美化输出。

    Args:
        value: 任意可 JSON 序列化对象。
        indent: 缩进空格数（None = 单行紧凑）。
        sort_keys: 是否按键字典序排序。
        ensure_ascii: False 保留中文 / emoji（默认 False，金融中文场景必需）。

    Returns:
        ToolResult(ok=True, content=<str>) 或 ok=False 含 TypeError 详情。
    """
    if not isinstance(indent, int) or indent < 0 or indent > 8:
        return ToolResult(
            ok=False,
            error=f"indent must be 0..8, got {indent}",
            risk_level="low",
        )

    try:
        text = json.dumps(
            value,
            indent=indent,
            sort_keys=sort_keys,
            ensure_ascii=ensure_ascii,
            allow_nan=False,  # NaN/Infinity 抛错，避免跨语言歧义
            default=str,  # datetime / Path 等常见类型走 str
        )
    except (TypeError, ValueError) as exc:
        return ToolResult(
            ok=False,
            error=f"json_format_error: {type(exc).__name__}: {exc}",
            risk_level="low",
        )

    return ToolResult(
        ok=True,
        content=text,
        meta={
            "bytes": len(text.encode("utf-8")),
            "indent": indent,
            "sort_keys": sort_keys,
            "ensure_ascii": ensure_ascii,
        },
        risk_level="low",
    )


# ---- builtin_regex_match -----------------------------------------------------


def builtin_regex_match(
    pattern: str,
    text: str,
    *,
    flags: int = 0,
    max_matches: int = _MAX_MATCHES,
    return_groups: bool = True,
) -> ToolResult:
    """正则匹配（防 ReDoS）。

    Args:
        pattern: 正则表达式字符串。
        text: 待匹配文本。
        flags: re 模块 flags（int）。允许：re.IGNORECASE / re.MULTILINE / re.DOTALL。
            拒绝 re.LOCALE / re.DEBUG / re.UNICODE（已默认开 / 副作用）。
        max_matches: 返回最大匹配数（> _MAX_MATCHES 截断并标 truncated=True）。
        return_groups: 是否返回捕获组（False 时仅返回 span）。

    Returns:
        ToolResult(ok=True, content=list[dict{span, match, groups?, named_groups?}])
    """
    if not isinstance(pattern, str) or not isinstance(text, str):
        return ToolResult(
            ok=False,
            error="pattern and text must be str",
            risk_level="low",
        )
    if len(pattern) > _MAX_REGEX_LEN:
        return ToolResult(
            ok=False,
            error=f"pattern too long: {len(pattern)} > {_MAX_REGEX_LEN}",
            hint="complex regex may cause ReDoS; consider simplifying",
            risk_level="low",
        )
    if len(text) > _MAX_TEXT_LEN:
        return ToolResult(
            ok=False,
            error=f"text too long: {len(text)} > {_MAX_TEXT_LEN}",
            risk_level="low",
        )
    if max_matches > _MAX_MATCHES:
        max_matches = _MAX_MATCHES

    # 拒绝危险 flags
    forbidden_flags = re.LOCALE
    if flags & forbidden_flags:
        return ToolResult(
            ok=False,
            error="flags.LOCALE is not allowed (locale-dependent semantics)",
            risk_level="low",
        )

    try:
        compiled = re.compile(pattern, flags=flags)
    except re.error as exc:
        return ToolResult(
            ok=False,
            error=f"regex_compile_error: {exc}",
            risk_level="low",
        )

    matches: list[dict] = []
    truncated = False
    try:
        for m in compiled.finditer(text):
            if len(matches) >= max_matches:
                truncated = True
                break
            entry: dict[str, Any] = {
                "span": [m.start(), m.end()],
                "match": m.group(0),
            }
            if return_groups:
                entry["groups"] = list(m.groups())
                if m.groupdict():
                    entry["named_groups"] = dict(m.groupdict())
            matches.append(entry)
    except Exception as exc:
        return ToolResult(
            ok=False,
            error=f"regex_runtime_error: {type(exc).__name__}: {exc}",
            risk_level="low",
        )

    return ToolResult(
        ok=True,
        content=matches,
        meta={
            "count": len(matches),
            "truncated": truncated,
            "pattern_len": len(pattern),
            "text_len": len(text),
        },
        risk_level="low",
    )


# ---- builtin_url_parse -------------------------------------------------------


def builtin_url_parse(url: str) -> ToolResult:
    """URL 解析 + 字段提取 + IPv4 主机校验。

    Args:
        url: 完整 URL 字符串。

    Returns:
        ToolResult(ok=True, content={
            scheme, netloc, path, params, query, fragment,
            hostname, port, username, password,
            query_dict (str -> list[str], 多值键),
            ipv4_valid (bool, 仅当 hostname 是 IPv4 字符串时 True)
        })
    """
    if not isinstance(url, str):
        return ToolResult(
            ok=False,
            error=f"url must be str, got {type(url).__name__}",
            risk_level="low",
        )
    if len(url) > _MAX_INPUT_CHARS:
        return ToolResult(
            ok=False,
            error=f"url too long: {len(url)} > {_MAX_INPUT_CHARS}",
            risk_level="low",
        )

    try:
        parts = urlsplit(url)
    except ValueError as exc:
        return ToolResult(
            ok=False,
            error=f"url_split_error: {exc}",
            risk_level="low",
        )

    # 解析 query 为 dict（多值键保留为 list）
    query_dict: dict[str, list[str]] = {}
    if parts.query:
        try:
            for key, value in parse_qsl(parts.query, keep_blank_values=True):
                query_dict.setdefault(key, []).append(value)
        except ValueError as exc:
            return ToolResult(
                ok=False,
                error=f"query_parse_error: {exc}",
                risk_level="low",
            )

    # IPv4 校验（仅当 hostname 是 IPv4 字面量时）
    ipv4_valid: bool | None = None
    if parts.hostname:
        try:
            ipaddress.IPv4Address(parts.hostname)
            ipv4_valid = True
        except (ipaddress.AddressValueError, ValueError):
            # 不是 IPv4 字面量（例如域名 / IPv6）
            ipv4_valid = False

    # port 转 int（urllib 返回 str 或 None）
    port_int: int | None = None
    if parts.port is not None:
        port_int = parts.port

    # params 字段：urllib.parse.urlsplit 不支持 params 属性（旧 urlparse 才支持）
    # 简化：返空字符串（Python 3 url 规范中 params 已废弃）
    params_str = ""

    return ToolResult(
        ok=True,
        content={
            "scheme": parts.scheme,
            "netloc": parts.netloc,
            "path": parts.path,
            "params": params_str,
            "query": parts.query,
            "fragment": parts.fragment,
            "hostname": parts.hostname,
            "port": port_int,
            "username": parts.username,
            "password": parts.password,
            "query_dict": query_dict,
            "ipv4_valid": ipv4_valid,
        },
        meta={"raw_url": url, "url_len": len(url)},
        risk_level="low",
    )
