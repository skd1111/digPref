"""A.5 单测：FastAPI envconfig 路由。

覆盖：
    - GET / → 列表 + active
    - GET /{env} → 详情（SecretStr 占位符）
    - POST /{env} → 保存
    - POST /{env}/activate
    - DELETE /{env}
    - POST /export → 加密 blob
    - POST /import → 反序列化 + placeholders 列表
"""

from __future__ import annotations

import base64

import pytest
from agent.envconfig import (
    DatabaseConnection,
    EnvConfig,
    Environment,
    save_env,
)
from agent.main import create_app
from fastapi.testclient import TestClient
from pydantic import SecretStr


@pytest.fixture(autouse=True)
def _isolate_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("EAIDE_CONFIG_DIR", str(tmp_path / "eaide-config"))
    monkeypatch.delenv("EAIDE_DATA_DIR", raising=False)
    yield


@pytest.fixture
def client():
    app = create_app()
    return TestClient(app)


def _make_cfg(env: Environment, label: str, pwd: str = "secret") -> EnvConfig:
    return EnvConfig(
        environment=env,
        label=label,
        databases=[
            DatabaseConnection(
                name="orders.pg",
                kind="postgres",
                host="db",
                port=5432,
                database="d",
                username="u",
                password=SecretStr(pwd),
            ),
        ],
    )


# ---- 列表 / 详情 / 增删 ---------------------------------------------------


class TestList:
    def test_empty_list(self, client):
        r = client.get("/envconfig/")
        assert r.status_code == 200
        body = r.json()
        # 首次启动会自动 seed 4 个 preset
        assert body["active"] == "prod"
        envs = {e["environment"] for e in body["environments"]}
        assert envs == {"dev", "test", "staging", "prod"}

    def test_after_save_shows_in_list(self, client):
        save_env(_make_cfg(Environment.DEV, "开发定制", pwd="new"))
        r = client.get("/envconfig/").json()
        dev_entry = next(e for e in r["environments"] if e["environment"] == "dev")
        assert dev_entry["label"] == "开发定制"
        assert dev_entry["configured"] is True


class TestGet:
    def test_get_returns_placeholders(self, client):
        save_env(_make_cfg(Environment.PROD, "生产", pwd="real-pwd"))
        r = client.get("/envconfig/prod")
        assert r.status_code == 200
        body = r.json()
        pwd = body["databases"][0]["password"]
        # 关键安全断言：返回值不含明文
        assert "real-pwd" not in str(body)
        # 是个占位符或空（model_dump json 模式 → "**********"）
        assert pwd is None or pwd.startswith("__KEYRING_REF:") or pwd == "**********"

    def test_get_arbitrary_name_returns_empty_default(self, client):
        # 旧设计：未注册的 env 名 → 404
        # 新设计：任意合法格式的 env 名都可访问，未配置则返空配置
        r = client.get("/envconfig/custom_env")
        assert r.status_code == 200
        body = r.json()
        assert body["environment"] == "custom_env"
        assert body["databases"] == []
        assert body["target_servers"] == []

    def test_get_invalid_env_format_400(self, client):
        # 真正非法的格式：含空格 / 以数字开头 / 以 - 开头。
        # 注：大小写不限制（unicode 友好），所以 "BadName" 是合法的。
        r = client.get("/envconfig/with%20space")
        assert r.status_code == 400
        r = client.get("/envconfig/1abc")  # 数字开头
        assert r.status_code == 400
        r = client.get("/envconfig/-foo")  # 符号开头
        assert r.status_code == 400


