"""A.4 单测：storage（单文件持久化）。

覆盖：
    - config_dir / environments_file 跨平台路径
    - save_env → 单文件写入 + 占位符（无明文）
    - load_env → 反序列化得 EnvConfig（SecretStr 是占位符）
    - list_envs / get_active_env / set_active_env
    - delete_env：从 environments.json 移除条目
    - 一次性从老 layout（envs/*.json + index.json）迁移
"""
from __future__ import annotations

import os
import pytest
from pydantic import SecretStr

from agent.envconfig import (
    ApiGateway,
    DatabaseConnection,
    EnvConfig,
    EnvIndexEntry,
    Environment,
    config_dir,
    delete_env,
    environments_file,
    export_configs,
    get_active_env,
    import_configs,
    list_envs,
    load_env,
    save_env,
    set_active_env,
)


@pytest.fixture(autouse=True)
def _isolate_config_dir(monkeypatch, tmp_path):
    """每个测试用独立的 EAIDE_CONFIG_DIR，避免污染用户机器。"""
    cfg = tmp_path / "eaide-config"
    monkeypatch.setenv("EAIDE_CONFIG_DIR", str(cfg))
    # 兼容旧路径：清掉 EAIDE_DATA_DIR 防止测试意外读到老 layout
    monkeypatch.delenv("EAIDE_DATA_DIR", raising=False)
    return cfg


# ---- 路径解析 ----------------------------------------------------------


class TestPaths:
    def test_config_dir_uses_env_var(self, _isolate_config_dir):
        assert config_dir() == _isolate_config_dir

    def test_environments_file_path(self, _isolate_config_dir):
        p = environments_file()
        assert p.parent.exists()
        assert p.parent.is_dir()
        assert p.name == "environments.json"


# ---- 落盘 / 读回 --------------------------------------------------------


def _make_prod_config() -> EnvConfig:
    return EnvConfig(
        environment=Environment.PROD,
        label="生产",
        description="线上环境",
        databases=[
            DatabaseConnection(
                name="orders.pg",
                kind="postgres",
                host="db.prod.example.com",
                port=5432,
                database="orders",
                username="readonly",
                password=SecretStr("super-secret-prod-pwd"),
            ),
        ],
        api_gateways=[
            ApiGateway(
                name="internal.llm",
                base_url="http://172.1.0.134:8000/v1",
                api_key=SecretStr("sk-internal-xxx"),
            ),
        ],
    )


class TestSaveLoad:
    def test_save_does_not_contain_plaintext(self, _isolate_config_dir):
        cfg = _make_prod_config()
        save_env(cfg)
        raw = environments_file().read_text(encoding="utf-8")
        # **绝无明文** —— 安全红线
        assert "super-secret-prod-pwd" not in raw
        assert "sk-internal-xxx" not in raw
        # 但占位符必须在
        assert "__KEYRING_REF:" in raw

    def test_load_returns_placeholders(self, _isolate_config_dir):
        cfg = _make_prod_config()
        save_env(cfg)
        loaded = load_env(Environment.PROD)
        # 字段值是占位符
        pwd = loaded.databases[0].password.get_secret_value()
        assert pwd.startswith("__KEYRING_REF:")
        key = loaded.api_gateways[0].api_key.get_secret_value()
        assert key.startswith("__KEYRING_REF:")

    def test_load_missing_env_returns_seeded_default(self, _isolate_config_dir):
        # 单文件方案：首次启动会 seed 4 个 preset，所以 load_env(DEV) 不再抛
        # FileNotFoundError，而是返回 seed 的空配置。
        cfg = load_env(Environment.DEV)
        assert cfg.environment == Environment.DEV
        assert cfg.databases == []
        assert cfg.target_servers == []


    def test_load_real_missing_env_raises(self, _isolate_config_dir):
        # 把所有 seed 删光后再 load，应该抛 FileNotFoundError
        for e in (
            Environment.DEV,
            Environment.TEST,
            Environment.STAGING,
            Environment.PROD,
        ):
            delete_env(e)
        with pytest.raises(FileNotFoundError):
            load_env(Environment.DEV)


# ---- 注册表 + active -----------------------------------------------------


class TestActive:
    def test_save_updates_existing_entry(self, _isolate_config_dir):
        # 首次启动会 seed 4 个 preset；save_env 不再"添加"而是"覆盖"
        save_env(_make_prod_config())
        entries = list_envs()
        # 4 个 preset 都还在
        names = {e.environment for e in entries}
        assert names == {
            Environment.DEV,
            Environment.TEST,
            Environment.STAGING,
            Environment.PROD,
        }
        prod = next(e for e in entries if e.environment == Environment.PROD)
        assert prod.label == "生产"
        assert prod.active is True  # seed 默认 prod active

    def test_active_round_trip(self, _isolate_config_dir):
        save_env(_make_prod_config())
        set_active_env(Environment.PROD)
        active = get_active_env()
        assert active is not None
        assert active.environment == Environment.PROD
        # 切到 test，prod 不再 active
        save_env(
            EnvConfig(environment=Environment.TEST, label="测试")
        )
        set_active_env(Environment.TEST)
        active = get_active_env()
        assert active is not None
        assert active.environment == Environment.TEST
        all_entries = list_envs()
        prod_entry = next(e for e in all_entries if e.environment == Environment.PROD)
        assert prod_entry.active is False

    def test_set_active_env_creates_entry_if_absent(self, _isolate_config_dir):
        # 还没 save 过 prod，直接 set_active_env 应该建一个空的 config 条目
        set_active_env(Environment.PROD)
        entries = list_envs()
        assert any(e.environment == Environment.PROD and e.active for e in entries)


# ---- 删除 ----------------------------------------------------------------


