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
    "update",
    "delete",
    "drop",
    "truncate",
    "insert",
    "alter",
    "grant",
    "revoke",
    "create",
    "replace",
    "merge",
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

# 数据外泄型子句（SELECT 体内导出到服务器文件）
_INTO_FILE_RE = re.compile(r"\bINTO\s+(OUTFILE|DUMPFILE)\b", re.IGNORECASE)

# CTE 收尾写操作（部分方言支持 WITH … INSERT/UPDATE/DELETE）
_CTE_WRITE_RE = re.compile(r"\)\s*(INSERT|UPDATE|DELETE|REPLACE|MERGE)\b", re.IGNORECASE)

# 首关键字提取
_FIRST_KW_RE = re.compile(r"\s*(\w+)")


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


def _split_statements(sql: str) -> list[str]:
    """按 ; 拆语句，忽略单/双引号字符串字面量内的分号。"""
    stmts: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    for ch in sql:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ";":
            stmts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    stmts.append("".join(buf))
    return [s.strip() for s in stmts if s.strip()]


def enforce_select_only(sql: str) -> None:
    """SELECT 白名单：非 dev 环境仅允许单条 SELECT / WITH…SELECT。

    缺口 10（用户红线）：除开发环境外只允许执行 SELECT 语句。
      - 多语句（; 拼接）一律拒；字符串字面量内的 ; 不误判
      - 首关键字必须 SELECT 或 WITH；WITH 收尾不得是写操作
      - SELECT 体内拦 INTO OUTFILE / INTO DUMPFILE（数据外泄）
      - env=="dev" 且 data_allow_non_select_in_dev=true 时跳过白名单，
        降级走黑名单 enforce_readonly（DROP 等仍拦，fail-safe）

    Args:
        sql: 待检测的 SQL 文本。

    Raises:
        WriteBlockedError: 非 SELECT 语句（调用方记 DATA_WRITE_BLOCKED 审计）。
    """
    if settings.env == "dev" and settings.data_allow_non_select_in_dev:
        # 豁免：降级黑名单第二层
        enforce_readonly(sql)
        return
    cleaned = _strip_comments(sql)
    stmts = _split_statements(cleaned)
    if len(stmts) != 1:
        raise WriteBlockedError(sql, "multiple-statements")
    stmt = stmts[0]
    first = _FIRST_KW_RE.match(stmt)
    kw = first.group(1).upper() if first else ""
    if kw == "WITH":
        if _CTE_WRITE_RE.search(stmt):
            raise WriteBlockedError(sql, "with-write")
        if not re.search(r"\bSELECT\b", stmt, re.IGNORECASE):
            raise WriteBlockedError(sql, "with-no-select")
    elif kw != "SELECT":
        raise WriteBlockedError(sql, kw.lower() or "empty")
    if _INTO_FILE_RE.search(stmt):
        raise WriteBlockedError(sql, "into-outfile")


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
