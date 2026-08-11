"""A.1 单测：Pydantic 模型 + scrub/restore。

覆盖：
    - 模型构造：合法 + 各种非法输入
    - scrub：SecretStr → 占位符
    - restore：占位符 → SecretStr（含缺失报错）
    - 占位符格式合法性
"""

from __future__ import annotations

import pytest
from agent.envconfig import (
    ApiGateway,
    DatabaseConnection,
    EnvConfig,
    Environment,
    McpServerEntry,
    PlaceholderMissing,
    is_placeholder,
    make_placeholder,
    parse_placeholder,
    restore_secrets,
    scrub,
)
from pydantic import SecretStr, ValidationError

# ---- 模型基本构造 -------------------------------------------------------


class TestModels:
    def test_minimal_env(self):
        cfg = EnvConfig(environment=Environment.DEV, label="开发环境")
        assert cfg.environment == Environment.DEV
        assert cfg.label == "开发环境"
        assert cfg.databases == []
        assert cfg.api_gateways == []
        assert cfg.mcp_servers == []

    def test_full_env(self):
        cfg = EnvConfig(
            environment=Environment.PROD,
            label="生产",
            description="线上生产环境",
            databases=[
                DatabaseConnection(
                    name="orders.pg",
                    kind="postgres",
                    host="db-1.prod.example.com",
                    port=5432,
                    database="orders",
                    username="readonly",
                    password=SecretStr("super-secret-pwd"),
                ),
            ],
            api_gateways=[
                ApiGateway(
                    name="internal.llm",
                    base_url="http://172.1.0.134:8000/v1",
                    api_key=SecretStr("sk-internal-xxx"),
                ),
            ],
            mcp_servers=[
                McpServerEntry(
                    server_name="mcp.db",
                    command="uv",
                    args=["run", "mcp-server-database"],
                    env={"EAIDE_DB_DSN_ORDERS_PG": "__KEYRING_REF:mcp.db.env.dsn__"},
                    allowed_tools=["db.query", "db.schema"],
                ),
            ],
        )
        assert len(cfg.databases) == 1
        assert cfg.databases[0].password.get_secret_value() == "super-secret-pwd"
        assert cfg.api_gateways[0].api_key.get_secret_value() == "sk-internal-xxx"

    def test_db_name_requires_dot(self):
        with pytest.raises(ValidationError):
            DatabaseConnection(
                name="orders_pg",  # 缺点
                kind="postgres",
                host="db",
                port=5432,
                database="x",
                username="u",
            )

    def test_env_label_must_be_nonempty(self):
        with pytest.raises(ValidationError):
            EnvConfig(environment=Environment.DEV, label="   ")

    def test_secret_field_paths_lists_all(self):
        paths = EnvConfig.secret_field_paths()
        # 当前版本只有 databases.password 和 api_gateways.api_key
        assert ("databases", "password") in paths
        assert ("api_gateways", "api_key") in paths


# ---- 占位符格式 -----------------------------------------------------------


class TestPlaceholderFormat:
    def test_valid(self):
        s = make_placeholder("db.orders_pg.password")
        assert s == "__KEYRING_REF:db.orders_pg.password__"
        assert is_placeholder(s)
        assert parse_placeholder(s) == "db.orders_pg.password"

    def test_rejects_invalid_chars(self):
        with pytest.raises(ValueError):
            make_placeholder("db:orders")  # 冒号不允许
        with pytest.raises(ValueError):
            make_placeholder("db orders")  # 空格不允许

    def test_is_placeholder_strict(self):
        assert is_placeholder("__KEYRING_REF:x__")
        assert not is_placeholder("__KEYRING_REF::x__")  # 内部含 :
        assert not is_placeholder("KEYRING_REF:x__")  # 缺前缀下划线
        assert not is_placeholder("plain text")


# ---- scrub --------------------------------------------------------------


