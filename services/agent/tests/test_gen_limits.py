"""生成限制两级回退（gen_limits）—— 存储 / 端点 / 客户端接线回归。

覆盖：
  - load/save 默认值与持久化往返（llm_kv.gen_limits）
  - 校验：越界 / 未知字段 / 非整数一律拒绝
  - GET/PUT /router/gen-limits 端点（PUT 落库 + GET 回读）
  - OllamaClient：max_output_tokens → options.num_predict（调用点显式值取较小）
  - PrivateLLMClient：max_output_tokens → payload.max_tokens
  - 行级回退：后端 max_context 为 NULL 时用 default_context_window 补位
"""

from __future__ import annotations

import json
import sqlite3

import httpx
import pytest
from agent.config import settings
from agent.llm import engine_api, gen_limits
from agent.llm.ollama import OllamaClient
from agent.llm.private_llm import PrivateLLMClient
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """构造 TestClient + 隔离 router.db（与 test_router_v2_weights 同模式）。"""
    monkeypatch.setenv("EAIDE_LLM_ROUTER_DB_PATH", str(tmp_path / "router.db"))
    monkeypatch.setattr(settings, "llm_router_db_path", str(tmp_path / "router.db"))
    engine_api._ENGINE = None
    app = FastAPI()
    app.include_router(engine_api.router)
    return TestClient(app)


# ---- 模块层：默认值 / 持久化 / 校验 -----------------------------------------


def test_load_defaults_when_no_db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "llm_router_db_path", str(tmp_path / "router.db"))
    limits = gen_limits.load_gen_limits()
    assert limits == gen_limits.DEFAULT_GEN_LIMITS


def test_save_load_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "llm_router_db_path", str(tmp_path / "router.db"))
    merged = gen_limits.save_gen_limits({"max_output_tokens": 8192})
    assert merged["max_output_tokens"] == 8192
    # 未传字段保留原值
    assert (
        merged["default_context_window"] == gen_limits.DEFAULT_GEN_LIMITS["default_context_window"]
    )
    assert gen_limits.load_gen_limits() == merged


def test_save_rejects_out_of_range(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "llm_router_db_path", str(tmp_path / "router.db"))
    with pytest.raises(ValueError):
        gen_limits.save_gen_limits({"max_output_tokens": 0})
    with pytest.raises(ValueError):
        gen_limits.save_gen_limits({"default_context_window": 10})  # < 1024 下限
    with pytest.raises(ValueError):
        gen_limits.save_gen_limits({"max_output_tokens": 10_000_001})


def test_save_rejects_unknown_field_and_non_int(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "llm_router_db_path", str(tmp_path / "router.db"))
    with pytest.raises(ValueError):
        gen_limits.save_gen_limits({"output_language": "zh"})
    with pytest.raises(ValueError):
        gen_limits.save_gen_limits({"max_output_tokens": True})  # bool 不是合法 int


# ---- 端点层 -----------------------------------------------------------------


def test_get_gen_limits_defaults(client):
    r = client.get("/router/gen-limits")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["limits"] == gen_limits.DEFAULT_GEN_LIMITS


def test_put_gen_limits_persists_and_reads_back(client, tmp_path):
    r = client.put("/router/gen-limits", json={"max_output_tokens": 4096})
    assert r.status_code == 200, r.text
    assert r.json()["limits"]["max_output_tokens"] == 4096

    # 落库：llm_kv.gen_limits 行存在
    conn = sqlite3.connect(str(tmp_path / "router.db"))
    try:
        row = conn.execute("SELECT value FROM llm_kv WHERE key='gen_limits'").fetchone()
    finally:
        conn.close()
    assert row is not None and "4096" in row[0]

    # 回读
    r2 = client.get("/router/gen-limits")
    assert r2.json()["limits"]["max_output_tokens"] == 4096


def test_put_gen_limits_validation_422(client):
    r = client.put("/router/gen-limits", json={"max_output_tokens": -1})
    assert r.status_code == 422
    r2 = client.put("/router/gen-limits", json={"default_context_window": 5})
    assert r2.status_code == 422


# ---- 客户端接线：Ollama num_predict -----------------------------------------


class _CapturingTransport(httpx.MockTransport):
    """捕获最后一次请求 JSON 的 MockTransport。"""

    def __init__(self, response_json: dict):
        self.captured: dict | None = None

        def handler(request: httpx.Request) -> httpx.Response:
            self.captured = json.loads(request.content) if request.content else None
            return httpx.Response(200, json=response_json)

        super().__init__(handler)


