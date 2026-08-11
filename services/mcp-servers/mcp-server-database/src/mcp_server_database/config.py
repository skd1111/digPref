"""MCP-server-database config."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="EAIDE_DB_",
        env_file=".env",
        extra="ignore",
    )

    # Connections: maps logical name -> DSN
    # e.g. {"orders_pg": "postgresql://readonly@db-1:5432/orders"}
    connections: dict[str, str] = {}

    tool_timeout_sec: int = 10
    default_row_limit: int = 50
    enforce_readonly_account: bool = True