class TestSave:
    def test_save_round_trip(self, client):
        body = {
            "environment": "dev",
            "label": "开发",
            "databases": [
                {
                    "name": "a.b",
                    "kind": "pg",
                    "host": "h",
                    "port": 1,
                    "database": "d",
                    "username": "u",
                    "password": "should-be-scrubbed",
                }
            ],
            "api_gateways": [],
            "mcp_servers": [],
        }
        r = client.post("/envconfig/dev", json=body)
        assert r.status_code == 200
        # 重新拉 —— password 字段已经 scrub 成占位符
        r2 = client.get("/envconfig/dev").json()
        pwd = r2["databases"][0]["password"]
        assert pwd is None or pwd != "should-be-scrubbed"

    def test_save_with_mismatched_env_400(self, client):
        r = client.post(
            "/envconfig/dev",
            json={
                "environment": "prod",  # 与 URL 不一致
                "label": "x",
                "databases": [],
                "api_gateways": [],
                "mcp_servers": [],
            },
        )
        assert r.status_code == 400

    def test_save_arbitrary_new_env(self, client):
        # 2026-07-09：自由命名。用户可输入任意 ^[a-z][a-z0-9._-]{0,62}$ 的
        # 名字，应能直接创建一个新 env 条目（不替换任何现存）。
        body = {
            "environment": "华东-dev",
            "label": "华东开发环境",
            "databases": [],
            "api_gateways": [],
            "mcp_servers": [],
            "target_servers": [
                {
                    "name": "web.app.01",
                    "description": "Web 主机 1",
                    "host": "10.0.0.1",
                    "port": 22,
                    "protocol": "ssh",
                    "username": "root",
                    "tags": [],
                    "enabled": True,
                }
            ],
        }
        r = client.post("/envconfig/华东-dev", json=body)
        assert r.status_code == 200
        # 出现在列表里
        body_list = client.get("/envconfig/").json()
        names = {e["environment"] for e in body_list["environments"]}
        assert "华东-dev" in names
        # 拉出来验证一致
        got = client.get("/envconfig/华东-dev").json()
        assert got["label"] == "华东开发环境"
        assert got["target_servers"][0]["host"] == "10.0.0.1"

    def test_save_invalid_name_400(self, client):
        # URL 里塞非法名字 → 立即 400
        r = client.post(
            "/envconfig/1abc",
            json={
                "environment": "1abc",
                "label": "x",
            },
        )
        assert r.status_code == 400


class TestActivate:
    def test_activate(self, client):
        save_env(_make_cfg(Environment.DEV, "dev"))
        save_env(_make_cfg(Environment.PROD, "prod"))
        r = client.post("/envconfig/dev/activate")
        assert r.status_code == 200
        assert r.json()["active"] == "dev"
        assert client.get("/envconfig/").json()["active"] == "dev"


class TestDelete:
    def test_delete(self, client):
        save_env(_make_cfg(Environment.DEV, "dev"))
        r = client.delete("/envconfig/dev")
        assert r.status_code == 200
        # seed 4 个 preset，删掉 dev 后剩 3 个
        body = client.get("/envconfig/").json()
        assert all(e["environment"] != "dev" for e in body["environments"])
        assert len(body["environments"]) == 3


# ---- export / import ------------------------------------------------------


class TestExportImport:
    def test_export_returns_ciphertext(self, client):
        save_env(_make_cfg(Environment.PROD, "prod", pwd="real-prod-pwd"))
        r = client.post(
            "/envconfig/export",
            json={
                "passphrase": "secret-pw",
                "environments": ["prod"],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["env_count"] == 1
        assert body["placeholder_count"] >= 1
        # 密文 base64 不能含明文
        assert "real-prod-pwd" not in body["ciphertext_base64"]
        # 必须能解出 magic prefix
        blob = base64.b64decode(body["ciphertext_base64"])
        assert blob.startswith(b"EAIDE-ENC-V1:")

    def test_export_empty_environments_400(self, client):
        r = client.post(
            "/envconfig/export",
            json={
                "passphrase": "x",
                "environments": [],
            },
        )
        assert r.status_code == 400

    def test_import_with_wrong_passphrase_400(self, client):
        save_env(_make_cfg(Environment.PROD, "prod"))
        ex = client.post(
            "/envconfig/export",
            json={
                "passphrase": "right",
                "environments": ["prod"],
            },
        ).json()
        r = client.post(
            "/envconfig/import",
            json={
                "passphrase": "wrong",
                "ciphertext_base64": ex["ciphertext_base64"],
            },
        )
        assert r.status_code == 400

    def test_import_round_trip_returns_placeholders(self, client):
        save_env(_make_cfg(Environment.PROD, "prod", pwd="real-pwd"))
        ex = client.post(
            "/envconfig/export",
            json={
                "passphrase": "secret",
                "environments": ["prod"],
            },
        ).json()
        r = client.post(
            "/envconfig/import",
            json={
                "passphrase": "secret",
                "ciphertext_base64": ex["ciphertext_base64"],
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["env_count"] == 1
        assert any("databases.orders.pg.password" in p for p in body["placeholders"])
        # 导入结果仍是占位符（绝无明文）
        assert "real-pwd" not in str(body)
