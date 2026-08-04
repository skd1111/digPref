"""Phase 7 V0 · 只读闸 —— 写操作硬拦截 + LIMIT 强制注入 + 全表扫描检测。

安全红线（design §6）：
  - 数据专家模式禁 UPDATE/DELETE/DROP/TRUNCATE/INSERT/ALTER/GRANT/REVOKE/CREATE/REPLACE/MERGE
  - 与 mcp-server-database 安全层各自独立封禁（纵深防御，CLAUDE.md §1）
  - 所有 SQL 执行前强制注入 LIMIT（默认 10000）
  - 多表 JOIN / 无 WHERE 全表扫描 → is_heavy=True（触发 HITL）
"""
from __future__ import annotations

import re

from agent.config import settings


# 写操作关键字（全小写匹配）
_WRITE_TOKENS = (
    "update", "delete", "drop", "truncate", "insert", "alter",
    "grant", "revoke", "create", "replace", "merge",
)

# 预编译正则：匹配独立的写操作关键字（词边界）
_WRITE_RE = re.compile(
    r"\b(" + "|".join(_WRITE_TOKENS) + r")\b",
    re.IGNORECASE,
)

# LIMIT 子句正则
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)

# JOIN 检测
_JOIN_RE = re.compile(r"\bJOIN\b", re.IGNORECASE)

# WHERE 检测
_WHERE_RE = re.compile(r"\bWHERE\b", re.IGNORECASE)


class WriteBlockedError(Exception):
    """写操作被只读闸拦截时抛出。

    调用方应记 DATA_WRITE_BLOCKED 审计事件（安全告警）。
    """

    def __init__(self, sql: str, token: str) -> None:
        self.sql = sql
        self.token = token
        super().__init__(
            f"只读铁律：检测到写操作 '{token.upper()}'，已拦截。"
            f"数据专家模式禁止任何写操作（纵深防御，与 mcp-server-database 独立封禁）。"
        )


def enforce_readonly(sql: str) -> None:
    """检测写操作 → 抛 WriteBlockedError（记 DATA_WRITE_BLOCKED 审计）。

    与 mcp-server-database 安全层各自独立封禁（纵深防御，CLAUDE.md §1）。

    Args:
        sql: 待检测的 SQL 文本。

    Raises:
        WriteBlockedError: 检测到写操作关键字。
    """
    # 去除 SQL 注释（-- 单行 + /* */ 多行）
    cleaned = _strip_comments(sql)
    match = _WRITE_RE.search(cleaned)
    if match:
        raise WriteBlockedError(sql, match.group(1).lower())


def inject_limit(sql: str, cap: int | None = None) -> str:
    """无 LIMIT 的 SELECT 强制加 LIMIT cap；已有更大 LIMIT 收窄到 cap。

    Args:
        sql: 只读 SELECT 语句。
        cap: 上限行数（默认取 settings.data_sql_row_limit）。

    Returns:
        注入/收窄 LIMIT 后的 SQL。
    """
    if cap is None:
        cap = settings.data_sql_row_limit

    cleaned = sql.rstrip().rstrip(";")
    match = _LIMIT_RE.search(cleaned)

    if match:
        existing = int(match.group(1))
        if existing > cap:
            # 收窄到 cap
            cleaned = _LIMIT_RE.sub(f"LIMIT {cap}", cleaned)
        return cleaned
    else:
        # 无 LIMIT → 追
        return f"{cleaned}\nLIMIT {cap}"


def is_heavy(sql: str) -> bool:
    """多表 JOIN / 无 WHERE 全表扫描 → True（触发 HITL 用户确认）。

    Args:
        sql: 只读 SELECT 语句。

    Returns:
        True 表示重查询，需要 HITL 确认。
    """
    cleaned = _strip_comments(sql)
    # 多表 JOIN
    joins = _JOIN_RE.findall(cleaned)
    if len(joins) >= 1:
        return True
    # 无 WHERE 的 SELECT（全表扫描）
    if not _WHERE_RE.search(cleaned):
        # 排除 COUNT(*) / 聚合函数等轻量查询
        if not re.search(r"\b(COUNT|SUM|AVG|MAX|MIN)\s*\(", cleaned, re.IGNORECASE):
            return True
    return False


def _strip_comments(sql: str) -> str:
    """去除 SQL 注释（-- 单行 + /* */ 多行）。"""
    # 去多行注释
    result = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    # 去单行注释
    result = re.sub(r"--[^\n]*", " ", result)
    return result
