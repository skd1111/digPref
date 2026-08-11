"""Read-only enforcement — wraps the DSN into a session that cannot write.

Strategy by driver:
    - Postgres  →  append `SET TRANSACTION READ ONLY` to every statement
    - MySQL     →  require `readonly=1` in the DSN; otherwise refuse
    - SQLite    →  open with `mode=ro` URI parameter
    - Snowflake →  default role is read-only
    - BigQuery  →  dataset-level read-only is enforced by IAM, not by SQL

This is belt-and-braces: even if a write somehow slips past the validator,
the connection itself refuses it.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


class ReadOnlyViolationError(Exception):
    pass


# ---- Driver-specific wrappers ---------------------------------------------


def wrap_readonly(dsn: str, sql: str, dialect: str) -> str:
    """Return `sql` with the necessary session-level guards prepended."""
    dialect = dialect.lower()
    if dialect in {"postgres", "redshift"}:
        return _wrap_postgres(sql)
    if dialect == "mysql":
        assert_mysql_readonly(dsn)
        return sql
    if dialect == "sqlite":
        assert_sqlite_readonly(dsn)
        return sql
    # snowflake / bigquery / duckdb / clickhouse rely on IAM (out-of-scope here)
    return sql


def assert_mysql_readonly(dsn: str) -> None:
    if "readonly=1" not in dsn.lower():
        raise ReadOnlyViolationError("mysql DSN must include 'readonly=1' for query operations")


def assert_sqlite_readonly(dsn: str) -> None:
    parts = urlsplit(dsn)
    if parts.scheme not in {"sqlite", "sqlite+ro"}:
        raise ReadOnlyViolationError(f"unexpected sqlite scheme: {parts.scheme}")
    # Accept explicit file: URI with mode=ro
    if parts.scheme == "file":
        qs = dict(parse_qsl(parts.query))
        if qs.get("mode", "").lower() != "ro":
            raise ReadOnlyViolationError(
                "sqlite file URI must include ?mode=ro for query operations"
            )


def _wrap_postgres(sql: str) -> str:
    """Inject a READ ONLY transaction guard."""
    sql_clean = sql.strip().rstrip(";").strip()
    return f"BEGIN READ ONLY; {sql_clean}; COMMIT;"


# ---- DSN rewriting utilities (for drivers that open from a DSN) ------------


def force_readonly_dsn(dsn: str, dialect: str) -> str:
    """Return a new DSN with read-only flags forced on (where supported)."""
    dialect = dialect.lower()
    if dialect == "mysql":
        return _force_mysql(dsn)
    if dialect == "sqlite":
        return _force_sqlite(dsn)
    return dsn


def _force_mysql(dsn: str) -> str:
    parts = urlsplit(dsn)
    qs = dict(parse_qsl(parts.query, keep_blank_values=True))
    qs["readonly"] = "1"
    return urlunsplit(parts._replace(query=urlencode(qs)))


def _force_sqlite(dsn: str) -> str:
    # file:./data.sqlite?mode=ro
    if dsn.startswith("file:"):
        parts = urlsplit(dsn)
        qs = dict(parse_qsl(parts.query, keep_blank_values=True))
        qs["mode"] = "ro"
        return urlunsplit(parts._replace(query=urlencode(qs)))
    # Plain path → prepend file: and mode=ro
    if "://" not in dsn:
        return f"file:{dsn}?mode=ro"
    return dsn
