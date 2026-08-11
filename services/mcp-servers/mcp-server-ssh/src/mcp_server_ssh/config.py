"""SSH MCP 服务器配置。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EAIDE_SSH_", extra="ignore")

    allowed_hosts: list[str] = []
    # 额外允许的命令前缀（白名单之外的命令）。例如部署脚本：
    #   extra_allowed_commands: ["/opt/deploy/restart.sh ", "systemctl restart "]
    extra_allowed_commands: list[str] = []
    # 旧版黑名单（已废弃，保留用于向后兼容；新代码使用白名单机制）
    command_blacklist: list[str] = [
        "rm -rf /",
        "mkfs",
        "dd if=",
        ":(){:|:&};:",
        "shutdown",
        "reboot",
        "halt",
        "poweroff",
        "iptables -F",
        "userdel",
        "passwd",
    ]
    tool_timeout_sec: int = 30
