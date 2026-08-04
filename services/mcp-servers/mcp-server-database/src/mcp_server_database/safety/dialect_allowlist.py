"""Allow-list of supported SQL dialects.

Restricting the dialect set prevents an attacker from injecting dialect-
specific constructs (e.g. PG `\\copy`, SQLite `ATTACH DATABASE`) that the
generic validator wouldn't recognise.

`ansi` is our virtual default — it has no exact sqlglot counterpart, so we
map it to None and let sqlglot use its built-in default parser.
"""
from __future__ import annotations


ALLOWED_DIALECTS: frozenset[str] = frozenset({
    "ansi",
    "postgres", "mysql", "sqlite", "tsql",
    "snowflake", "bigquery", "redshift", "duckdb", "clickhouse",
})


def to_sqlglot_dialect(dialect: str) -> str | None:
    """Translate our logical dialect id into a sqlglot-readable name."""
    if dialect.lower() == "ansi":
        return None  # use sqlglot default
    return dialect.lower()


# Mapping from our logical connection name suffix to the sqlglot dialect id.
# Connection names like `orders_pg`, `billing_my` map to `postgres` / `mysql`.
CONNECTION_SUFFIX_TO_DIALECT: dict[str, str] = {
    "pg": "postgres",
    "postgres": "postgres",
    "postgresql": "postgres",
    "my": "mysql",
    "mysql": "mysql",
    "sq": "sqlite",
    "sqlite": "sqlite",
    "ms": "tsql",
    "tsql": "tsql",
    "mssql": "tsql",
    "sf": "snowflake",
    "snowflake": "snowflake",
    "bq": "bigquery",
    "bigquery": "bigquery",
    "rs": "redshift",
    "redshift": "redshift",
    "ck": "clickhouse",
    "clickhouse": "clickhouse",
    "ddb": "duckdb",
    "duckdb": "duckdb",
}


class UnsupportedDialectError(Exception):
    pass


def assert_dialect_allowed(dialect: str) -> None:
    if dialect.lower() not in ALLOWED_DIALECTS:
        raise UnsupportedDialectError(f"dialect not allowed: {dialect!r}")


def dialect_from_connection(name: str) -> str:
    """Infer the sqlglot dialect id from a connection name like `orders_pg`."""
    # Try the suffix first
    parts = name.rsplit("_", 1)
    if len(parts) == 2 and parts[1].lower() in CONNECTION_SUFFIX_TO_DIALECT:
        return CONNECTION_SUFFIX_TO_DIALECT[parts[1].lower()]
    # Fall back to the whole-name match (orders_postgres etc.)
    if name.lower() in CONNECTION_SUFFIX_TO_DIALECT:
        return CONNECTION_SUFFIX_TO_DIALECT[name.lower()]
    # Default — let upstream config decide
    return "ansi"