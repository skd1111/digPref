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


# ---- V1 业务字典 YAML 外置 ---------------------------------------------------


def test_translate_yaml_override_builtin(tmp_path):
    """YAML 同键覆盖内置默认（运营改口径不用碰代码）。"""
    from agent.dataexpert.nl2sql.dictionary import reset_dictionary_cache

    d = tmp_path / "biz_dict"
    d.mkdir()
    (d / "_global.yaml").write_text("成功: \"status='2'\"\n", encoding="utf-8")
    reset_dictionary_cache()
    ctx = translate("查询成功的交易", source_id="", dict_dir=str(d))
    assert "status='2'" in ctx
    assert "status='SUC'" not in ctx


def test_translate_yaml_extend_new_source(tmp_path):
    """YAML 新增数据源字典（内置没有的源）。"""
    from agent.dataexpert.nl2sql.dictionary import reset_dictionary_cache

    d = tmp_path / "biz_dict"
    d.mkdir()
    (d / "ds_custom.yaml").write_text('大额: "amount > 1000000"\n', encoding="utf-8")
    reset_dictionary_cache()
    ctx = translate("查询大额交易", source_id="ds_custom", dict_dir=str(d))
    assert "amount > 1000000" in ctx
    # 合并而非替换：全局内置条目仍在全量上下文里
    full = get_dictionary_context("ds_custom", dict_dir=str(d))
    assert "成功" in full and "amount > 1000000" in full


def test_translate_yaml_dir_missing_falls_back(monkeypatch, tmp_path):
    """字典目录不存在 → 退化内置默认，功能不中断。"""
    from agent.config import settings

    monkeypatch.setattr(settings, "data_biz_dict_dir", str(tmp_path / "不存在"))
    ctx = translate("查询成功的交易")
    assert "status='SUC'" in ctx


def test_translate_yaml_invalid_file_skipped(tmp_path):
    """非法 YAML 跳过（记 warning），内置默认仍生效。"""
    from agent.dataexpert.nl2sql.dictionary import reset_dictionary_cache

    d = tmp_path / "biz_dict"
    d.mkdir()
    (d / "_global.yaml").write_text("a: [1, 2\n", encoding="utf-8")  # 未闭合 → 解析失败
    reset_dictionary_cache()
    ctx = translate("查询成功的交易", source_id="", dict_dir=str(d))
    assert "status='SUC'" in ctx  # 退化内置


def test_load_dictionary_mtime_cache_invalidation(tmp_path):
    """文件修改（mtime 变化）→ 缓存自动失效，无需重启。"""
    import os

    from agent.dataexpert.nl2sql.dictionary import load_dictionary, reset_dictionary_cache

    d = tmp_path / "biz_dict"
    d.mkdir()
    fp = d / "_global.yaml"
    fp.write_text('旧术语: "a=1"\n', encoding="utf-8")
    reset_dictionary_cache()
    assert "旧术语" in load_dictionary(str(d))["_global"]

    fp.write_text('新术语: "b=2"\n', encoding="utf-8")
    os.utime(fp, (fp.stat().st_atime + 10, fp.stat().st_mtime + 10))  # 跨过 int 秒粒度
    merged = load_dictionary(str(d))
    assert "新术语" in merged["_global"]
    assert "旧术语" not in merged["_global"]


def test_load_dictionary_meipass_fallback(tmp_path, monkeypatch):
    """打包 exe 场景：cwd 无 config/biz_dict → 回退 _MEIPASS 内置副本。"""
    import sys

    from agent.dataexpert.nl2sql.dictionary import load_dictionary, reset_dictionary_cache

    bundled = tmp_path / "meipass" / "config" / "biz_dict"
    bundled.mkdir(parents=True)
    (bundled / "_global.yaml").write_text('打包术语: "p=1"\n', encoding="utf-8")
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)
    monkeypatch.chdir(tmp_path)  # cwd 下无 config/biz_dict → 必走 _MEIPASS 回退
    reset_dictionary_cache()
    merged = load_dictionary()
    assert "打包术语" in merged["_global"]
    # 内置默认仍合并存在
    assert "成功" in merged["_global"]


