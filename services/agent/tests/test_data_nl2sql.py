"""Phase 7 V0 · NL2SQL 测试 —— Schema 链接选表 + 业务字典替换。

验收硬门槛（design §11）：
  - Schema 链接选表数 ≤ 5（绝不把全量 Schema 塞给大模型）
  - 业务字典正确替换（'成功' → status='SUC'）
"""

import pytest
from agent.dataexpert.nl2sql.dictionary import get_dictionary_context, translate
from agent.dataexpert.nl2sql.linker import MAX_TABLES, select_tables

# ---- Schema 链接：选表数 ≤ 5 --------------------------------------------------

_SCHEMA_CACHE_10 = [
    {
        "name": f"t_table_{i}",
        "comment": f"测试表{i}",
        "columns": [
            {"name": "id", "type": "BIGINT", "comment": "主键"},
            {"name": f"col_{i}", "type": "VARCHAR", "comment": f"字段{i}"},
        ],
    }
    for i in range(10)
]


@pytest.mark.asyncio
async def test_select_tables_max_5():
    """10 张表中选表数不超过 MAX_TABLES=5。"""
    result = await select_tables("查询所有数据", _SCHEMA_CACHE_10)
    assert len(result) <= MAX_TABLES
    assert len(result) <= 5


@pytest.mark.asyncio
async def test_select_tables_respects_custom_max():
    """自定义 max_tables=3 时不超过 3。"""
    result = await select_tables("查询所有数据", _SCHEMA_CACHE_10, max_tables=3)
    assert len(result) <= 3


@pytest.mark.asyncio
async def test_select_tables_empty_cache():
    """空 schema_cache 返回空列表。"""
    result = await select_tables("查询余额", [])
    assert result == []


@pytest.mark.asyncio
async def test_select_tables_relevance():
    """表名匹配度高的排在前面。"""
    cache = [
        {
            "name": "t_account",
            "comment": "账户表",
            "columns": [
                {"name": "balance", "type": "DECIMAL", "comment": "余额"},
            ],
        },
        {
            "name": "t_order",
            "comment": "订单表",
            "columns": [
                {"name": "amount", "type": "DECIMAL", "comment": "金额"},
            ],
        },
        {
            "name": "t_log",
            "comment": "日志表",
            "columns": [
                {"name": "msg", "type": "TEXT", "comment": "消息"},
            ],
        },
    ]
    result = await select_tables("查询账户余额", cache)
    # t_account 应该排第一（表名 + 字段名都匹配）
    assert result[0].name == "t_account"


@pytest.mark.asyncio
async def test_select_tables_returns_table_schema():
    """返回的对象是 TableSchema 类型。"""
    from agent.dataexpert.models import TableSchema

    result = await select_tables("查询数据", _SCHEMA_CACHE_10)
    for tbl in result:
        assert isinstance(tbl, TableSchema)
        assert tbl.name != ""


# ---- 业务字典替换 ---------------------------------------------------------------


def test_translate_global_term():
    """全局字典：'成功' → status='SUC'。"""
    ctx = translate("查询成功的交易", source_id="")
    assert "成功" in ctx
    assert "status='SUC'" in ctx


def test_translate_source_specific():
    """数据源特定字典：信贷系统 '损失类' → five_class='5'。"""
    ctx = translate("统计损失类贷款", source_id="ds_credit")
    assert "损失类" in ctx
    assert "five_class='5'" in ctx


def test_translate_pay_channel():
    """支付网关字典：'微信' → channel='WECHAT'。"""
    ctx = translate("微信消费金额", source_id="ds_pay")
    assert "微信" in ctx
    assert "channel='WECHAT'" in ctx
    assert "消费" in ctx
    assert "txn_code='1001'" in ctx


def test_translate_no_match():
    """无匹配术语 → 返回空字符串。"""
    ctx = translate("hello world", source_id="")
    assert ctx == ""


def test_translate_multiple_terms():
    """多个术语同时匹配。"""
    ctx = translate("成功和失败的订单", source_id="")
    assert "成功" in ctx
    assert "失败" in ctx


# ---- get_dictionary_context ----------------------------------------------------


def test_get_dictionary_context_global():
    """全局字典上下文非空。"""
    ctx = get_dictionary_context("")
    assert "业务字典" in ctx
    assert "成功" in ctx


def test_get_dictionary_context_with_source():
    """带数据源的字典上下文包含特定映射。"""
    ctx = get_dictionary_context("ds_credit")
    assert "正常类" in ctx
    assert "five_class='1'" in ctx


def test_get_dictionary_context_unknown_source():
    """未知数据源只返回全局字典。"""
    ctx = get_dictionary_context("ds_unknown")
    assert "成功" in ctx
    # 不应包含信贷特定映射
    assert "five_class" not in ctx


def test_extract_sql_from_generator():
    """SQL 围栏/前缀清洗（spec §4.5 extract_sql）。"""
    from agent.llm.json_discipline import extract_sql

    raw = "好的：\n```sql\nSELECT * FROM orders WHERE status='SUC'\n```"
    assert extract_sql(raw) == "SELECT * FROM orders WHERE status='SUC'"
