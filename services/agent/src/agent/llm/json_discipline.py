"""llm/json_discipline.py —— 共享 JSON 输出纪律与容错解析。

四层防御（spec §4.5）：
1. API 参数（response_format / format）—— 由各客户端负责
2. Prompt 纪律段 —— JSON_DISCIPLINE / json_instructions()
3. 代码后处理 —— strip_think_blocks() + extract_json() / extract_sql()
4. 重试自纠错 —— parse_with_retry()：解析失败把错误喂回模型

实现来源：doc_review/classifier.py 的容错修复链 + private_llm/codenav 的 think 剥离。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("agent.llm.json_discipline")

JSON_DISCIPLINE = """# 输出约束（严格的数据结构化引擎）
- 仅输出 JSON 字符串，禁止任何解释、问候、Markdown 标记（不要 ``` 代码块）
- 空值用 null，布尔值用 true/false，字符串必须用双引号
- 字符串内的换行符转义为 \\n，禁止未转义的双引号（\\"）或反斜杠
- 输出必须以 { 开头、以 } 结尾，且是完整闭合的合法 JSON
- 输出 Schema：{{schema}}"""


def json_instructions(schema: str, *, style: str = "markdown", extra: str = "") -> str:
    """按模型族生成输出纪律段（第二层 Prompt 约束）。

    style:
      - "markdown"（默认）：Markdown 负向约束 + 强规则，适合开源/通用模型
      - "xml"（预留）：XML 标签包裹，适配 Claude 类模型
      - "none"：已开 response_format/format 强约束时省略
    """
    if style == "none":
        return ""
    if style == "xml":
        return (
            "<output_constraints>\n"
            "1. 只输出一个合法 JSON 对象/数组。\n"
            "2. 禁止任何解释、问候、Markdown 代码块（不要 ```json）。\n"
            "3. 空值用 null，字符串必须用双引号，换行转义为 \\n。\n"
            "4. 输出必须以 { 或 [ 开头并以 } 或 ] 结尾。\n"
            f"<schema>{schema}</schema>\n"
            "</output_constraints>"
        )
    discipline = JSON_DISCIPLINE.replace("{{schema}}", schema)
    return discipline + (("\n" + extra) if extra else "")


# ---- think 剥离 ------------------------------------------------------------

_THINK_BLOCK_RE = re.compile(
    r"<\s*THINK\s*>.*?<\s*/\s*THINK\s*>|```think\s*.*?```",
    re.IGNORECASE | re.DOTALL,
)


def strip_think_blocks(text: str) -> str:
    """剥离 <think>…</think> 与 ```think … ``` 块（含未闭合尾巴）。"""
    if not text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    lowered = cleaned.lower()
    for marker in ("<think", "```think"):
        idx = lowered.rfind(marker)
        if idx != -1:
            cleaned = cleaned[:idx]
    return cleaned.strip()


# ---- 容错 JSON 提取 ---------------------------------------------------------


def extract_json(text: str, *, want: str = "any") -> Any | None:
    """从模型输出提取 JSON；失败返回 None（触发第四层重试）。

    want: "object" | "array" | "any"
    流程：strip_think_blocks → 剥围栏 → 定位 {}/[] 边界 → 严格 loads
         → 容错修复（尾逗号 / 未转义引号 / 裸换行 / 截断补全）。
    """
    if not text:
        return None
    cleaned = strip_think_blocks(str(text))
    cleaned = _strip_fence(cleaned)
    start, end = _find_bounds(cleaned, want)
    if start < 0:
        return None
    if end <= start:
        # 输出被截断（无闭合括号）→ 交给容错修复链补全
        return _loads_lenient(cleaned[start:])
    return _loads_lenient(cleaned[start : end + 1])


def _strip_fence(text: str) -> str:
    if not text:
        return text
    if text.startswith("```"):
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
    if text.rstrip().endswith("```"):
        text = text.rstrip()[:-3]
    return text.strip()


