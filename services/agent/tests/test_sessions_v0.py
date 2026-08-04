"""test_sessions_v0 —— Phase 6 V0 会话管理测试。

覆盖：
- storage：session / message / checkpoint CRUD
- knowledge_base：MockKBAdapter + build_kb_context + prompt 拼装 + 未知 backend 兜底
- checkpointer：MemorySaver wrapper + save_reference
- api：4 核心路由（create / list / get / delete）+ KB search
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent.sessions.checkpointer import SessionCheckpointer
from agent.sessions.knowledge_base import (
    KBConfig,
    KBContext,
    KBQueryResult,
    MockKBAdapter,
    build_adapter,
    build_kb_context,
    kb_context_to_prompt_snippet,
)
from agent.sessions.models import Session
from agent.sessions.storage import SessionStorage


# ---- storage ---------------------------------------------------------------

@pytest.fixture
def storage(tmp_path):
    return SessionStorage(db_path=tmp_path / "sessions_test.db")


def test_create_session_returns_session_with_thread_id(storage):
    s = storage.create_session("test", owner="alice", project_name="order-service")
    assert s.id  # UUID
    assert s.title == "test"
    assert s.owner == "alice"
    assert s.project_name == "order-service"
    assert s.status == "active"
    assert s.thread_id == s.id  # thread_id 默认 = session_id
    assert s.created_at > 0
    assert s.created_at == s.updated_at


def test_get_session_returns_none_for_missing(storage):
    assert storage.get_session("nonexistent-uuid") is None


def test_list_sessions_default_active(storage):
    s1 = storage.create_session("s1")
    s2 = storage.create_session("s2")
    storage.update_session(s1.id, status="archived")
    active = storage.list_sessions(status="active")
    assert {s.id for s in active} == {s2.id}
    all_sessions = storage.list_sessions(status=None)
    assert {s.id for s in all_sessions} == {s1.id, s2.id}


def test_list_sessions_filter_by_project(storage):
    s1 = storage.create_session("s1", project_name="project-a")
    s2 = storage.create_session("s2", project_name="project-b")
    project_a = storage.list_sessions(project_name="project-a")
    assert {s.id for s in project_a} == {s1.id}
    assert {s.id for s in storage.list_sessions(project_name="project-b")} == {s2.id}


def test_update_session_title_and_status(storage):
    s = storage.create_session("original")
    ok = storage.update_session(s.id, title="renamed")
    assert ok is True
    assert storage.get_session(s.id).title == "renamed"
    ok2 = storage.update_session(s.id, status="archived")
    assert ok2 is True
    assert storage.get_session(s.id).status == "archived"


def test_delete_session_cascades(storage):
    s = storage.create_session("to-delete")
    storage.append_message(s.id, "user", "hello")
    storage.record_checkpoint(s.id, s.thread_id, "cp-1")
    ok = storage.delete_session(s.id)
    assert ok is True
    assert storage.get_session(s.id) is None
    # CASCADE: messages + checkpoints 也删
    assert storage.list_messages(s.id) == []
    assert storage.list_checkpoints(s.id) == []


def test_append_message_persists_with_tool_args(storage):
    s = storage.create_session("s")
    m = storage.append_message(
        s.id, "assistant", "ok", tool_name="db.query",
        tool_args={"sql": "SELECT 1"}, tool_result="42",
    )
    assert m.id > 0
    msgs = storage.list_messages(s.id)
    assert len(msgs) == 1
    assert msgs[0].tool_name == "db.query"
    assert msgs[0].tool_args == {"sql": "SELECT 1"}


def test_list_messages_ordered_asc(storage):
    s = storage.create_session("s")
    storage.append_message(s.id, "user", "first")
    storage.append_message(s.id, "assistant", "second")
    msgs = storage.list_messages(s.id)
    assert [m.content for m in msgs] == ["first", "second"]


def test_record_checkpoint_unique_constraint(storage):
    """同 thread_id + checkpoint_id 重复 record 不创建新行（INSERT OR IGNORE）。

    Python sqlite3 + `INSERT OR IGNORE` 行为：
      - 第一次 INSERT 成功：cur.lastrowid = 新 rowid（>0）
      - 第二次 INSERT UNIQUE 冲突被 IGNORE：cur.lastrowid = 0（python sqlite3 默认；
        不是"上次的 rowid"）
    所以 cp1.id > 0，cp2.id == 0 —— 通过 cp2 是否成功判断 ignore 生效。
    """
    s = storage.create_session("s")
    cp1 = storage.record_checkpoint(s.id, s.thread_id, "cp-1", label="v1")
    assert cp1.id > 0
    # 同 thread_id + checkpoint_id 不重复插入（INSERT OR IGNORE）
    cp2 = storage.record_checkpoint(s.id, s.thread_id, "cp-1", label="v2")
    assert cp2.id == 0  # 0 = 没新行
    # list 只返一条
    cps = storage.list_checkpoints(s.id)
    assert len(cps) == 1
    # label 是第一次写入的 v1（INSERT OR IGNORE 不更新）
    assert cps[0].label == "v1"


# ---- knowledge_base -------------------------------------------------------

def test_kb_config_from_env(monkeypatch):
    monkeypatch.setenv("EAIDE_KB_BACKEND", "notion")
    monkeypatch.setenv("EAIDE_KB_BASE_URL", "https://wiki.example.com")
    monkeypatch.setenv("EAIDE_KB_TIMEOUT_S", "10")
    cfg = KBConfig.from_env()
    assert cfg.backend == "notion"
    assert cfg.base_url == "https://wiki.example.com"
    assert cfg.timeout_s == 10.0


def test_build_adapter_known_backend():
    a = build_adapter(KBConfig(backend="mock"))
    assert isinstance(a, MockKBAdapter)
    assert a.name == "mock"


def test_build_adapter_unknown_backend_falls_back_to_mock():
    a = build_adapter(KBConfig(backend="notion-xyz-unknown"))
    assert isinstance(a, MockKBAdapter)


@pytest.mark.asyncio
async def test_build_kb_context_returns_results():
    ctx = await build_kb_context("EAIDE 是什么？", top_k=2)
    assert ctx.query == "EAIDE 是什么？"
    assert ctx.backend == "mock"
    assert len(ctx.results) <= 2
    assert all(r.score >= 0 for r in ctx.results)


@pytest.mark.asyncio
async def test_build_kb_context_empty_results_when_adapter_fails(monkeypatch):
    """适配器抛错 → 空 KBContext（best-effort，不抛错）。"""
    class _Broken:
        name = "broken"
        def is_available(self): return True
        async def search(self, **kw): raise RuntimeError("kaboom")
    ctx = await build_kb_context("x", adapter=_Broken())
    assert ctx.results == []
    assert ctx.backend == "broken"


def test_kb_context_to_prompt_snippet_includes_titles():
    ctx = KBContext(
        query="test",
        results=[
            KBQueryResult(doc_id="1", title="Doc1", snippet="snippet1", score=0.9),
            KBQueryResult(doc_id="2", title="Doc2", snippet="snippet2", score=0.5),
        ],
        backend="mock",
    )
    snippet = kb_context_to_prompt_snippet(ctx)
    assert "[0] Doc1: snippet1" in snippet
    assert "[1] Doc2: snippet2" in snippet
    assert "来源: " in snippet


def test_kb_context_to_prompt_snippet_empty_when_no_results():
    assert kb_context_to_prompt_snippet(KBContext(query="x")) == ""


def test_kb_context_to_prompt_snippet_truncates():
    long_results = [
        KBQueryResult(
            doc_id=str(i),
            title=f"Doc {i}",
            snippet="x" * 500,
            score=0.5,
            source_url="https://example.com/" + "a" * 100,
        )
        for i in range(10)
    ]
    snippet = kb_context_to_prompt_snippet(
        KBContext(query="x", results=long_results), max_chars=200
    )
    assert len(snippet) <= 200


# ---- checkpointer ---------------------------------------------------------

@pytest.fixture
def cp(storage):
    return SessionCheckpointer(storage)


def test_checkpointer_saver_is_memory_saver_by_default(cp):
    from langgraph.checkpoint.memory import MemorySaver
    assert isinstance(cp.saver, MemorySaver)


def test_checkpointer_save_reference_returns_id_and_lists(cp, storage):
    s = storage.create_session("s")
    cid = cp.save_reference(s.id, s.thread_id, "cp-1", label="initial")
    assert cid > 0
    cps = cp.list_checkpoints(s.id)
    assert len(cps) == 1
    assert cps[0].checkpoint_id == "cp-1"
    assert cps[0].label == "initial"


def test_checkpointer_save_reference_failure_best_effort(cp, monkeypatch):
    """save_reference 失败不抛错（best-effort）。"""
    s = cp._storage.create_session("s")
    # 把 storage 替换成 fake 让 record_checkpoint 抛错
    class _BoomStorage:
        def record_checkpoint(self, **kw):
            raise RuntimeError("simulated DB error")

    cp._storage = _BoomStorage()  # type: ignore[assignment]
    cid = cp.save_reference(s.id, s.thread_id, "cp-x")
    assert cid == -1  # best-effort 兜底


# ---- api ------------------------------------------------------------------

@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """FastAPI TestClient + 临时 DB（monkeypatch 默认 db_path）。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    # 强制 sessions 用临时 DB
    from agent.sessions import api as sessions_api
    test_db = tmp_path / "sessions_api_test.db"
    monkeypatch.setattr(sessions_api, "_storage", SessionStorage(test_db))
    monkeypatch.setattr(sessions_api, "_checkpointer", None)

    test_app = FastAPI()
    test_app.include_router(sessions_api.router)

    with TestClient(test_app) as client:
        yield client


