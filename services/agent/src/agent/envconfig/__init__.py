"""envconfig —— 多环境治理与配置管理。

本模块是 Module A 的核心：
    - Pydantic 模型：Environment / DatabaseConnection / ApiGateway / McpServerEntry / EnvConfig
    - 敏感字段统一用 Pydantic SecretStr 包裹
    - 持久化前调用 scrub() 把 SecretStr 替换为 `__KEYRING_REF:<account>__` 占位符
    - 加载时调用 restore() 校验占位符并回填（由调用方从 OS Keychain 拿到值后注入）
    - 真正的密钥永远不进任何 JSON / YAML 文件
"""
from __future__ import annotations

from .models import (
    ApiGateway,
    DatabaseConnection,
    EnvConfig,
    Environment,
    McpServerEntry,
    TargetServer,
)
from .scrub import (
    PLACEHOLDER_PREFIX,
    PLACEHOLDER_SUFFIX,
    PlaceholderMissing,
    is_placeholder,
    make_placeholder,
    parse_placeholder,
    restore_secrets,
    scrub,
)
from .storage import (
    Environments,
    EnvIndexEntry,
    config_dir,
    data_dir,
    delete_env,
    env_file,
    environments_file,
    envs_dir,
    get_active_env,
    index_file,
    list_envs,
    load_env,
    save_env,
    set_active_env,
)
from .export import (
    ExportResult,
    ImportResult,
    export_configs,
    import_configs,
)

__all__ = [
    "ApiGateway",
    "DatabaseConnection",
    "EnvConfig",
    "EnvIndexEntry",
    "Environments",
    "Environment",
    "ExportResult",
    "ImportResult",
    "McpServerEntry",
    "PLACEHOLDER_PREFIX",
    "PLACEHOLDER_SUFFIX",
    "PlaceholderMissing",
    "TargetServer",
    "config_dir",
    "data_dir",
    "delete_env",
    "env_file",
    "environments_file",
    "envs_dir",
    "export_configs",
    "get_active_env",
    "import_configs",
    "index_file",
    "is_placeholder",
    "list_envs",
    "load_env",
    "make_placeholder",
    "parse_placeholder",
    "restore_secrets",
    "save_env",
    "scrub",
    "set_active_env",
]
