"""BUGFIX #101 回归：MySQL/Oracle 执行分支必须经 cursor 取数构造 DataFrame。

此前 `pd.read_sql(sql, conn)` 把 aiomysql/oracledb 异步连接直接喂给 pandas，
pandas 对连接调 `.execute()` → 异步 cursor 是 _ContextManager 无此属性 →
500 AttributeError。修复后：cursor.execute + fetchall → 手动构造 DataFrame。
"""

from __future__ import annotations

from agent.dataexpert.readonly.pool import ReadOnlyPool


class _FakeCursor:
    def __init__(self, columns: list[str], rows: list[tuple]):
        self._columns = columns
        self._rows = rows
        self.executed: list[str] = []
        self.closed = False

    async def execute(self, sql: str) -> None:
        self.executed.append(sql)

    async def fetchall(self) -> list[tuple]:
        return self._rows

    @property
    def description(self):
        return [(c,) for c in self._columns] if self._columns else None

    async def close(self) -> None:
        self.closed = True

    # aiomysql cursor 是异步上下文管理器
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    def __init__(self, cursor: _FakeCursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _FakeAcquire:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *exc):
        return False


class _FakePool:
    def __init__(self, conn: _FakeConn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquire(self._conn)


def _pool_with_fake(pool_type: str, columns: list[str], rows: list[tuple]):
    pool = ReadOnlyPool({"type": pool_type, "host": "h", "database": "d"})
    cur = _FakeCursor(columns, rows)
    pool._pool = _FakePool(_FakeConn(cur))  # 预置 → _ensure_pool 跳过真初始化
    return pool, cur


async def test_mysql_executes_via_cursor_not_read_sql():
    pool, cur = _pool_with_fake("mysql", ["id", "name"], [(1, "甲"), (2, "乙")])
    df = await pool._execute_mysql("SELECT * FROM t")
    # 只读语句与业务 SQL 都经 cursor.execute（而非 pd.read_sql(conn)）
    assert cur.executed[0] == "SET SESSION TRANSACTION READ ONLY"
    assert cur.executed[1] == "SELECT * FROM t"
    assert list(df.columns) == ["id", "name"]
    assert df.iloc[1]["name"] == "乙"


async def test_mysql_empty_result_keeps_columns():
    pool, _cur = _pool_with_fake("mysql", ["id", "name"], [])
    df = await pool._execute_mysql("SELECT * FROM t")
    assert df.empty
    assert list(df.columns) == ["id", "name"]


async def test_oracle_executes_via_cursor_and_closes():
    pool, cur = _pool_with_fake("oracle", ["id"], [(1,), (2,)])
    df = await pool._execute_oracle("SELECT id FROM t")
    assert cur.executed[0] == "ALTER SESSION SET READ ONLY"
    assert cur.executed[1] == "SELECT id FROM t"
    assert cur.closed is True
    assert list(df.columns) == ["id"]
    assert len(df) == 2


async def test_mysql_compat_types_share_fixed_path():
    """TiDB/OceanBase/GBase 与 MySQL 同走 _execute_mysql（同协议同坑）。"""
    for t in ("tidb", "oceanbase", "gbase"):
        pool, _cur = _pool_with_fake(t, ["a"], [(1,)])
        df = await pool._execute_mysql("SELECT a FROM t")
        assert df.iloc[0]["a"] == 1