class TestDelete:
    def test_delete_removes_entry(self, _isolate_config_dir):
        save_env(_make_prod_config())
        assert any(e.environment == Environment.PROD for e in list_envs())
        removed = delete_env(Environment.PROD)
        assert removed is True
        assert not any(
            e.environment == Environment.PROD for e in list_envs()
        )

    def test_delete_absent_returns_false(self, _isolate_config_dir):
        # 先把 seed 全删光
        for e in (
            Environment.DEV,
            Environment.TEST,
            Environment.STAGING,
            Environment.PROD,
        ):
            delete_env(e)
        assert delete_env(Environment.DEV) is False


# ---- 一次性迁移 ----------------------------------------------------------


class TestMigration:
    def test_migrates_legacy_layout(self, _isolate_config_dir, monkeypatch, tmp_path):
        """老 layout（envs/*.json + index.json）应在首次读取时自动迁移。"""
        legacy = tmp_path / "legacy-eaide"
        (legacy / "envs").mkdir(parents=True)
        # 写一个 env 文件（只覆盖 prod；seed 会负责 dev/test/staging）
        (legacy / "envs" / "prod.json").write_text(
            '{"environment": "prod", "label": "线上", "description": "老数据", '
            '"databases": [], "api_gateways": [], "mcp_servers": [], '
            '"target_servers": []}',
            encoding="utf-8",
        )
        # 写 index（active = test，dev 也注册了）
        (legacy / "index.json").write_text(
            '{"active": "test", "environments": ['
            '{"environment": "test", "label": "测试", "description": ""}, '
            '{"environment": "dev", "label": "开发", "description": ""}]}',
            encoding="utf-8",
        )
        # 让 storage 在 $APPDATA 之外找：直接给 EAIDE_DATA_DIR 指过去
        monkeypatch.setenv("EAIDE_DATA_DIR", str(legacy))

        # _read_environments 的入口才触发迁移 —— 这里手动调一次
        from agent.envconfig.storage import _read_environments
        _read_environments()

        envs = list_envs()
        names = {e.environment for e in envs}
        assert Environment.PROD in names
        # 老 index 里设的 active = test 应保留（seed 默认 active = prod，但迁移后被覆盖）
        active = get_active_env()
        assert active is not None
        assert active.environment == Environment.TEST
        # 老文件已挪走
        assert not (legacy / "envs" / "prod.json").exists()
        assert not (legacy / "index.json").exists()
        # 老 prod.json 的 label "线上" 应合并进来
        prod = next(e for e in envs if e.environment == Environment.PROD)
        assert prod.label == "线上"


# ---- export / import roundtrip -------------------------------------------


class TestExportImportRoundtrip:
    def test_roundtrip_with_passphrase(self, _isolate_config_dir):
        # 准备两个环境
        save_env(_make_prod_config())
        save_env(
            EnvConfig(
                environment=Environment.DEV,
                label="开发",
                databases=[
                    DatabaseConnection(
                        name="orders.pg",
                        kind="postgres",
                        host="localhost",
                        port=5432,
                        database="orders",
                        username="readonly",
                        password=SecretStr("dev-pwd"),
                    ),
                ],
            )
        )
        configs = [load_env(Environment.PROD), load_env(Environment.DEV)]
        out = _isolate_config_dir / "export.bin"
        res = export_configs(configs, out, passphrase="s3cret-pass")
        assert res.env_count == 2
        assert res.placeholder_count >= 3  # 2 password + 1 api_key

        # 1. 文件以 magic prefix 开头
        raw = out.read_bytes()
        assert raw.startswith(b"EAIDE-ENC-V1:")

        # 2. 用错 passphrase 应失败
        with pytest.raises(ValueError, match="passphrase 错误"):
            import_configs(out, passphrase="wrong")

        # 3. 用对 passphrase 能读回，且密钥仍是占位符
        imp = import_configs(out, passphrase="s3cret-pass")
        assert imp.env_count == 2
        assert imp.configs[0].databases[0].password.get_secret_value().startswith(
            "__KEYRING_REF:"
        )
        # 占位符账户列表完整
        assert any(
            "databases.orders.pg.password" in p for p in imp.placeholders
        )

    def test_export_blocks_plaintext_secret(self, _isolate_config_dir, monkeypatch):
        # 模拟有 bug 的 EnvConfig（密码字段存了明文，且我们绕过 scrub 的二次校验）
        # 这里我们直接拼一个含明文的 dict 喂给 export
        bad = {
            "schema_version": 1,
            "environments": [
                {
                    "environment": "prod",
                    "label": "x",
                    "config": {
                        "environment": "prod",
                        "label": "x",
                        "databases": [
                            {
                                "name": "a.b",
                                "kind": "pg",
                                "host": "h",
                                "port": 1,
                                "database": "d",
                                "username": "u",
                                "password": "PLAINTEXT-PWD-DETECT",  # 明文！
                            }
                        ],
                        "api_gateways": [],
                        "mcp_servers": [],
                    },
                }
            ],
        }
        cfg = EnvConfig.model_validate(bad["environments"][0]["config"])
        out = _isolate_config_dir / "exp.bin"
        export_configs([cfg], out, passphrase="pw")
        raw = out.read_bytes()
        assert b"PLAINTEXT-PWD-DETECT" not in raw

    def test_import_rejects_malformed_file(self, _isolate_config_dir):
        bad_file = _isolate_config_dir / "bad.bin"
        # _isolate_config_dir 自身由 fixture 创建；这里 .bin 是它的直接子项
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_bytes(b"not a real eaide file")
        with pytest.raises(ValueError):
            import_configs(bad_file, passphrase="anything", plaintext_ok=True)
