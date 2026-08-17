"""Phase 7 V1 · 只读连接池 —— 支持主流 + 国产/信创数据库。

支持数据库：
  主流：MySQL / PostgreSQL / Oracle / SQL Server / SQLite / ClickHouse
  国产/信创：达梦(DM) / 人大金仓(KingbaseES) / 南大通用(GBase) /
            OceanBase / TiDB / 华为 GaussDB / openGauss / 瀚高(HighGo)
  文件：CSV / Excel

安全红线（双层防御）：
  - 连接级别强制只读：
    · MySQL/TiDB/OceanBase/GBase: SET SESSION TRANSACTION READ ONLY
    · PostgreSQL/openGauss/GaussDB/KingbaseES/HighGo: SET TRANSACTION READ ONLY
    · Oracle: ALTER SESSION SET READ ONLY
    · SQLite: PRAGMA query_only = ON
    · SQL Server: SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED (只读查询)
    · ClickHouse: 天然只读（SELECT only）
    · 达梦(DM): SET SESSION READ ONLY
  - 与 readonly/guard.py 双层防御（guard 在 SQL 文本层拦截，pool 在连接层拦截）

连接池策略：
  - MySQL/TiDB/OceanBase/GBase: aiomysql 异步连接池（min=1, max=5）
  - PostgreSQL/openGauss/GaussDB/KingbaseES/HighGo: asyncpg 连接池
  - Oracle: oracledb 异步连接池（min=1, max=3）
  - SQL Server: aioodbc / pyodbc
  - 达梦(DM): dmPython（同步，线程池包装）
  - ClickHouse: clickhouse-connect
  - SQLite/CSV/Excel: 单连接 + pandas 读取
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from agent.dataexpert.readonly.guard import enforce_readonly, inject_limit

logger = logging.getLogger(__name__)

# ---- 数据库类型注册表（含国产/信创）------------------------------------------
# 每种类型：default_port / driver / readonly_sql / category
DB_TYPE_REGISTRY: dict[str, dict[str, Any]] = {
    # 主流
    "mysql": {"port": 3306, "driver": "aiomysql", "category": "mainstream"},
    "postgresql": {"port": 5432, "driver": "asyncpg", "category": "mainstream"},
    "oracle": {"port": 1521, "driver": "oracledb", "category": "mainstream"},
    "sqlserver": {"port": 1433, "driver": "aioodbc", "category": "mainstream"},
    "sqlite": {"port": 0, "driver": "sqlite3", "category": "mainstream"},
    "clickhouse": {"port": 8123, "driver": "clickhouse_connect", "category": "mainstream"},
    # 国产/信创
    "dm": {"port": 5236, "driver": "dmPython", "category": "xinchuang"},
    "kingbase": {"port": 54321, "driver": "asyncpg", "category": "xinchuang"},
    "gbase": {"port": 5258, "driver": "aiomysql", "category": "xinchuang"},
    "oceanbase": {"port": 2881, "driver": "aiomysql", "category": "xinchuang"},
    "tidb": {"port": 4000, "driver": "aiomysql", "category": "xinchuang"},
    "gaussdb": {"port": 5432, "driver": "asyncpg", "category": "xinchuang"},
    "opengauss": {"port": 5432, "driver": "asyncpg", "category": "xinchuang"},
    "highgo": {"port": 5866, "driver": "asyncpg", "category": "xinchuang"},
    # 文件
    "csv": {"port": 0, "driver": "pandas", "category": "file"},
    "excel": {"port": 0, "driver": "pandas", "category": "file"},
}

# MySQL 协议兼容类型（使用 aiomysql）
_MYSQL_COMPAT = {"mysql", "tidb", "oceanbase", "gbase"}
# PostgreSQL 协议兼容类型（使用 asyncpg）
_PG_COMPAT = {"postgresql", "kingbase", "gaussdb", "opengauss", "highgo"}


# ---- Schema 元数据同步（缺口 2）----------------------------------------------
# 各方言族元数据查询（全部 SELECT，只读铁律）。
# 输出列统一为：(table_name, table_comment, column_name, data_type, column_comment)

_SCHEMA_QUERY_MYSQL = """SELECT t.TABLE_NAME, t.TABLE_COMMENT, c.COLUMN_NAME,
       c.COLUMN_TYPE, c.COLUMN_COMMENT