def _find_bounds(text: str, want: str) -> tuple[int, int]:
    brace = text.find("{")
    bracket = text.find("[")
    if want == "object":
        return brace, text.rfind("}")
    if want == "array":
        return bracket, text.rfind("]")
    if brace == -1:
        return bracket, text.rfind("]")
    if bracket == -1:
        return brace, text.rfind("}")
    if brace < bracket:
        return brace, text.rfind("}")
    return bracket, text.rfind("]")


def _loads_lenient(candidate: str) -> Any | None:
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))
    except json.JSONDecodeError:
        pass
    repaired = _repair_string_escapes(candidate)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", repaired))
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_balance_close(repaired))
    except json.JSONDecodeError:
        return None


def _is_valid_continuation(s: str, j: int) -> bool:
    k = j
    while k < len(s) and s[k] in " \t\r\n":
        k += 1
    if k >= len(s):
        return False
    if s[k] in ",:}]":
        return True
    try:
        json.JSONDecoder().raw_decode(s, k)
        return True
    except json.JSONDecodeError:
        return False


def _repair_string_escapes(s: str) -> str:
    out: list[str] = []
    in_str = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if not in_str:
            if c == '"':
                in_str = True
            out.append(c)
            i += 1
            continue
        if c == "\\":
            out.append(s[i : i + 2])
            i += 2
            continue
        if c == '"':
            j = i + 1
            while j < n and s[j] in " \t\r\n":
                j += 1
            if j >= n or s[j] in ",:}]" or _is_valid_continuation(s, j):
                in_str = False
                out.append(c)
            else:
                out.append('\\"')
            i += 1
        elif c == "\n" or c == "\r":
            out.append("\\n")
            i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _balance_close(s: str) -> str:
    stack: list[str] = []
    in_str = False
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c in "{[":
                stack.append("}" if c == "{" else "]")
            elif c in "}]" and stack:
                stack.pop()
        i += 1
    tail = ('"' if in_str else "") + "".join(reversed(stack))
    return s + tail


# ---- SQL 提取 ---------------------------------------------------------------


def extract_sql(text: str) -> str:
    """提取纯 SQL：剥 think/围栏，去掉「好的」「以下是 SQL」等前缀。"""
    cleaned = strip_think_blocks(text or "")
    # 优先取 ```sql 围栏内的内容
    fence = re.search(r"```(?:sql)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        return fence.group(1).strip()
    # 无围栏：跳过前缀解释，从 SQL 关键字起截取
    start = re.search(r"(?i)\b(?:SELECT|WITH|INSERT|UPDATE|DELETE|CREATE)\b", cleaned)
    if start:
        return cleaned[start.start() :].strip()
    return cleaned.strip()


# ---- 第四层：重试自纠错 -------------------------------------------------------


RETRY_PROMPT = """你上一次输出的内容无法被解析为 JSON。
原始输出：{last_output}
请修复格式错误，重新只输出合法 JSON，不要包含任何道歉或解释。"""


async def parse_with_retry(
    call: Callable[[str, str], Awaitable[Any]],
    parse: Callable[[Any], Any],
    *,
    max_retries: int = 1,
) -> Any:
    """解析失败把错误喂回模型重试（每节点最多 max_retries 次）。

    call(retry_hint, last_output) -> 模型原始输出（str 或已解析对象）
    parse(output) -> 解析结果（None 表示失败）
    """
    last = ""
    for attempt in range(max_retries + 1):
        hint = RETRY_PROMPT.replace("{last_output}", str(last)[:2000]) if attempt else ""
        text = await call(hint, last)
        data = parse(text)
        if data is not None:
            if attempt:
                logger.info("parse_with_retry ok after %d retry", attempt)
            return data
        logger.warning(
            "parse_with_retry attempt=%d/%d 解析失败，原始输出: %s",
            attempt,
            max_retries,
            str(text)[:2000],
        )
        last = text
    logger.error("parse_with_retry 全部 %d 次尝试失败，返回 None", max_retries + 1)
    return None