def test_extract_sql_from_generator():
    """SQL 围栏/前缀清洗（spec §4.5 extract_sql）。"""
    from agent.llm.json_discipline import extract_sql

    raw = "好的：\n```sql\nSELECT * FROM orders WHERE status='SUC'\n```"
    assert extract_sql(raw) == "SELECT * FROM orders WHERE status='SUC'"


# ---- V2 生成后校验（validator）-------------------------------------------------

from agent.dataexpert.models import ColumnSchema, TableSchema
from agent.dataexpert.nl2sql.validator import validate_generated_sql


def _tbl(name: str, cols: list[str]) -> TableSchema:
    return TableSchema(
        name=name,
        comment="",
        columns=[ColumnSchema(name=c, dtype="VARCHAR") for c in cols],
    )


_TABLES = [
    _tbl("t_order", ["id", "status", "amount"]),
    _tbl("t_account", ["id", "balance"]),
]


def test_validate_valid_sql_passes():
    """合法 SQL（表/字段都存在）→ 无问题。"""
    assert (
        validate_generated_sql("SELECT id, amount FROM t_order WHERE status='SUC'", _TABLES) == []
    )


def test_validate_syntax_error():
    """语法错误 → 报语法问题。"""
    issues = validate_generated_sql("SELECT FROM WHERE", _TABLES)
    assert issues and "语法" in issues[0]


def test_validate_unknown_table():
    """幻觉表 → 报不存在并列出可用表。"""
    issues = validate_generated_sql("SELECT id FROM t_ghost", _TABLES)
    assert any("t_ghost" in i and "不存在" in i for i in issues)


def test_validate_unknown_column():
    """幻觉字段 → 报不存在。"""
    issues = validate_generated_sql("SELECT money FROM t_order", _TABLES)
    assert any("money" in i for i in issues)


def test_validate_qualified_column_via_alias():
    """别名限定字段：o.money（t_order 无 money）→ 报错。"""
    issues = validate_generated_sql("SELECT o.money FROM t_order o", _TABLES)
    assert any("money" in i for i in issues)


def test_validate_select_alias_exempt():
    """SELECT 别名被 GROUP BY/ORDER BY 引用不误报。"""
    sql = "SELECT status, COUNT(*) AS cnt FROM t_order GROUP BY status ORDER BY cnt"
    assert validate_generated_sql(sql, _TABLES) == []


def test_validate_cte_exempt():
    """CTE 名不报未知表，CTE 字段不核对。"""
    sql = "WITH recent AS (SELECT * FROM t_order) SELECT id FROM recent"
    assert validate_generated_sql(sql, _TABLES) == []


def test_validate_no_schema_only_syntax():
    """tables 为空（无 schema 信息）→ 只做语法检查，不误杀表/字段。"""
    assert validate_generated_sql("SELECT anything FROM any_table", []) == []


def test_validate_star_not_checked():
    """SELECT * 不核对字段。"""
    assert validate_generated_sql("SELECT * FROM t_order", _TABLES) == []


# ---- V2 自纠错重试（generator）-------------------------------------------------


class _SeqRouter:
    """按序返回多轮生成结果的假 router。"""

    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)
        self.calls: list[str] = []

    async def generate_raw(self, *, prompt: str, task: str = "nl2sql") -> str:
        self.calls.append(prompt)
        return self._answers.pop(0)


@pytest.mark.asyncio
async def test_to_sql_repairs_invalid_via_retry():
    """首版幻觉字段 → 回喂重试 → 采重试后的合法 SQL。"""
    from agent.dataexpert.nl2sql.generator import to_sql

    router = _SeqRouter(["SELECT money FROM t_order", "SELECT amount FROM t_order"])
    sql = await to_sql("查订单金额", _TABLES, llm_router=router)
    assert sql == "SELECT amount FROM t_order"
    assert len(router.calls) == 2
    assert "修复任务" in router.calls[1]  # 重试 prompt 含问题清单


