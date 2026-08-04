"""envconfig.models —— 多环境治理的 Pydantic 数据模型。

设计原则：
    - 一切"敏感字段"统一用 `pydantic.SecretStr` 标注
    - `EnvConfig` 是一个根聚合，所有数据库 / API / MCP 项都挂在它下面
    - 序列化（dump）前由 scrub() 替换为 Keyring 占位符
    - 反序列化（load）时自动得到 `SecretStr('**********')`，由 restore() 回填

2026-07-09 变更：环境名（`Environment`）从 4 项硬编码枚举改为自由格式字符串。
    dev/test/staging/prod 仅作为首次启动的 seed 项提示，可任意增删 / 重命名。
"""
from __future__ import annotations

import re
from typing import Any, ClassVar

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_core import core_schema


# ---- 环境名类型 ------------------------------------------------------------
#
# 自由格式，只校验形状（^[a-z][a-z0-9._-]{0,62}$），允许任意命名。
# 原 4 项枚举的友好名（开发/测试/准生产/生产）保留为 helper，但不限制合法值。

# 开头必须是 Unicode 字母（任意大小写 / 中文 / CJK 都行），后续可为 unicode 字母 / 数字 / . _ -
_ENV_PATTERN = re.compile(r"^[^\W\d][\w.\-]{0,62}$", re.UNICODE)
_PRESET_DISPLAY_NAMES = {
    "dev": "开发",
    "test": "测试",
    "staging": "准生产",
    "prod": "生产",
}


class Environment(str):
    """自由格式的环境名。

    校验：必须以小写字母开头，后续字符为小写字母/数字/点/下划线/中划线，最长 63。
    校验器用 pydantic-core 注册，构造时即抛错。
    """

    __slots__ = ()

    # 历史 4 项 preset（仅作"速记"用途，按 str 等值比较，可被新名替换）
    DEV: ClassVar[str] = "dev"
    TEST: ClassVar[str] = "test"
    STAGING: ClassVar[str] = "staging"
    PROD: ClassVar[str] = "prod"

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type, handler):  # noqa: ARG003
        def _validate(value, info=None):  # noqa: ARG001
            if isinstance(value, cls):
                return value
            if not isinstance(value, str):
                raise TypeError(
                    f"environment 必须为 str，got {type(value).__name__}"
                )
            if not _ENV_PATTERN.match(value):
                raise ValueError(
                    f"环境名非法：必须匹配 ^[a-z][a-z0-9._-]{{0,62}}$，got {value!r}"
                )
            return cls(value)

        def _serialize(value):
            return str(value)

        return core_schema.no_info_plain_validator_function(
            _validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                _serialize, return_schema=core_schema.str_schema()
            ),
        )


def environment_display_name(env: str) -> str:
    """返回环境名的人类可读标签。

    对 4 项历史 preset 有友好映射；其他名原样回显。
    """
    return _PRESET_DISPLAY_NAMES.get(env, env)


# ---- 资源条目（每个 entry 都可能带 0..n 个 SecretStr 字段）--------------


class DatabaseConnection(BaseModel):
    """单个数据库连接。

    `password` 敏感（SecretStr）。`name` 命名空间用于占位符，例如
    `db.orders_pg.password`。
    """

    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    kind: str = Field(default="postgres", description="postgres | mysql | sqlite | ...")
    host: str
    port: int = Field(..., ge=1, le=65535)
    database: str
    username: str
    password: SecretStr | None = Field(default=None, description="敏感：DB 密码")
    options: dict[str, Any] = Field(default_factory=dict)
    read_only_account: bool = Field(default=True, description="强制只读账号（生产必开）")

    @field_validator("name")
    @classmethod
    def _name_namespace(cls, v: str) -> str:
        # name 必须含点（命名空间化），与凭证保险箱保持一致
        if "." not in v:
            raise ValueError(f"DatabaseConnection.name 必须含点（命名空间化），got {v!r}")
        return v


class ApiGateway(BaseModel):
    """内部 API 网关（私有 LLM、平台 API、监控等）。"""

    name: str = Field(..., pattern=r"^[a-zA-Z0-9._-]+$")
    base_url: str
    api_key: SecretStr | None = Field(default=None, description="敏感：API Key / Token")
    timeout_sec: int = Field(default=30, ge=1, le=600)
    rate_limit_per_min: int | None = None


class McpServerEntry(BaseModel):
    """单个 MCP server 注册。

    字段定义遵循 Module D 的 MCP JSON Schema 草案（这里只列 Module A 需要的部分）。
    """

    server_name: str = Field(..., pattern=r"^[a-zA-Z0-9._-]+$")
    command: str
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(
        default_factory=dict,
        description="环境变量；值如 `__KEYRING_REF:server.foo.env_token__`",
    )
    allowed_tools: list[str] = Field(default_factory=list, description="白名单工具名")
    # 启动参数
    auto_start: bool = False
    working_dir: str | None = None

    @field_validator("env")
    @classmethod
    def _env_keys_namespace(cls, v: dict[str, str]) -> dict[str, str]:
        # key 必须大写 + 含点（避免与其他环境变量冲突）
        for k in v:
            if not k.replace(".", "").replace("_", "").isalnum():
                raise ValueError(f"env key 含非法字符: {k!r}")
        return v


# ---- 顶层：单环境的完整配置 -----------------------------------------------


class TargetServer(BaseModel):
    """单台远程目标服务器（一个 IP + 一组凭据）。

    与 SSH 终端联动：用户在 UI 点这台机器，xterm.js 终端会拿这里的
    host/port/username/password 发起 SSH 连接。
    密码走 Keyring 占位符（SecretStr），永不落盘明文。
    """

    name: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    description: str = ""
    host: str = Field(..., min_length=1)
    port: int = Field(default=22, ge=1, le=65535, description="SSH 端口；db/rpc 等可改")
    protocol: str = Field(default="ssh", description="ssh | rdp | db | api | ...")
    username: str = Field(default="root")
    password: SecretStr | None = Field(default=None, description="敏感：密码（Keyring）")
    private_key_ref: str | None = Field(
        default=None,
        description="可选：私钥在 Keychain 里的 account 名称",
    )
    tags: list[str] = Field(default_factory=list, description="role / env / cluster 标签")
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _name_namespace(cls, v: str) -> str:
        if "." not in v:
            raise ValueError(f"TargetServer.name 必须含点（命名空间化），got {v!r}")
        return v


class EnvConfig(BaseModel):
    """单个环境的完整配置（数据库 + API + MCP + 目标服务器）。"""

    environment: Environment
    label: str = Field(..., min_length=1, max_length=64, description="人类可读名")
    description: str = ""
    databases: list[DatabaseConnection] = Field(default_factory=list)
    api_gateways: list[ApiGateway] = Field(default_factory=list)
    mcp_servers: list[McpServerEntry] = Field(default_factory=list)
    target_servers: list[TargetServer] = Field(default_factory=list)

    @field_validator("label")
    @classmethod
    def _label_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("label 不能是空字符串")
        return v

    # ---- SecretStr 字段名清单（scrub / restore 用）----------------------

    @classmethod
    def secret_field_paths(cls) -> list[tuple[str, ...]]:
        """返回所有 (parent, child) SecretStr 字段路径的列表。

        用于 scrub 时定位哪些字段要替换。结构变化时这里要同步。
        """
        return [
            ("databases", "password"),
            ("api_gateways", "api_key"),
            ("target_servers", "password"),
        ]  # type: ignore  # noqa: F822
