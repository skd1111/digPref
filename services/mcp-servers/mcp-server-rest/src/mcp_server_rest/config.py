"""REST MCP server config."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EAIDE_REST_", extra="ignore")

    allowed_hosts: list[str] = []  # list of allowed hostnames (exact match)
    allowed_methods_by_host: dict[str, list[str]] = {}  # host -> methods
    tool_timeout_sec: int = 10
    max_body_bytes: int = 1_000_000