@pytest.mark.asyncio
async def test_to_sql_keeps_first_when_retry_still_invalid():
    """重试后仍不过 → 保留首版（下游白名单闸 + HITL 兜底）。"""
    from agent.dataexpert.nl2sql.generator import to_sql

    router = _SeqRouter(["SELECT money FROM t_order", "SELECT cash FROM t_order"])
    sql = await to_sql("查订单金额", _TABLES, llm_router=router)
    assert sql == "SELECT money FROM t_order"


@pytest.mark.asyncio
async def test_to_sql_valid_no_retry():
    """首版即合法 → 不产生第二次调用。"""
    from agent.dataexpert.nl2sql.generator import to_sql

    router = _SeqRouter(["SELECT amount FROM t_order"])
    sql = await to_sql("查订单金额", _TABLES, llm_router=router)
    assert sql == "SELECT amount FROM t_order"
    assert len(router.calls) == 1


# ---- Few-shot 关键字降级选取 -------------------------------------------------


@pytest.mark.asyncio
async def test_select_few_shot_keyword_fallback(monkeypatch):
    """无 embedding 配置 → 关键字重叠度选取，按相似度排序。"""
    from agent.config import settings
    from agent.dataexpert.nl2sql.linker import select_few_shot

    monkeypatch.setattr(settings, "local_embedding_base_url", None)

    history = [
        {"name": "查询流程总数", "query_sql": "SELECT COUNT(*) FROM sm_process_tb"},
        {"name": "今日天气如何", "query_sql": "SELECT 1"},
        {"name": "查询流程明细", "query_sql": "SELECT * FROM sm_process_tb"},
    ]
    cases = await select_few_shot("查询流程状态", history)
    assert len(cases) == 3
    # 与「查询流程」重叠最多的排前面
    assert "流程" in cases[0]["question"]
    assert cases[0]["sql"]


@pytest.mark.asyncio
async def test_select_few_shot_skips_tasks_without_sql():
    """无 SQL 的历史任务不入 few-shot。"""
    from agent.dataexpert.nl2sql.linker import select_few_shot

    history = [{"name": "未执行的任务", "query_sql": ""}]
    assert await select_few_shot("任意问题", history) == []


# ---- 向量 Schema 链接（embedding 可注入）--------------------------------------


class _FakeEmbedding:
    """one-hot 假向量：含「账户」的文本与查询同向量。"""

    async def embed(self, text: str) -> list[float]:
        return [1.0, 0.0] if "账户" in text else [0.0, 1.0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


@pytest.mark.asyncio
async def test_select_tables_vector_rank():
    """向量通道：语义相近（零字面重叠也能命中）的表排第一。"""
    cache = [
        {"name": "t_log", "comment": "日志", "columns": [{"name": "msg", "type": "TEXT"}]},
        {"name": "t_acct", "comment": "账户信息", "columns": [{"name": "bal", "type": "DECIMAL"}]},
    ]
    result = await select_tables("查账户余额", cache, embedding=_FakeEmbedding())
    assert result[0].name == "t_acct"


@pytest.mark.asyncio
async def test_select_tables_vector_dead_falls_back_to_keyword():
    """embedding 服务掉线（全零向量）→ 自动退化关键字评分。"""

    class _DeadEmbedding:
        async def embed(self, text: str) -> list[float]:
            return [0.0, 0.0]

        async def embed_batch(self, texts: list[str]) -> list[list[float]]:
            return [[0.0, 0.0] for _ in texts]

    result = await select_tables("查询账户余额", _SCHEMA_CACHE_10, embedding=_DeadEmbedding())
    assert len(result) <= MAX_TABLES