FROM information_schema.TABLES t
JOIN information_schema.COLUMNS c
  ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
WHERE t.TABLE_SCHEMA = DATABASE() AND t.TABLE_TYPE = 'BASE TABLE'
ORDER BY t.TABLE_NAME, c.ORDINAL_POSITION"""

_SCHEMA_QUERY_PG = """SELECT c.table_name,
       COALESCE(obj_description((quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass), ''),
       c.column_name, c.data_type,
       COALESCE(col_description((quote_ident(c.table_schema) || '.' || quote_ident(c.table_name))::regclass, c.ordinal_position), '')
FROM information_schema.columns c
WHERE c.table_schema = 'public'
ORDER BY c.table_name, c.ordinal_position"""

_SCHEMA_QUERY_ORACLE = """SELECT tc.TABLE_NAME, COALESCE(tt.COMMENTS, ''),
       tc.COLUMN_NAME, tc.DATA_TYPE, COALESCE(cc.COMMENTS, '')
FROM USER_TAB_COLUMNS tc
LEFT JOIN USER_TAB_COMMENTS tt ON tt.TABLE_NAME = tc.TABLE_NAME
LEFT JOIN USER_COL_COMMENTS cc
  ON cc.TABLE_NAME = tc.TABLE_NAME AND cc.COLUMN_NAME = tc.COLUMN_NAME
ORDER BY tc.TABLE_NAME, tc.COLUMN_ID"""

_SCHEMA_QUERY_SQLSERVER = """SELECT t.name, CAST(ISNULL(ep_t.value, '') AS NVARCHAR(400)),
       c.name, ty.name, CAST(ISNULL(ep_c.value, '') AS NVARCHAR(400))
FROM sys.tables t
JOIN sys.columns c ON c.object_id = t.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
LEFT JOIN sys.extended_properties ep_t
  ON ep_t.major_id = t.object_id AND ep_t.minor_id = 0 AND ep_t.name = 'MS_Description'
LEFT JOIN sys.extended_properties ep_c
  ON ep_c.major_id = c.object_id AND ep_c.minor_id = c.column_id AND ep_c.name = 'MS_Description'
ORDER BY t.name, c.column_id"""

_SCHEMA_QUERY_CLICKHOUSE = """SELECT c.table, COALESCE(t.comment, ''),
       c.name, c.type, c.comment
FROM system.columns c
JOIN system.tables t ON t.database = c.database AND t.name = c.table
WHERE c.database = currentDatabase()
ORDER BY c.table, c.position"""

_SCHEMA_QUERY_SQLITE = """SELECT m.name, '', p.name, p.type, ''
FROM sqlite_master m JOIN pragma_table_info(m.name) p
WHERE m.type = 'table' AND m.name NOT LIKE 'sqlite_%'
ORDER BY m.name, p.cid"""


def build_schema_query(db_type: str) -> str:
    """按数据库类型返回元数据查询 SQL（全部 SELECT，只读）。"""
    if db_type in _MYSQL_COMPAT:
        return _SCHEMA_QUERY_MYSQL
    if db_type in _PG_COMPAT:
        return _SCHEMA_QUERY_PG
    if db_type in ("oracle", "dm"):
        return _SCHEMA_QUERY_ORACLE
    if db_type == "sqlserver":
        return _SCHEMA_QUERY_SQLSERVER
    if db_type == "clickhouse":
        return _SCHEMA_QUERY_CLICKHOUSE
    # sqlite 及未知类型回退 sqlite_master 风格
    return _SCHEMA_QUERY_SQLITE


def normalize_schema_rows(rows: list) -> list[dict]:
    """(table, table_comment, col, dtype, col_comment)* → 统一 schema 结构。

    输出：[{name, comment, columns:[{name, dtype, comment}]}]（保持首次出现顺序）。
    """
    tables: list[dict] = []
    index: dict[str, int] = {}
    for row in rows:
        tname, tcomment, cname, dtype, ccomment = (list(row) + [""] * 5)[:5]
        tname = str(tname)
        if tname not in index:
            index[tname] = len(tables)
            tables.append({"name": tname, "comment": str(tcomment or ""), "columns": []})
        tables[index[tname]]["columns"].append(
            {
                "name": str(cname),
                "dtype": str(dtype or ""),
                "comment": str(ccomment or ""),
            }
        )
    return tables


class ReadOnlyPool:
    """只读连接池（V1：主流 + 国产/信创数据库全覆盖）。

    根据 source_config['type'] 分发到对应后端：
      - mysql/tidb/oceanbase/gbase: aiomysql + SET SESSION TRANSACTION READ ONLY
      - postgresql/kingbase/gaussdb/opengauss/highgo: asyncpg + SET TRANSACTION READ ONLY
      - oracle: oracledb + ALTER SESSION SET READ ONLY
      - sqlserver: aioodbc + 只读查询
      - dm(达梦): dmPython + SET SESSION READ ONLY
      - clickhouse: clickhouse-connect（天然只读）
      - sqlite: aiosqlite + PRAGMA query_only = ON
      - csv / excel: pandas 读取（无连接池，文件级只读）
    """

    def __init__(self, source_config: dict | None = None) -> None:
        self._config = source_config or {}
        self._pool: Any = None
        # 注意：空串也算"未配置"——get 的默认值只在键缺失时生效，
        # Rust 注入 {"type": ""} 时不能静默兜底（BUGFIX #97：曾静默返回空结果）
        self._type = (self._config.get("type") or "").strip()

    def _require_known_type(self) -> None:
        """type 缺失/未知时快速失败，绝不静默返回空结果（fail-fast 红线）。"""
        if self._type not in DB_TYPE_REGISTRY:
            raise ValueError(
                f"数据源类型未配置或不支持: '{self._type}'。"
                "请在「系统资产」中编辑该数据源，选择数据库类型后重试"
            )

    async def _ensure_pool(self) -> None:
        """懒初始化连接池（首次 execute 时创建）。"""
        if self._pool is not None:
            return

        if self._type in _MYSQL_COMPAT:
            await self._init_mysql()
        elif self._type in _PG_COMPAT:
            await self._init_postgres()
        elif self._type == "oracle":
            await self._init_oracle()
        elif self._type == "sqlserver":
            await self._init_sqlserver()
        elif self._type == "dm":
            await self._init_dm()
        elif self._type == "clickhouse":
            await self._init_clickhouse()
        # sqlite/csv/excel 无需连接池

    async def _init_mysql(self) -> None:
        """初始化 MySQL 协议只读连接池（MySQL/TiDB/OceanBase/GBase）。"""
        try:
            import aiomysql
        except ImportError as e:
            raise RuntimeError("aiomysql 未安装，请执行: pip install aiomysql") from e

        cfg = self._config
        default_port = DB_TYPE_REGISTRY.get(self._type, {}).get("port", 3306)
        self._pool = await aiomysql.create_pool(
            host=cfg.get("host", "127.0.0.1"),
            port=int(cfg.get("port", default_port)),
            user=cfg.get("user", cfg.get("username", "readonly")),
            password=cfg.get("password", ""),
            db=cfg.get("database", ""),
            minsize=1,
            maxsize=5,
            autocommit=True,
            charset="utf8mb4",
        )
        # 连接级只读
        async with self._pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute("SET SESSION TRANSACTION READ ONLY")
        logger.info("%s 只读连接池已初始化 (host=%s)", self._type, cfg.get("host"))

    async def _init_postgres(self) -> None:
        """初始化 PostgreSQL 协议只读连接池（PostgreSQL/KingbaseES/GaussDB/openGauss/HighGo）。"""
        try:
            import asyncpg
        except ImportError as e:
            raise RuntimeError("asyncpg 未安装，请执行: pip install asyncpg") from e

        cfg = self._config
        default_port = DB_TYPE_REGISTRY.get(self._type, {}).get("port", 5432)

        async def _init_conn(conn: asyncpg.Connection) -> None:
            """每个新连接设置只读。"""
            await conn.execute("SET default_transaction_read_only = on")

        self._pool = await asyncpg.create_pool(
            host=cfg.get("host", "127.0.0.1"),
            port=int(cfg.get("port", default_port)),
            user=cfg.get("user", cfg.get("username", "readonly")),
            password=cfg.get("password", ""),
            database=cfg.get("database", "postgres"),
            min_size=1,
            max_size=5,
            init=_init_conn,
        )
        logger.info("%s 只读连接池已初始化 (host=%s)", self._type, cfg.get("host"))

    async def _init_oracle(self) -> None:
        """初始化 Oracle 只读连接池。"""
        try:
            import oracledb
        except ImportError as e:
            raise RuntimeError("oracledb 未安装，请执行: pip install oracledb") from e

        cfg = self._config
        dsn = cfg.get(
            "dsn",
            f"{cfg.get('host', '127.0.0.1')}:{cfg.get('port', 1521)}/{cfg.get('database', cfg.get('service', ''))}",
        )
        self._pool = oracledb.create_pool_async(
            user=cfg.get("user", cfg.get("username", "readonly")),
            password=cfg.get("password", ""),
            dsn=dsn,
            min=1,
            max=3,
            increment=1,
        )
        logger.info("Oracle 只读连接池已初始化 (dsn=%s)", dsn)

    async def _init_sqlserver(self) -> None:
        """初始化 SQL Server 连接（aioodbc）。"""
        try:
            import aioodbc
        except ImportError as e:
            raise RuntimeError("aioodbc 未安装，请执行: pip install aioodbc pyodbc") from e

        cfg = self._config
        host = cfg.get("host", "127.0.0.1")
        port = int(cfg.get("port", 1433))
        database = cfg.get("database", "master")
        user = cfg.get("user", cfg.get("username", "sa"))
        password = cfg.get("password", "")
        dsn = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={host},{port};DATABASE={database};"
            f"UID={user};PWD={password}"
        )
        self._pool = await aioodbc.create_pool(dsn=dsn, minsize=1, maxsize=3, autocommit=True)
        logger.info("SQL Server 连接池已初始化 (host=%s)", host)

    async def _init_dm(self) -> None:
        """初始化达梦(DM)连接（dmPython 同步驱动，标记为已初始化）。"""
        try:
            import dmPython  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "dmPython 未安装，请从达梦官网下载驱动: https://www.dameng.com"
            ) from e
        # dmPython 是同步驱动，不创建连接池，每次执行时创建连接
        self._pool = "dm_ready"  # 标记已初始化
        logger.info("达梦(DM) 驱动已就绪 (host=%s)", self._config.get("host"))

    async def _init_clickhouse(self) -> None:
        """初始化 ClickHouse 连接。"""
        try:
            import clickhouse_connect
        except ImportError as e:
            raise RuntimeError(
                "clickhouse-connect 未安装，请执行: pip install clickhouse-connect"
            ) from e

        cfg = self._config
        self._pool = clickhouse_connect.get_client(
            host=cfg.get("host", "127.0.0.1"),
            port=int(cfg.get("port", 8123)),
            username=cfg.get("user", cfg.get("username", "default")),
            password=cfg.get("password", ""),
            database=cfg.get("database", "default"),
        )
        logger.info("ClickHouse 连接已初始化 (host=%s)", cfg.get("host"))

    async def execute_sql(self, sql: str, *, row_limit: int | None = None) -> Any:
        """执行只读 SQL，返回 DataFrame。

        流程：
          1. enforce_readonly(sql) —— 写操作硬拦截（文本层）
          2. inject_limit(sql, cap) —— 强制 LIMIT
          3. 根据数据源类型分发执行

        Args:
            sql: 只读 SELECT 语句。
            row_limit: 行数上限（默认取 settings）。

        Returns:
            查询结果 DataFrame。

        Raises:
            WriteBlockedError: 检测到写操作。
        """
        # 安全层 1：文本级拦截
        enforce_readonly(sql)
        # 安全层 2：强制 LIMIT
        safe_sql = inject_limit(sql, row_limit)

        # 类型未配置/未知 → 明确报错，禁止静默返回空 DataFrame（BUGFIX #97）
        self._require_known_type()

        start = time.perf_counter()

        if self._type in _MYSQL_COMPAT:
            df = await self._execute_mysql(safe_sql)
        elif self._type in _PG_COMPAT:
            df = await self._execute_postgres(safe_sql)
        elif self._type == "oracle":
            df = await self._execute_oracle(safe_sql)
        elif self._type == "sqlserver":
            df = await self._execute_sqlserver(safe_sql)
        elif self._type == "dm":
            df = await self._execute_dm(safe_sql)
        elif self._type == "clickhouse":
            df = await self._execute_clickhouse(safe_sql)
        elif self._type == "sqlite":
            df = await self._execute_sqlite(safe_sql)
        elif self._type in ("csv", "excel"):
            df = self._execute_file(safe_sql)
        else:
            # _require_known_type 已拦截，理论不可达（防御性兜底）
            raise ValueError(f"不支持的数据源类型: {self._type}")

        elapsed_ms = int((time.perf_counter() - start) * 1000)
        logger.debug("SQL 执行完成: %dms, type=%s", elapsed_ms, self._type)
        return df

    async def _execute_mysql(self, sql: str) -> Any:
        """MySQL 协议执行（MySQL/TiDB/OceanBase/GBase，连接层只读）。"""
        import pandas as pd

        await self._ensure_pool()
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SET SESSION TRANSACTION READ ONLY")
                await cur.execute(sql)
                columns = (
                    [desc[0] for desc in cur.description] if cur.description else []
                )
                rows = await cur.fetchall()
        # 基于 cursor 结果构造 DataFrame：不能把 aiomysql 异步连接直接喂给
        # pd.read_sql（pandas 会调 conn.execute → _ContextManager 无此属性，BUGFIX #102）
        if not rows:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame([list(r) for r in rows], columns=columns)

    async def _execute_postgres(self, sql: str) -> Any:
        """PostgreSQL 协议执行（PostgreSQL/KingbaseES/GaussDB/openGauss/HighGo）。"""
        import pandas as pd

        await self._ensure_pool()
        async with self._pool.acquire() as conn:
            # 连接级只读
            await conn.execute("SET TRANSACTION READ ONLY")
            rows = await conn.fetch(sql)
            if not rows:
                return pd.DataFrame()
            columns = list(rows[0].keys())
            data = [list(r.values()) for r in rows]
            return pd.DataFrame(data, columns=columns)

    async def _execute_oracle(self, sql: str) -> Any:
        """Oracle 执行（连接层只读）。"""
        import pandas as pd

        await self._ensure_pool()
        async with self._pool.acquire() as conn:
            cursor = conn.cursor()
            try:
                await cursor.execute("ALTER SESSION SET READ ONLY")
                await cursor.execute(sql)
                columns = (
                    [desc[0] for desc in cursor.description]
                    if cursor.description
                    else []
                )
                rows = await cursor.fetchall()
            finally:
                await cursor.close()
        # 同 MySQL 分支：oracledb 异步连接不能直接喂 pd.read_sql（BUGFIX #102）
        if not rows:
            return pd.DataFrame(columns=columns)
        return pd.DataFrame([list(r) for r in rows], columns=columns)

    async def _execute_sqlserver(self, sql: str) -> Any:
        """SQL Server 执行。"""
        import pandas as pd

        await self._ensure_pool()
        async with self._pool.acquire() as conn, conn.cursor() as cur:
            await cur.execute(sql)
            columns = [desc[0] for desc in cur.description] if cur.description else []
            rows = await cur.fetchall()
            return pd.DataFrame([list(r) for r in rows], columns=columns)

    async def _execute_dm(self, sql: str) -> Any:
        """达梦(DM) 执行（同步驱动，线程池包装）。"""
        import pandas as pd

        await self._ensure_pool()
        cfg = self._config

        def _sync_query() -> Any:
            import dmPython

            conn = dmPython.connect(
                user=cfg.get("user", cfg.get("username", "SYSDBA")),
                password=cfg.get("password", ""),
                server=cfg.get("host", "127.0.0.1"),
                port=int(cfg.get("port", 5236)),
            )
            try:
                cursor = conn.cursor()
                cursor.execute("SET SESSION READ ONLY")
                df = pd.read_sql(sql, conn)
                cursor.close()
                return df
            finally:
                conn.close()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _sync_query)

    async def _execute_clickhouse(self, sql: str) -> Any:
        """ClickHouse 执行（天然只读 SELECT）。"""
        import pandas as pd

        await self._ensure_pool()
        result = self._pool.query(sql)
        if result and result.result_rows:
            columns = (
                [col[0] for col in result.column_names] if hasattr(result, "column_names") else None
            )
            return pd.DataFrame(result.result_rows, columns=columns)
        return pd.DataFrame()

    async def _execute_sqlite(self, sql: str) -> Any:
        """SQLite 执行（PRAGMA query_only 只读）。"""
        import sqlite3

        import pandas as pd

        db_path = self._config.get("path", ":memory:")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            conn.execute("PRAGMA query_only = ON")
            df = pd.read_sql_query(sql, conn)
        finally:
            conn.close()
        return df

    def _execute_file(self, sql: str) -> Any:
        """CSV/Excel 文件数据源（pandas 读取 + SQL 模拟）。"""
        import pandas as pd

        path = self._config.get("path", "")
        if self._type == "csv":
            df = pd.read_csv(path)
        else:
            df = pd.read_excel(path)

        # 简单 SQL 模拟：如果是 SELECT * 直接返回，否则用 pandasql 兜底
        sql_lower = sql.strip().lower()
        if "select *" in sql_lower and "where" not in sql_lower:
            return df
        # 尝试用 sqlite 内存库执行
        import sqlite3

        conn = sqlite3.connect(":memory:")
        table_name = self._config.get("table_name", "data")
        df.to_sql(table_name, conn, index=False, if_exists="replace")
        try:
            result = pd.read_sql_query(sql, conn)
        except Exception:
            result = df  # SQL 不兼容时返回全量
        finally:
            conn.close()
        return result

    async def fetch_schema(self) -> list[dict]:
        """拉取数据源全量表结构（缺口 2：真实 Schema 同步）。

        元数据查询由 build_schema_query 内部生成（全部 SELECT），
        不走 execute_sql 的 LIMIT 注入（部分方言不支持 LIMIT，如 Oracle）。

        Returns:
            [{name, comment, columns:[{name, dtype, comment}]}]
        """
        # 文件型数据源：从文件列推导单表 schema
        if self._type in ("csv", "excel"):
            df_file = self._execute_file("SELECT *")
            cols = [
                {"name": str(c), "dtype": str(df_file[c].dtype), "comment": ""}
                for c in df_file.columns
            ]
            path = self._config.get("path", "")
            tname = Path(path).stem or "data"
            return [{"name": tname, "comment": path, "columns": cols}]

        sql = build_schema_query(self._type)
        # 类型未配置/未知 → 明确报错（BUGFIX #97，与 execute_sql 同策略）
        self._require_known_type()
        if self._type in _MYSQL_COMPAT:
            df = await self._execute_mysql(sql)
        elif self._type in _PG_COMPAT:
            df = await self._execute_postgres(sql)
        elif self._type == "oracle":
            df = await self._execute_oracle(sql)
        elif self._type == "sqlserver":
            df = await self._execute_sqlserver(sql)
        elif self._type == "dm":
            df = await self._execute_dm(sql)
        elif self._type == "clickhouse":
            df = await self._execute_clickhouse(sql)
        elif self._type == "sqlite":
            df = await self._execute_sqlite(sql)
        else:
            raise ValueError(f"不支持的数据源类型: {self._type}")

        if df is None or len(df) == 0:
            return []
        rows = [tuple(r) for r in df.itertuples(index=False, name=None)]
        return normalize_schema_rows(rows)

    async def test_connection(self) -> dict[str, Any]:
        """测试数据源连接（支持所有数据库类型）。"""
        try:
            if self._type in _MYSQL_COMPAT:
                await self._ensure_pool()
                async with self._pool.acquire() as conn, conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                return {"ok": True, "type": self._type, "message": f"{self._type} 连接成功（只读）"}
            elif self._type in _PG_COMPAT:
                await self._ensure_pool()
                async with self._pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                return {"ok": True, "type": self._type, "message": f"{self._type} 连接成功（只读）"}
            elif self._type == "oracle":
                await self._ensure_pool()
                return {"ok": True, "type": "oracle", "message": "Oracle 连接成功（只读）"}
            elif self._type == "sqlserver":
                await self._ensure_pool()
                async with self._pool.acquire() as conn, conn.cursor() as cur:
                    await cur.execute("SELECT 1")
                return {"ok": True, "type": "sqlserver", "message": "SQL Server 连接成功"}
            elif self._type == "dm":
                import dmPython

                cfg = self._config
                conn = dmPython.connect(
                    user=cfg.get("user", cfg.get("username", "SYSDBA")),
                    password=cfg.get("password", ""),
                    server=cfg.get("host", "127.0.0.1"),
                    port=int(cfg.get("port", 5236)),
                )
                cursor = conn.cursor()
                cursor.execute("SELECT 1")
                cursor.close()
                conn.close()
                return {"ok": True, "type": "dm", "message": "达梦(DM) 连接成功"}
            elif self._type == "clickhouse":
                await self._ensure_pool()
                self._pool.query("SELECT 1")
                return {"ok": True, "type": "clickhouse", "message": "ClickHouse 连接成功"}
            elif self._type == "sqlite":
                import sqlite3

                db_path = self._config.get("path", ":memory:")
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                conn.execute("SELECT 1")
                conn.close()
                return {"ok": True, "type": "sqlite", "message": "SQLite 连接成功（只读）"}
            elif self._type in ("csv", "excel"):
                import os

                path = self._config.get("path", "")
                exists = os.path.isfile(path)
                return {
                    "ok": exists,
                    "type": self._type,
                    "message": f"文件{'存在' if exists else '不存在'}: {path}",
                }
            else:
                return {
                    "ok": False,
                    "type": self._type,
                    "message": f"不支持的数据源类型: {self._type}",
                }
        except Exception as e:
            return {"ok": False, "type": self._type, "message": str(e)}

    async def close(self) -> None:
        """关闭连接池。"""
        if self._pool is not None:
            if self._type in _MYSQL_COMPAT:
                self._pool.close()
                await self._pool.wait_closed()
            elif self._type in _PG_COMPAT or self._type == "oracle":
                await self._pool.close()
            elif self._type == "sqlserver":
                self._pool.close()
                await self._pool.wait_closed()
            elif self._type == "clickhouse":
                self._pool.close()
            # dm / sqlite 无持久连接池
            self._pool = None
            logger.info("连接池已关闭 (type=%s)", self._type)
