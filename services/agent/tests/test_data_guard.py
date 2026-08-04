"""Phase 7 V0 · 只读闸测试 —— 写操作全封 + LIMIT 注入 + heavy 检测。

验收硬门槛（design §11）：
  - 任何写操作被硬拦截并记 DATA_WRITE_BLOCKED
"""
import pytest

from agent.dataexpert.readonly.guard import (
    WriteBlockedError,
    enforce_readonly,
    inject_limit,
    is_heavy,
)


# ---- 写操作全封（逐一断言拦截）------------------------------------------------

_WRITE_SQLS = [
    "UPDATE t_account SET balance = 0 WHERE account_id = 1",
    "DELETE FROM t_order WHERE order_id = 100",
    "DROP TABLE t_account",
    "TRUNCATE TABLE t_order",
    "INSERT INTO t_account (account_id) VALUES (1)",
    "ALTER TABLE t_account ADD COLUMN foo TEXT",
    "GRANT SELECT ON t_account TO user1",
    "REVOKE SELECT ON t_account FROM user1",
    "CREATE TABLE t_evil (id INT)",
    "REPLACE INTO t_account VALUES (1, 2)",
    "MERGE INTO t_account USING t_order ON 1=1 WHEN MATCHED THEN UPDATE SET x=1",
]


@pytest.mark.parametrize("sql", _WRITE_SQLS)
def test_write_blocked(sql: str):
    """所有写操作关键字必须被拦截。"""
    with pytest.raises(WriteBlockedError):
        enforce_readonly(sql)


def test_select_allowed():
    """正常 SELECT 不拦截。"""
    enforce_readonly("SELECT * FROM t_account WHERE status = '1'")
    enforce_readonly("SELECT COUNT(*) FROM t_order")
    enforce_readonly("SELECT a.id, b.name FROM t_a a JOIN t_b b ON a.id = b.id")


def test_write_in_comment_not_blocked():
    """注释中的写操作关键字不拦截（去注释后检测）。"""
    sql = "-- 这里有个 UPDATE 注释\nSELECT * FROM t_account"
    enforce_readonly(sql)  # 不应抛异常


def test_write_in_string_literal():
    """字符串字面量中的关键字（V0 简化：仍会拦截，V1 接力精确解析）。"""
    # V0 已知限制：字符串内的关键字也会触发（安全优先，宁可误拦不可漏放）
    sql = "SELECT * FROM t_log WHERE msg = 'UPDATE failed'"
    with pytest.raises(WriteBlockedError):
        enforce_readonly(sql)


# ---- LIMIT 注入 ----------------------------------------------------------------

def test_inject_limit_no_existing():
    """无 LIMIT → 追加。"""
    result = inject_limit("SELECT * FROM t_account", cap=100)
    assert "LIMIT 100" in result


def test_inject_limit_existing_smaller():
    """已有 LIMIT 50 < cap 100 → 不变。"""
    result = inject_limit("SELECT * FROM t_account LIMIT 50", cap=100)
    assert "LIMIT 50" in result


def test_inject_limit_existing_larger():
    """已有 LIMIT 99999 > cap 10000 → 收窄。"""
    result = inject_limit("SELECT * FROM t_account LIMIT 99999", cap=10000)
    assert "LIMIT 10000" in result
    assert "99999" not in result


def test_inject_limit_strips_semicolon():
    """尾部分号被去除。"""
    result = inject_limit("SELECT 1;", cap=10)
    assert not result.rstrip().endswith(";")


# ---- heavy 检测 ----------------------------------------------------------------

def test_heavy_join():
    """多表 JOIN → heavy。"""
    sql = "SELECT a.id FROM t_a a JOIN t_b b ON a.id = b.id"
    assert is_heavy(sql) is True


def test_heavy_no_where():
    """无 WHERE 全表扫描 → heavy。"""
    sql = "SELECT * FROM t_account"
    assert is_heavy(sql) is True


def test_not_heavy_with_where():
    """有 WHERE → 不 heavy。"""
    sql = "SELECT * FROM t_account WHERE status = '1'"
    assert is_heavy(sql) is False


def test_not_heavy_aggregate():
    """聚合函数（COUNT/SUM）无 WHERE → 不 heavy。"""
    sql = "SELECT COUNT(*) FROM t_account"
    assert is_heavy(sql) is False
