"""TargetServer 单测 —— SSH 联动相关字段。"""
from __future__ import annotations

import pytest
from pydantic import SecretStr, ValidationError

from agent.envconfig import (
    ApiGateway,
    DatabaseConnection,
    EnvConfig,
    Environment,
    McpServerEntry,
    TargetServer,
    export_configs,
    import_configs,
    restore_secrets,
    scrub,
)


class TestTargetServer:
    def test_minimal(self):
        t = TargetServer(name="web.prod.01", host="10.0.0.1")
        assert t.port == 22
        assert t.protocol == "ssh"
        assert t.username == "root"
        assert t.password is None
        assert t.enabled is True

    def test_name_must_have_dot(self):
        with pytest.raises(ValidationError):
            TargetServer(name="web_prod_01", host="x")

    def test_full_ssh_with_password(self):
        t = TargetServer(
            name="web.prod.01",
            description="Web server 01",
            host="10.0.0.1",
            port=2222,
            protocol="ssh",
            username="deploy",
            password=SecretStr("ssh-password"),
            tags=["web", "prod"],
        )
        assert t.password is not None
        assert t.password.get_secret_value() == "ssh-password"
        assert t.tags == ["web", "prod"]

    def test_rdp_target(self):
        t = TargetServer(
            name="db.prod.01",
            host="10.0.0.100",
            port=3389,
            protocol="rdp",
            username="administrator",
        )
        assert t.protocol == "rdp"
        assert t.port == 3389


class TestTargetServerScrub:
    def test_scrub_replaces_password(self):
        cfg = EnvConfig(
            environment=Environment.PROD,
            label="p",
            target_servers=[
                TargetServer(
                    name="web.prod.01",
                    host="10.0.0.1",
                    password=SecretStr("real-ssh-pwd"),
                ),
            ],
        )
        dumped = scrub(cfg)
        # 占位符已写入；明文不在
        assert dumped["target_servers"][0]["password"] == "__KEYRING_REF:target_servers.web.prod.01.password__"
        assert "real-ssh-pwd" not in str(dumped)

    def test_scrub_keeps_none_password(self):
        cfg = EnvConfig(
            environment=Environment.DEV,
            label="d",
            target_servers=[
                TargetServer(name="a.b", host="h", password=None),
            ],
        )
        dumped = scrub(cfg)
        assert dumped["target_servers"][0]["password"] is None

    def test_secret_field_paths_includes_target_servers(self):
        paths = EnvConfig.secret_field_paths()
        assert ("target_servers", "password") in paths


class TestTargetServerRoundtrip:
    def test_full_env_with_target_servers(self):
        cfg = EnvConfig(
            environment=Environment.STAGING,
            label="Staging",
            target_servers=[
                TargetServer(
                    name="web.stg.01",
                    host="10.0.1.1",
                    username="deploy",
                    password=SecretStr("stg-pwd"),
                ),
                TargetServer(
                    name="db.stg.01",
                    host="10.0.1.100",
                    port=3306,
                    protocol="mysql",
                    username="root",
                ),
            ],
        )
        dumped = scrub(cfg)
        # 重建
        cfg2 = EnvConfig.model_validate(dumped)
        # restore
        restored = restore_secrets(
            cfg2,
            {"target_servers.web.stg.01.password": "stg-pwd"},
        )
        # 第一个有密码
        assert restored.target_servers[0].password.get_secret_value() == "stg-pwd"
        # 第二个没密码
        assert restored.target_servers[1].password is None