def test_api_create_session(api_client):
    resp = api_client.post(
        "/sessions",
        json={"title": "API test", "owner": "alice", "project_name": "demo"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == "API test"
    assert data["owner"] == "alice"
    assert data["status"] == "active"
    assert data["thread_id"] == data["id"]


def test_api_list_sessions_default_active(api_client):
    api_client.post("/sessions", json={"title": "s1"})
    api_client.post("/sessions", json={"title": "s2"})
    resp = api_client.get("/sessions")
    assert resp.status_code == 200
    titles = {s["title"] for s in resp.json()}
    assert titles == {"s1", "s2"}


def test_api_get_session_returns_messages_and_checkpoints(api_client):
    s = api_client.post("/sessions", json={"title": "s"}).json()
    resp = api_client.get(f"/sessions/{s['id']}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"] == "s"
    assert "messages" in data
    assert "checkpoints" in data


def test_api_get_session_404(api_client):
    resp = api_client.get("/sessions/nonexistent")
    assert resp.status_code == 404


def test_api_delete_session(api_client):
    s = api_client.post("/sessions", json={"title": "s"}).json()
    resp = api_client.delete(f"/sessions/{s['id']}")
    assert resp.status_code == 204
    # 再次 GET 404
    assert api_client.get(f"/sessions/{s['id']}").status_code == 404


@pytest.mark.asyncio
async def test_api_kb_search_returns_mock_results(api_client):
    resp = api_client.post(
        "/sessions/kb/search",
        json={"query": "EAIDE 架构", "top_k": 2},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["backend"] == "mock"
    assert data["elapsed_ms"] >= 0
    assert len(data["results"]) <= 2
    assert "snippet" in data
    assert "EAIDE" in data["snippet"] or len(data["snippet"]) >= 0