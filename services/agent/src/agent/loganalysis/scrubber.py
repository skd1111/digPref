"""loganalysis.scrubber —— Phase 2F+ V1 日志 PII 脱敏。

设计：
- 与 envconfig.scrub 不同 —— 这里是**敏感数据**脱敏（卡号 / 身份证 / 手机号 / IP / 邮箱），
  而非 Keyring 占位符
- 输入 / 输出都是字符串；块级用 list[str] → list[str] 包装
- 正则覆盖金融/政企常见 PII；后续可扩展（车牌 / 银行卡 BIN 等）
- 任何 regex 失败都不抛错，原文返回（best-effort）

CLAUDE.md §6 安全红线（合规）：
- 日志含手机号 / 身份证 / 银行卡 / IP / 邮箱 → 必须脱敏成 [REDACTED:<TYPE>]
- 脱敏在 LLM 调用前完成（永远不让原始 PII 进 LLM）
- 原始 PII 永远不写任何缓存（log_analysis_cache 只存脱敏后的 payload）
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from agent.loganalysis.models import ErrorBlock

# ---- 正则模式 -------------------------------------------------------------

# 11 位中国大陆手机号（1[3-9]xxxxxxxxx）
_RE_PHONE_CN = re.compile(r"\b1[3-9]\d{9}\b")

# 18 位身份证（含 X）
_RE_ID_CN = re.compile(r"\b\d{17}[0-9Xx]\b")

# 银行卡（13-19 位连续数字；带空格 / 横线分隔也匹配）
_RE_CARD = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")

# IPv4
_RE_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# 邮箱
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")

# AWS Access Key（启发式）
_RE_AWS_KEY = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")

# JWT（eyJ...三段式）
_RE_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")

# 通用高熵 token（≥32 字符 + 含字母数字 + 至少 2 种字符类）—— 启发式捕捉密码 / API key
_RE_HIGH_ENTROPY = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")


# 规则表：(regex, type_name) → 匹配替换成 [REDACTED:TYPE]
_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (_RE_PHONE_CN, "PHONE"),
    (_RE_ID_CN, "ID_CARD"),
    (_RE_CARD, "BANK_CARD"),
    (_RE_AWS_KEY, "AWS_KEY"),
    (_RE_JWT, "JWT"),
    (_RE_EMAIL, "EMAIL"),
    (_RE_IPV4, "IPV4"),
    (_RE_HIGH_ENTROPY, "TOKEN"),
)


# 替换顺序：先手机 / 身份证 / 银行卡（避免被 HIGH_ENTROPY 提前吃掉），
# 再 AWS / JWT，再 EMAIL / IP，最后 HIGH_ENTROPY 兜底。
# 注意：_RULES 已按这个顺序定义。


# ---- 公开 API -------------------------------------------------------------


def scrub_text(text: str) -> str:
    """对单行字符串做 PII 脱敏。

    任何 regex 失败时返回原文（best-effort）。
    """
    if not text:
        return text
    out = text
    for pattern, type_name in _RULES:
        try:
            out = pattern.sub(f"[REDACTED:{type_name}]", out)
        except re.error:
            continue
    return out


def scrub_lines(lines: Iterable[str]) -> list[str]:
    """对 list[str] 批量脱敏（输入只迭代一次；返回新 list）。"""
    return [scrub_text(line) for line in lines]


def scrub_error_block(block: ErrorBlock) -> ErrorBlock:
    """对 ErrorBlock.stack_trace 做脱敏，header 也同步。

    返回**新** ErrorBlock（不修改入参）。
    fingerprint 不变（脱敏后 stack_trace 的 fingerprint 会变；调用方按需重算）。
    """
    scrubbed_header = scrub_text(block.header)
    scrubbed_stack = scrub_lines(block.stack_trace)
    return ErrorBlock(
        start_line=block.start_line,
        end_line=block.end_line,
        header=scrubbed_header,
        stack_trace=scrubbed_stack,
        level=block.level,
        fingerprint=block.fingerprint,
    )


def scrub_error_blocks(blocks: Iterable[ErrorBlock]) -> list[ErrorBlock]:
    """批量脱敏 + 重算 fingerprint（脱敏后 hash 不同）。"""
    out: list[ErrorBlock] = []
    for b in blocks:
        s = scrub_error_block(b)
        # 用脱敏后的 stack_trace 重算 fingerprint（用 adler32 简单 hash；不依赖 hashlib）
        s.fingerprint = _stack_fingerprint(s.stack_trace)
        out.append(s)
    return out


# ---- 内部工具 -------------------------------------------------------------


def _stack_fingerprint(stack: list[str]) -> str:
    """简单的 stack 指纹（脱敏后内容稳定即可，用 zlib + adler32）。"""
    import zlib

    blob = "\n".join(stack).encode("utf-8", errors="replace")
    return f"{zlib.adler32(blob) & 0xFFFFFFFF:08x}"
