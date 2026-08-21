"""Phase 7 V0 · NL2SQL 生成器 —— 组装 prompt → LMRouter → 返回 SQL 文本。

流水线（design §4.1）：
  1. Schema 链接（linker.select_tables）→ 裁剪到 3-5 表
  2. 业务字典注入（dictionary.translate）→ 消除幻觉
  3. Few-shot 学习（从 analysis_tasks 检索相似 SQL）
  4. 组装 prompt → LMRouter task='nl2sql'（可云端）
  5. 生成后校验（validator）→ 问题回喂重试 1 次（DIN-SQL self-correction 范式）

注意：nl2sql 本身只看裁剪后的 3-5 表结构 + 业务字典（不含原始数据行），
可走 LMRouter 正常路由（云端大模型）。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.dataexpert.models import TableSchema
from agent.dataexpert.nl2sql.validator import format_issues, validate_generated_sql
from agent.llm.json_discipline import extract_sql
from agent.llm.prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)


# Few-shot 案例
class SqlCase:
    """历史 SQL 案例（用于 few-shot）。"""

    def __init__(self, question: str, sql: str) -> None:
        self.question = question
        self.sql = sql


def build_prompt(
    question: str,
    tables: list[TableSchema],
    dictionary_context: str = "",
    few_shot: list[SqlCase] | None = None,
    repair_section: str = "",
) -> str:
    """组装 NL2SQL prompt（裁剪 schema + 字典 + few-shot）。

    Args:
        question: 用户自然语言问题。
        tables: Schema 链接选出的 3-5 张表。
        dictionary_context: 业务字典上下文。
        few_shot: 历史 SQL 案例。
        repair_section: 自纠错重试段落（首轮为空；重试时含上轮 SQL + 问题清单）。

    Returns:
        完整的 prompt 文本。
    """
    return render_prompt(
        load_prompt("nl2sql/generate"),
        QUESTION=question,
        TABLES=_format_tables(tables),
        DICTIONARY=dictionary_context,
        FEW_SHOT=_format_few_shot(few_shot),
        REPAIR_SECTION=repair_section,
    )


def _format_tables(tables: list[TableSchema]) -> str:
    """表结构文本（裁剪后的 3-5 张表）。"""
    if not tables:
        return ""
    parts: list[str] = ["【可用表结构】："]
    for tbl in tables:
        parts.append(f"\n-- {tbl.name}（{tbl.comment}）")
        parts.append(f"CREATE TABLE {tbl.name} (")
        col_lines = []
        for col in tbl.columns:
            comment = f"  -- {col.comment}" if col.comment else ""
            col_lines.append(f"  {col.name} {col.dtype}{comment}")
        parts.append(",\n".join(col_lines))
        parts.append(");")
    return "\n".join(parts)


def _format_few_shot(few_shot: list[SqlCase] | None) -> str:
    """Few-shot 案例文本（最多 3 个）。"""
    if not few_shot:
        return ""
    parts: list[str] = ["【参考案例】："]
    for case in few_shot[:3]:
        parts.append(f"-- 问题：{case.question}")
        parts.append(f"{case.sql}\n")
    return "\n".join(parts)


async def to_sql(
    question: str,
    tables: list[TableSchema],
    dictionary_context: str = "",
    few_shot: list[SqlCase] | None = None,
    llm_router: Any = None,
) -> str:
    """组装裁剪 prompt → LMRouter task='nl2sql' → 返回 SQL 文本（不执行）。

    Args:
        question: 用户自然语言问题。
        tables: Schema 链接选出的表。
        dictionary_context: 业务字典上下文。
        few_shot: 历史 SQL 案例。
        llm_router: LMRouter 实例（可选，V0 无 router 时返回占位 SQL）。

    Returns:
        生成的 SQL 文本。
    """
    prompt = build_prompt(question, tables, dictionary_context, few_shot)

    if llm_router is None:
        # V0 无 LLM：返回占位（前端 mock 数据独立运行）
        return f"-- V0 占位（需要 LMRouter）\n-- 问题：{question}\nSELECT 1;"

    # V1：走 LMRouter.generate_raw（ollama → 内网 → 云端降级链，原始透传）。
    # BUGFIX #128：旧实现调不存在的 route(kind=..., max_tokens=...) 必挂，
    # 且端点从未传入 router → 永远落 V0 占位，前端误报「已生成 SQL」。
    try:
        result = await llm_router.generate_raw(prompt=prompt, task="nl2sql")
        sql = extract_sql(result)
    except Exception:
        return f"-- LLM 调用失败\n-- 问题：{question}\nSELECT 1;"

    # V2 自纠错：校验不过 → 问题清单回喂重试 1 次（DIN-SQL self-correction）。
    # 重试后仍不过 → 保留首版 SQL（下游还有白名单闸 + HITL 人工复核兜底）。
    if sql:
        issues = validate_generated_sql(sql, tables)
        if issues:
            logger.info("nl2sql validate failed issues=%d, retry once", len(issues))
            repair = _repair_section(sql, issues)
            retry_prompt = build_prompt(
                question, tables, dictionary_context, few_shot, repair_section=repair
            )
            try:
                retry_result = await llm_router.generate_raw(prompt=retry_prompt, task="nl2sql")
                retry_sql = extract_sql(retry_result)
                if retry_sql and not validate_generated_sql(retry_sql, tables):
                    return retry_sql
            except Exception:
                logger.warning("nl2sql repair retry failed", exc_info=True)
        return sql
    return f"-- LLM 调用失败\n-- 问题：{question}\nSELECT 1;"


def _repair_section(bad_sql: str, issues: list[str]) -> str:
    """自纠错重试段落：上轮 SQL + 问题清单，要求修正后重新输出。"""
    return (
        "\n## 修复任务\n"
        "你上一轮生成的 SQL 未通过校验，请严格对照上面的表结构修正：\n"
        f"【上轮 SQL】：\n{bad_sql}\n"
        f"【问题清单】：\n{format_issues(issues)}\n"
        "只输出修正后的完整 SQL，不要解释。"
    )
