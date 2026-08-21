"""Phase 7 V2 · NL2SQL 生成后校验 —— sqlglot 语法检查 + 表/字段存在性核对。

参考 DIN-SQL / MAC-SQL 的 self-correction 范式：生成 SQL 先静态校验，
问题清单回喂 LLM 重试 1 次（generator.to_sql 编排），减少幻觉表/字段直接下发。

保守策略（宁漏报不误杀）：
  - 语法解析失败 → 直接报语法问题，不再做存在性核对（AST 不可靠）
  - CTE（WITH 子句）视为已知临时表，其字段不核对
  - 带限定名的字段只在校验器能解析到具体表时才核对
  - SELECT 别名（GROUP BY / ORDER BY 引用别名）豁免
  - SELECT * 不核对字段
"""

from __future__ import annotations

import logging
from typing import cast

import sqlglot
from sqlglot import exp

from agent.dataexpert.models import TableSchema

logger = logging.getLogger(__name__)

# 单轮校验问题清单的最大条数（回喂 prompt 防爆）
_MAX_ISSUES = 5


def validate_generated_sql(sql: str, tables: list[TableSchema]) -> list[str]:
    """校验生成的 SQL，返回问题清单（空列表 = 通过）。

    Args:
        sql: 生成的 SQL 文本（已剥围栏）。
        tables: Schema 链接选出的表（空列表 → 只做语法检查）。

    Returns:
        中文问题描述列表；空列表表示校验通过。
    """
    if not sql.strip():
        return ["SQL 为空"]
    try:
        parsed = sqlglot.parse(sql)
    except Exception as e:
        return [f"SQL 语法错误：{e}"]
    if not parsed or parsed[0] is None:
        return ["SQL 语法错误：无法解析"]

    issues: list[str] = []
    for stmt in parsed:
        if stmt is None:
            continue
        issues.extend(_check_statement(cast(exp.Expression, stmt), tables))
    return issues[:_MAX_ISSUES]


def _check_statement(stmt: exp.Expression, tables: list[TableSchema]) -> list[str]:
    """单条语句的存在性核对（表 → 限定字段 → 未限定字段）。"""
    schema_map = {t.name.lower(): t for t in tables}

    # CTE 名视为已知临时表（字段不核对）
    cte_names = {(cte.alias_or_name or "").lower() for cte in stmt.find_all(exp.CTE)} - {""}

    # 别名 → 真实表名（小写）；别名与表名本身都可作限定符
    alias_map: dict[str, str] = {}
    table_refs: list[exp.Table] = list(stmt.find_all(exp.Table))
    for t in table_refs:
        name = (t.name or "").lower()
        if not name:
            continue
        alias_map[name] = name
        if t.alias:
            alias_map[t.alias.lower()] = name

    issues: list[str] = []

    # 1) 表存在性（CTE 引用豁免）
    if schema_map:
        unknown_tables = sorted(
            {
                (t.name or "")
                for t in table_refs
                if (t.name or "").lower() not in schema_map
                and (t.name or "").lower() not in cte_names
                and t.name
            }
        )
        if unknown_tables:
            issues.append(
                f"引用了不存在的表：{', '.join(unknown_tables)}；"
                f"可用的表：{', '.join(t.name for t in tables)}"
            )

    # 2) SELECT 别名豁免（GROUP BY / ORDER BY / HAVING 可引用别名）
    select_aliases = {
        (alias.alias or "").lower() for alias in stmt.find_all(exp.Alias) if alias.alias
    }

    # 3) 字段存在性
    columns = [c for c in stmt.find_all(exp.Column) if c.name != "*"]
    if schema_map and columns:
        union_cols = {col.name.lower() for t in tables for col in t.columns if col.name}
        flagged: list[str] = []
        for col in columns:
            col_name = col.name or ""
            qualifier = (col.table or "").lower()
            if qualifier:
                real_table = alias_map.get(qualifier)
                if real_table is None or real_table in cte_names:
                    continue  # 限定符解析不到已知表（可能是子查询别名）→ 不核对
                if real_table not in schema_map:
                    continue  # 未知表已在上一步报过
                known = {c.name.lower() for c in schema_map[real_table].columns}
                if col_name.lower() not in known:
                    flagged.append(f"{qualifier}.{col_name}")
            else:
                if col_name.lower() in select_aliases:
                    continue
                if union_cols and col_name.lower() not in union_cols:
                    flagged.append(col_name)
        if flagged:
            issues.append(f"引用了不存在的字段：{', '.join(sorted(set(flagged)))}")

    return issues


def format_issues(issues: list[str]) -> str:
    """把问题清单格式化为回喂 prompt 的文本段落。"""
    return "\n".join(f"- {issue}" for issue in issues)


__all__ = ["format_issues", "validate_generated_sql"]
