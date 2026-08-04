"""RPA MCP server config."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EAIDE_RPA_", extra="ignore")

    allowed_domains: list[str] = []
    tool_timeout_sec: int = 30
    user_agent: str = "eaide-rpa/0.1 (playwright)"