class TestScrub:
    def test_scrub_replaces_password(self):
        cfg = EnvConfig(
            environment=Environment.PROD,
            label="生产",
            databases=[
                DatabaseConnection(
                    name="orders.pg",
                    kind="postgres",
                    host="db",
                    port=5432,
                    database="x",
                    username="u",
                    password=SecretStr("super-secret"),
                ),
            ],
        )
        dumped = scrub(cfg)
        # 序列化后 passwords 都成占位符
        assert dumped["databases"][0]["password"] == "__KEYRING_REF:databases.orders.pg.password__"
        # **没有明文** —— 这是安全红线的硬约束
        assert "super-secret" not in str(dumped)

    def test_scrub_replaces_api_key(self):
        cfg = EnvConfig(
            environment=Environment.DEV,
            label="dev",
            api_gateways=[
                ApiGateway(
                    name="internal.llm",
                    base_url="http://x",
                    api_key=SecretStr("sk-abc"),
                ),
            ],
        )
        dumped = scrub(cfg)
        assert (
            dumped["api_gateways"][0]["api_key"]
            == "__KEYRING_REF:api_gateways.internal.llm.api_key__"
        )
        assert "sk-abc" not in str(dumped)

    def test_scrub_handles_none_secrets(self):
        cfg = EnvConfig(
            environment=Environment.DEV,
            label="dev",
            databases=[
                DatabaseConnection(
                    name="a.b",
                    kind="postgres",
                    host="h",
                    port=1,
                    database="d",
                    username="u",
                    password=None,
                ),
            ],
        )
        dumped = scrub(cfg)
        # None 不会变成占位符
        assert dumped["databases"][0]["password"] is None


# ---- restore --------------------------------------------------------------


class TestRestore:
    def _make_cfg(self) -> EnvConfig:
        return EnvConfig(
            environment=Environment.PROD,
            label="生产",
            databases=[
                DatabaseConnection(
                    name="orders.pg",
                    kind="postgres",
                    host="db",
                    port=5432,
                    database="x",
                    username="u",
                    password=SecretStr("__KEYRING_REF:databases.orders.pg.password__"),
                ),
            ],
            api_gateways=[
                ApiGateway(
                    name="internal.llm",
                    base_url="http://x",
                    api_key=SecretStr("__KEYRING_REF:api_gateways.internal.llm.api_key__"),
                ),
            ],
        )

    def test_restore_success(self):
        cfg = self._make_cfg()
        lookup = {
            "databases.orders.pg.password": "real-pwd",
            "api_gateways.internal.llm.api_key": "real-key",
        }
        out = restore_secrets(cfg, lookup)
        assert out.databases[0].password.get_secret_value() == "real-pwd"
        assert out.api_gateways[0].api_key.get_secret_value() == "real-key"

    def test_restore_missing_placeholder(self):
        cfg = self._make_cfg()
        with pytest.raises(PlaceholderMissing) as exc:
            restore_secrets(cfg, {"databases.orders.pg.password": "real-pwd"})
        # 缺失的是 api_key
        assert exc.value.account == "api_gateways.internal.llm.api_key"
        assert "找不到 keyring 里的对应值" in str(exc.value)

    def test_restore_empty_lookup(self):
        cfg = self._make_cfg()
        with pytest.raises(PlaceholderMissing):
            restore_secrets(cfg, {})

    def test_scrub_restore_roundtrip(self):
        """scrub → 重新构造 → restore 应该能完整还原。"""
        original = EnvConfig(
            environment=Environment.DEV,
            label="d",
            databases=[
                DatabaseConnection(
                    name="a.b",
                    kind="pg",
                    host="h",
                    port=1,
                    database="d",
                    username="u",
                    password=SecretStr("secret-1"),
                ),
            ],
            api_gateways=[
                ApiGateway(
                    name="c.d",
                    base_url="http://x",
                    api_key=SecretStr("key-1"),
                ),
            ],
        )
        # 1) scrub 出 dict
        dumped = scrub(original)
        # 2) 从 dumped 重建 EnvConfig（明文已不在）
        cfg_from_dump = EnvConfig.model_validate(dumped)
        # 验证原始 secret 不在 dumped
        assert "secret-1" not in str(dumped)
        assert "key-1" not in str(dumped)
        # 3) restore
        restored = restore_secrets(
            cfg_from_dump,
            {
                "databases.a.b.password": "secret-1",
                "api_gateways.c.d.api_key": "key-1",
            },
        )
        assert restored.databases[0].password.get_secret_value() == "secret-1"
        assert restored.api_gateways[0].api_key.get_secret_value() == "key-1"