@pytest.mark.asyncio
async def test_ollama_chat_injects_num_predict(monkeypatch):
    """max_output_tokens → options.num_predict；调用点显式传值时取较小者。"""
    transport = _CapturingTransport(
        {"message": {"role": "assistant", "content": "hi"}, "eval_count": 1, "prompt_eval_count": 1}
    )
    _orig_client = httpx.AsyncClient  # 先捕获原始类，避免 patch 后自引用
    monkeypatch.setattr(
        "agent.llm.ollama.httpx.AsyncClient",
        lambda timeout=None: _orig_client(transport=transport),
    )
    c = OllamaClient(base_url="http://127.0.0.1:11434", model="m", max_output_tokens=100)
    await c._chat([{"role": "user", "content": "x"}])
    assert transport.captured is not None
    assert transport.captured["options"]["num_predict"] == 100

    # 调用点显式传了更小的 num_predict → 保持更小值
    await c._chat([{"role": "user", "content": "x"}], options={"num_predict": 30})
    assert transport.captured["options"]["num_predict"] == 30

    # 未配置 cap → 不注入 num_predict
    c2 = OllamaClient(base_url="http://127.0.0.1:11434", model="m")
    await c2._chat([{"role": "user", "content": "x"}])
    assert "num_predict" not in (transport.captured.get("options") or {})


# ---- 客户端接线：PrivateLLM max_tokens --------------------------------------


@pytest.mark.asyncio
async def test_private_chat_injects_max_tokens():
    """max_output_tokens → payload.max_tokens；未配置时不带该字段。"""
    transport = _CapturingTransport(
        {"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}}
    )
    c = PrivateLLMClient(base_url="http://x/v1", api_key="k", model="m", max_output_tokens=2048)
    c._client = httpx.AsyncClient(transport=transport)
    await c._chat_completion([{"role": "user", "content": "x"}])
    assert transport.captured is not None
    assert transport.captured["max_tokens"] == 2048

    c2 = PrivateLLMClient(base_url="http://x/v1", api_key="k", model="m")
    c2._client = httpx.AsyncClient(transport=transport)
    await c2._chat_completion([{"role": "user", "content": "x"}])
    assert "max_tokens" not in transport.captured


# ---- 行级回退：max_context NULL → default_context_window ---------------------


async def test_row_fallback_uses_default_context_window(tmp_path, monkeypatch):
    """llm_backends.max_context 为 NULL 时 _row_to_backend 用全局默认补位。"""
    monkeypatch.setattr(settings, "llm_router_db_path", str(tmp_path / "router.db"))
    from agent.llm import storage

    # 手工插一行 max_context=NULL（绕过 upsert 的必填路径）
    conn = sqlite3.connect(str(tmp_path / "router.db"))
    try:
        conn.executescript((storage._SCHEMA_PATH).read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO llm_backends (name, type, base_url, model_name, max_context) "
            "VALUES ('null-ctx', 'local', 'http://x', 'm', NULL)"
        )
        conn.commit()
    finally:
        conn.close()

    backends = await storage.list_backends()
    target = next(b for b in backends if b.name == "null-ctx")
    assert target.max_context == gen_limits.DEFAULT_GEN_LIMITS["default_context_window"]


def test_load_max_context_null_row_falls_back(tmp_path, monkeypatch):
    """_load_max_context_from_db：行存在但 max_context NULL → 全局默认回退。"""
    monkeypatch.setattr(settings, "llm_router_db_path", str(tmp_path / "router.db"))
    from agent.llm import storage
    from agent.llm.router import _load_max_context_from_db

    conn = sqlite3.connect(str(tmp_path / "router.db"))
    try:
        conn.executescript((storage._SCHEMA_PATH).read_text(encoding="utf-8"))
        conn.execute(
            "INSERT INTO llm_backends (name, type, base_url, model_name, max_context, enabled) "
            "VALUES (?, 'local', 'http://x', ?, NULL, 1)",
            ("local-null", settings.ollama_model),
        )
        conn.commit()
    finally:
        conn.close()

    ollama_ctx, private_ctx = _load_max_context_from_db()
    assert ollama_ctx == gen_limits.DEFAULT_GEN_LIMITS["default_context_window"]
    assert private_ctx is None
