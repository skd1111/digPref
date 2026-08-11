"""test_biznav_api.py —— FastAPI 路由测试（Phase 2G V1.1）。

策略：把 biznav_api.router 临时挂到一个 fresh FastAPI 子 app 上，
避免触发 agent.main.create_app() 的 lifespan + 整个 MCP / LLM 启动序列。
所有调用走 TestClient（sync）。

测试矩阵：
- test_extract_returns_job_id          （POST /biznav/extract 起异步任务）
- test_list_features_filter_by_category
- test_update_feature_validates_version
- test_delete_feature
- test_export_yaml + import_yaml
- test_status_endpoint_empty
- test_affected_returns_empty_v11
- test_reload_returns_503_v11
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def biznav_app(tmp_path, monkeypatch):
    """挂载 biznav_api.router 到 fresh FastAPI 子 app，所有 storage 走 tmp_path。"""
    db = str(tmp_path / "biznav.db")
    audit_db = str(tmp_path / "audit.sqlite")
    monkeypatch.setenv("EAIDE_BIZNAV_DB", db)
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", audit_db)

    # 重置 storage 单例
    from agent.biznav import api as biznav_api

    biznav_api._reset_storage_for_tests()

    app = FastAPI()
    app.include_router(biznav_api.router)
    return app, db


@pytest.fixture
def client(biznav_app):
    app, db = biznav_app
    return TestClient(app), db


# ---------------------------------------------------------------------------
# 工具：构造 feature
# ---------------------------------------------------------------------------


def _seed_feature(storage, feature_id: str = "a", **kw):
    from agent.biznav.models import Feature, RelatedFile

    base = dict(
        id=feature_id,
        name="订单管理",
        description="订单 CRUD",
        category="业务",
        project_name="demo",
        project_root="/tmp/demo",
        related_files=[RelatedFile(path="src/order/X.java")],
        source="ai",
        version=1,
    )
    base.update(kw)
    storage.upsert(Feature(**base))


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


def test_extract_returns_job_id(client, tmp_path):
    c, db = client
    # 准备一个项目根目录
    root = tmp_path / "proj"
    root.mkdir()
    (root / "x.java").write_text("class X {}", encoding="utf-8")
    resp = c.post(
        "/biznav/extract",
        json={"project_name": "demo", "project_root": str(root)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "job_id" in body
    assert body["project_name"] == "demo"
    assert body["status"] == "pending"
    # job 应该已经写入 extraction_jobs
    import sqlite3

    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT id, status FROM extraction_jobs").fetchall()
    assert len(rows) == 1
    assert rows[0][1] in ("pending", "scanning", "extracting", "done", "failed")


def test_list_features_filter_by_category(client):
    c, db = client
    from agent.biznav import api as biznav_api

    storage = biznav_api._get_storage(db)
    _seed_feature(storage, "a", category="业务")
    _seed_feature(storage, "b", category="路由")
    resp = c.get("/biznav/features", params={"project_name": "demo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert {f["id"] for f in body["features"]} == {"a", "b"}

    resp2 = c.get(
        "/biznav/features",
        params={"project_name": "demo", "category": "业务"},
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["total"] == 1
    assert body2["features"][0]["id"] == "a"


def test_update_feature_validates_version(client):
    c, db = client
    from agent.biznav import api as biznav_api

    storage = biznav_api._get_storage(db)
    _seed_feature(storage, "a")

    # 正确版本 → 成功
    resp = c.put(
        "/biznav/features/a",
        json={
            "project_name": "demo",
            "expected_version": 1,
            "name": "新名",
            "category": "业务更新",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "新名"
    assert body["version"] == 2
    assert body["source"] == "manual"  # PUT 后默认 manual

    # 用旧版本 → 409
    resp2 = c.put(
        "/biznav/features/a",
        json={
            "project_name": "demo",
            "expected_version": 1,
            "name": "再改",
        },
    )
    assert resp2.status_code == 409


def test_delete_feature(client):
    c, db = client
    from agent.biznav import api as biznav_api

    storage = biznav_api._get_storage(db)
    _seed_feature(storage, "a")
    # 软删除
    resp = c.delete("/biznav/features/a", params={"project_name": "demo"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["hard"] is False
    # 列表应不包含
    listed = storage.list_by_project("demo")
    assert listed == []

    # 硬删除
    _seed_feature(storage, "b")
    resp2 = c.delete(
        "/biznav/features/b",
        params={"project_name": "demo", "hard": "true"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["hard"] is True


def test_export_and_import(client):
    c, db = client
    from agent.biznav import api as biznav_api

    storage = biznav_api._get_storage(db)
    _seed_feature(storage, "a")
    _seed_feature(storage, "b", category="路由")

    # export YAML
    resp = c.get(
        "/biznav/export",
        params={"project_name": "demo", "project_root": "/tmp/demo", "format": "yaml"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["format"] == "yaml"
    text = body["body"]
    assert "订单管理" in text

    # export JSON
    resp2 = c.get(
        "/biznav/export",
        params={"project_name": "demo", "format": "json"},
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["format"] == "json"
    import json

    items = json.loads(body2["body"])
    assert len(items) == 2

    # import：清空，再灌入 YAML（合并）
    storage.delete("a", "demo")
    storage.delete("b", "demo")
    resp3 = c.post(
        "/biznav/import",
        json={"project_name": "demo", "yaml_text": text, "merge": True},
    )
    assert resp3.status_code == 200
    rep = resp3.json()
    assert rep["ok"] is True
    assert rep["inserted"] == 2
    again = storage.list_by_project("demo")
    assert {f.id for f in again} == {"a", "b"}


def test_status_endpoint_empty(client):
    c, _db = client
    resp = c.get("/biznav/status", params={"project_name": "nope"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_job"] is False
    assert body["project_name"] == "nope"


def test_affected_returns_empty_v11(client):
    c, _db = client
    resp = c.get("/biznav/affected", params={"project_name": "demo"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["affected"] == []


def test_reload_returns_503_v11(client):
    c, _db = client
    resp = c.post("/biznav/reload", params={"project_name": "demo"})
    assert resp.status_code == 503
    assert "V1.1" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# _make_llm_client 逐级降级链（V1.4：本地 → 内网 → 云端）
# ---------------------------------------------------------------------------


class _FakeBackend:
    """mock extract_chat 后端：可配置抛异常 / 返回空 / 返回文本。"""

    def __init__(self, text: str = "ok", raises: Exception | None = None):
        self._text = text
        self._raises = raises
        self.calls = 0

    async def extract_chat(self, messages):
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        return self._text


class _FakeRouter:
    def __init__(self, ollama=None, private=None, cloud=None):
        self._mock_mode = False
        self.ollama = ollama
        self.private = private
        self._cloud = cloud

    async def _build_cloud_client(self):
        return self._cloud

    async def _build_private_client(self):
        # V1.6：内网与云端同层，都从注册表构造；测试里直接返回注入的 fake
        return self.private


@pytest.mark.asyncio
async def test_llm_client_local_first(monkeypatch):
    """本地可用 → 直接用本地，不碰内网/云端。"""
    import agent.llm.router as router_mod
    from agent.biznav import api as biznav_api

    local = _FakeBackend(text="feature-json")
    private = _FakeBackend(text="should-not-use")
    monkeypatch.setattr(router_mod, "LMRouter", lambda: _FakeRouter(ollama=local, private=private))

    client = biznav_api._make_llm_client()
    out = await client("biznav_extract", [{"role": "user", "content": "p"}])
    assert out == "feature-json"
    assert local.calls == 1
    assert private.calls == 0


@pytest.mark.asyncio
async def test_llm_client_fallback_to_private(monkeypatch):
    """本地不可用（抛异常）→ 降级内网。"""
    import agent.llm.router as router_mod
    from agent.biznav import api as biznav_api

    private = _FakeBackend(text="private-json")
    monkeypatch.setattr(
        router_mod,
        "LMRouter",
        lambda: _FakeRouter(
            ollama=_FakeBackend(raises=RuntimeError("ollama down")), private=private
        ),
    )

    client = biznav_api._make_llm_client()
    out = await client("biznav_extract", [{"role": "user", "content": "p"}])
    assert out == "private-json"
    assert private.calls == 1


@pytest.mark.asyncio
async def test_llm_client_fallback_to_cloud(monkeypatch):
    """本地不可用 + 内网未配置 → 降级云端。"""
    import agent.llm.router as router_mod
    from agent.biznav import api as biznav_api

    cloud = _FakeBackend(text="cloud-json")
    monkeypatch.setattr(
        router_mod,
        "LMRouter",
        lambda: _FakeRouter(
            ollama=_FakeBackend(raises=RuntimeError("ollama down")),
            private=None,
            cloud=cloud,
        ),
    )

    client = biznav_api._make_llm_client()
    out = await client("biznav_extract", [{"role": "user", "content": "p"}])
    assert out == "cloud-json"
    assert cloud.calls == 1


@pytest.mark.asyncio
async def test_llm_client_empty_local_falls_through(monkeypatch):
    """本地返回空文本也视为不可用 → 逐级降级。"""
    import agent.llm.router as router_mod
    from agent.biznav import api as biznav_api

    private = _FakeBackend(text="")  # 内网也返回空
    cloud = _FakeBackend(text="cloud-json")
    monkeypatch.setattr(
        router_mod,
        "LMRouter",
        lambda: _FakeRouter(ollama=_FakeBackend(text=""), private=private, cloud=cloud),
    )

    client = biznav_api._make_llm_client()
    out = await client("biznav_extract", [{"role": "user", "content": "p"}])
    assert out == "cloud-json"


@pytest.mark.asyncio
async def test_llm_client_all_fail_raises(monkeypatch):
    """三级全失败 → 抛 RuntimeError（extractor 记录到 job.error_message 并标 failed）。"""
    import agent.llm.router as router_mod
    from agent.biznav import api as biznav_api

    monkeypatch.setattr(
        router_mod,
        "LMRouter",
        lambda: _FakeRouter(
            ollama=_FakeBackend(raises=RuntimeError("down")),
            private=_FakeBackend(raises=RuntimeError("down")),
            cloud=None,
        ),
    )

    client = biznav_api._make_llm_client()
    with pytest.raises(RuntimeError, match="所有 LLM 后端均不可用"):
        await client("biznav_extract", [{"role": "user", "content": "p"}])


@pytest.mark.asyncio
async def test_build_private_client_from_registry(monkeypatch):
    """V1.6：内网后端从 router.db 注册表取（type=private 且启用），
    不读 settings/环境变量；base_url 带 /chat/completions 后缀时自动剥离。"""
    import agent.llm.router as router_mod
    from agent.llm.models import LLMBackend

    backend = LLMBackend(
        name="intranet",
        type="private",
        base_url="http://172.1.0.134:8000/v1/chat/completions",
        model_name="DeepSeek-RD-Llama-70B-Int8",
        api_key_ref="",
        data_residency="private",
        enabled=True,
    )

    async def fake_list_backends(*, enabled_only: bool = False):
        return [backend] if not enabled_only or backend.enabled else []

    monkeypatch.setattr("agent.llm.storage.list_backends", fake_list_backends)

    router = router_mod.LMRouter()
    client = await router._build_private_client()
    assert client is not None
    assert client.base_url == "http://172.1.0.134:8000/v1"
    assert client.model == "DeepSeek-RD-Llama-70B-Int8"


@pytest.mark.asyncio
async def test_build_private_client_none_when_disabled(monkeypatch):
    """V1.6：注册表里没有启用的 private 后端 → 返回 None（降级链走云端）。"""
    import agent.llm.router as router_mod

    async def fake_list_backends(*, enabled_only: bool = False):
        return []

    monkeypatch.setattr("agent.llm.storage.list_backends", fake_list_backends)

    router = router_mod.LMRouter()
    assert await router._build_private_client() is None


def test_parse_llm_json_fenced_array():
    """biznav 提取输出围栏/前缀容忍（spec §4.5 第三层）。"""
    from agent.biznav.extractor import FeatureExtractor

    raw = '好的：\n```json\n[{"id": "x-1", "name": "n", "description": "d", "category": "c"}]\n```'
    assert FeatureExtractor._parse_llm_json(raw) == [
        {"id": "x-1", "name": "n", "description": "d", "category": "c"}
    ]
