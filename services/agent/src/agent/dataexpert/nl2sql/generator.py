"""Phase 7 V0 · NL2SQL 生成器 —— 组装 prompt → LMRouter → 返回 SQL 文本。

流水线（design §4.1）：
  1. Schema 链接（linker.select_tables）→ 裁剪到 3-5 表
  2. 业务字典注入（dictionary.translate）→ 消除幻觉
  3. Few-shot 学习（从 analysis_tasks 检索相似 SQL）
  4. 组装 prompt → LMRouter task='nl2sql'（可云端）

注意：nl2sql 本身只看裁剪后的 3-5 表结构 + 业务字典（不含原始数据行），
可走 LMRouter 正常路由（云端大模型）。
"""
from __future__ import annotations

from typing import Any

from agent.dataexpert.models import TableSchema


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
) -> str:
    """组装 NL2SQL prompt（裁剪 schema + 字典 + few-shot）。

    Args:
        question: 用户自然语言问题。
        tables: Schema 链接选出的 3-5 张表。
        dictionary_context: 业务字典上下文。
        few_shot: 历史 SQL 案例。

    Returns:
        完整的 prompt 文本。
    """
    parts: list[str] = []

    # 系统指令
    parts.append(
        "你是一个金融数据分析 SQL 专家。根据用户的自然语言问题，"
        "生成只读 SELECT SQL（禁止任何写操作）。\n"
        "规则：\n"
        "  1. 只生成 SELECT 语句，禁止 UPDATE/DELETE/DROP/INSERT/ALTER 等写操作\n"
        "  2. 使用提供的表结构和字段，不要编造不存在的表或字段\n"
        "  3. 业务术语必须使用字典中的编码值（如 '成功' → status='SUC'）\n"
        "  4. 只输出 SQL，不要解释\n"
    )

    # 表结构（裁剪后的 3-5 表）
    if tables:
        parts.append("【可用表结构】：")
        for tbl in tables:
            parts.append(f"\n-- {tbl.name}（{tbl.comment}）")
            parts.append(f"CREATE TABLE {tbl.name} (")
            col_lines = []
            for col in tbl.columns:
                comment = f"  -- {col.comment}" if col.comment else ""
                col_lines.append(f"  {col.name} {col.dtype}{comment}")
            parts.append(",\n".join(col_lines))
            parts.append(");")

    # 业务字典
    if dictionary_context:
        parts.append(f"\n{dictionary_context}")

    # Few-shot 案例
    if few_shot:
        parts.append("\n【参考案例】：")
        for case in few_shot[:3]:  # 最多 3 个 few-shot
            parts.append(f"-- 问题：{case.question}")
            parts.append(f"{case.sql}\n")

    # 用户问题
    parts.append(f"\n【用户问题】：{question}")
    parts.append("\n【SQL】：")

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

    # V1：走 LMRouter task='nl2sql'（可云端）
    try:
        result = await llm_router.route(
            prompt=prompt,
            kind="nl2sql",
            max_tokens=1024,
            temperature=0.0,
        )
        return result.strip()
    except Exception:
        return f"-- LLM 调用失败\n-- 问题：{question}\nSELECT 1;"
