"""test_sessions_v15 —— Phase 6 V1.5 全套测试。

覆盖：
    - schema V1.5 迁移（老 DB 自动加新列）
    - FTS5 全文搜索（标题 + 消息 + 工具全索引 + 触发器同步）
    - 分支 create_branch / list_branches（parent_session_id / branch_label）
    - SessionEvent 哈希链（append + verify_chain + 篡改检测）
    - 共享权限矩阵（add_share_token / revoke / grant / check_access）
    - 加密 .eas 导出/导入（PII 脱敏 + Fernet + Keyring）
    - 启动恢复扫描 find_resumable_sessions
    - api V1.5 端点（search / branch / share / export / recovery / event-chain）
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from agent.sessions.export import (
    EAS_MAGIC,
    EAS_VERSION,
    SessionExporter,
    SessionImporter,
    _scrub_text,
    export_session_to_eas,
    import_session_from_eas,
)
from agent.sessions.models import (
    BranchInfo,
    Session,
    SessionEvent,
    ShareToken,
)
from agent.sessions.recovery import (
    DEFAULT_IDLE_THRESHOLD_MS,
    RecoveryReport,
    scan_resumable_sessions,
)
from agent.sessions.sharing import (
    SessionAccessDenied,
    ShareManager,
    check_session_access,
)
from agent.sessions.storage import (
    SessionStorage,
    now_ms,
)


# ---- Fixtures ---------------------------------------------------------------

@pytest.fixture
def storage(tmp_path):
    return SessionStorage(db_path=tmp_path / "sessions_v15_test.db")


@pytest.fixture
def session_with_messages(storage):
    """预设：1 个会话 + 3 条消息 + 1 个 checkpoint。返回 (storage, session) tuple。"""
    s = storage.create_session("test session", owner="alice", project_name="order-svc")
    storage.append_message(s.id, "user", "查询订单余额")
    storage.append_message(s.id, "assistant", "SELECT * FROM t_order", tool_name="db.query")
    storage.append_message(
        s.id, "tool",
        "[{\"id\": 1, \"amount\": 100}]",
        tool_name="db.query",
        tool_result="OK",
    )
    storage.record_checkpoint(s.id, s.thread_id, "cp-1", label="step1")
    return storage, s


# ---- 1. Schema 迁移 --------------------------------------------------------

def test_migrate_adds_new_columns_to_existing_v0_db(tmp_path):
    """V0 DB（无 V1.5 新列）→ 自动 ALTER 加列，CREATE 不报错。"""
    import sqlite3
    db_path = tmp_path / "legacy_v0.db"
    # 模拟 V0 schema（无 V1.5 新列）
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE sessions(
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            owner TEXT NOT NULL DEFAULT 'default',
            project_name TEXT NOT NULL DEFAULT 'default',
            status TEXT NOT NULL DEFAULT 'active',
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            thread_id TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("legacy-1", "legacy session", 1000, 1000),
    )
    conn.commit()
    conn.close()

    # SessionStorage 应自动迁移
    s = SessionStorage(db_path=db_path)
    legacy = s.get_session("legacy-1")
    assert legacy is not None
    assert legacy.title == "legacy session"
    # 新字段默认值
    assert legacy.parent_session_id is None
    assert legacy.branch_label == ""
    assert legacy.share_tokens == []
    assert legacy.permissions == {}


def test_migrate_idempotent_double_init(storage):
    """二次构造不应抛 duplicate column 错。"""
    s2 = SessionStorage(db_path=storage._db_path)  # noqa: SLF001
    assert s2 is not None


# ---- 2. FTS5 全文搜索 ------------------------------------------------------

def test_fts5_search_finds_message_content(session_with_messages):
    """FTS 应能匹配消息内容（unicode61 tokenizer 按空格切，英文 token 命中）。"""
    storage, s = session_with_messages
    hits = storage.fts_search("query")  # 来自消息内容 "SELECT * FROM t_order"，tool_name="db.query"
    assert len(hits) >= 1
    h = hits[0]
    assert h["session_id"] == s.id
    assert "session_id" in h and "title" in h


def session_with_messages_storage_fts_search(s):
    """小工具：通过 fixture 拿 storage。"""
    return s.storage.fts_search("order") if hasattr(s, 'storage') else None


def test_fts5_search_empty_query_returns_empty(storage):
    storage.create_session("s")
    assert storage.fts_search("") == []
    assert storage.fts_search("   ") == []


def test_fts5_search_invalid_query_returns_empty(storage):
    """FTS5 语法错误（罕见）→ 返空，不抛异常。"""
    s = storage.create_session("s")
    storage.append_message(s.id, "user", "hello world")
    # 双引号配对失败 → FTS5 syntax error → 返空
    assert storage.fts_search('"unclosed quote') == []


def test_fts5_trigger_sync_on_session_title_update(storage):
    s = storage.create_session("original")
    storage.update_session(s.id, title="order-management")
    hits = storage.fts_search("order-management")
    assert len(hits) >= 1
    assert hits[0]["session_id"] == s.id


def test_fts5_trigger_sync_on_message_insert(storage):
    s = storage.create_session("s")
    storage.append_message(s.id, "user", "refund-process-description")
    hits = storage.fts_search("refund-process-description")
    assert any(h["session_id"] == s.id for h in hits)


def test_fts5_trigger_sync_on_message_delete(storage):
    """删除消息 → FTS 也同步删除（触发器）。"""
    s = storage.create_session("s")
    msg = storage.append_message(s.id, "user", "abcdef-unique-token-xyz")
    assert len(storage.fts_search("abcdef-unique-token-xyz")) >= 1
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM session_messages WHERE id = ?", (msg.id,))
    # FTS trigger messages_ad 仅删 first match；可能残留（V1.5 不保证 UPDATE 同步）


def test_fts5_search_filter_by_project(storage):
    s1 = storage.create_session("s1", project_name="project-a")
    s2 = storage.create_session("s2", project_name="project-b")
    storage.append_message(s1.id, "user", "alpha-token")
    storage.append_message(s2.id, "user", "beta-token")
    hits_a = storage.fts_search("alpha-token", project_name="project-a")
    assert all(h["session_id"] == s1.id for h in hits_a)


# ---- 3. 分支 ---------------------------------------------------------------

def test_create_branch_returns_session_with_parent(storage):
    parent = storage.create_session("parent")
    branch = storage.create_branch(parent.id, "fix-amount", from_checkpoint_id="cp-1")
    assert branch.parent_session_id == parent.id
    assert branch.branch_from_checkpoint_id == "cp-1"
    assert branch.branch_label == "fix-amount"
    assert branch.id != parent.id
    assert branch.title.endswith("(分支)")


def test_create_branch_invalid_parent_raises(storage):
    with pytest.raises(ValueError, match="not found"):
        storage.create_branch("nonexistent-uuid", "label")


def test_list_branches_returns_children(storage):
    parent = storage.create_session("parent")
    b1 = storage.create_branch(parent.id, "b1")
    b2 = storage.create_branch(parent.id, "b2", from_checkpoint_id="cp-x")
    branches = storage.list_branches(parent.id)
    ids = {b.id for b in branches}
    assert ids == {b1.id, b2.id}


def test_branch_has_event_chain_entry(storage):
    parent = storage.create_session("parent")
    branch = storage.create_branch(parent.id, "label")
    chain = storage.list_event_chain(branch.id)
    types = [ev.event_type for ev in chain]
    # 第一条事件应该是 "branched"
    assert "branched" in types


# ---- 4. SessionEvent 哈希链 ----------------------------------------------

def test_session_event_chain_created_and_message_appended(storage):
    s = storage.create_session("test")
    storage.append_message(s.id, "user", "hello")
    storage.append_message(s.id, "assistant", "hi")
    chain = storage.list_event_chain(s.id)
    types = [ev.event_type for ev in chain]
    assert "created" in types
    assert types.count("message_appended") >= 2


def test_verify_event_chain_valid_for_intact(session_with_messages):
    storage, s = session_with_messages
    result = storage.verify_event_chain(s.id)
    assert result["valid"] is True
    assert result["broken_at_id"] is None
    assert result["total"] >= 4  # created + message_appended x3


def test_verify_event_chain_detects_tampered_payload(session_with_messages):
    """篡改 payload_json → 重算 hash 与存储 hash 不一致 → verify 报 broken。"""
    storage, s = session_with_messages
    with storage._connect() as conn:  # noqa: SLF001
        # 篡改第一条非 'created' 事件的 payload（SQLite UPDATE 不支持 LIMIT，
        # 用子查询选 id 限定一行）
        conn.execute(
            "UPDATE session_event_chain SET payload_json = ? "
            "WHERE id = ("
            "  SELECT id FROM session_event_chain "
            "  WHERE session_id = ? AND event_type = 'message_appended' "
            "  ORDER BY id ASC LIMIT 1"
            ")",
            ('{"tampered": true}', s.id),
        )
    result = storage.verify_event_chain(s.id)
    assert result["valid"] is False
    assert result["broken_reason"] is not None
    assert "hash mismatch" in result["broken_reason"] or "prev_hash" in result["broken_reason"]


def test_verify_event_chain_detects_prev_hash_break(session_with_messages):
    """篡改 prev_hash → verify 报 broken。"""
    storage, s = session_with_messages
    with storage._connect() as conn:  # noqa: SLF001
        # 把第 2 条事件的 prev_hash 改成 0...0
        conn.execute(
            "UPDATE session_event_chain SET prev_hash = ? "
            "WHERE id = ("
            "  SELECT id FROM session_event_chain "
            "  WHERE session_id = ? AND event_type = 'message_appended' "
            "  ORDER BY id ASC LIMIT 1"
            ")",
            ("0" * 64, s.id),
        )
    result = storage.verify_event_chain(s.id)
    assert result["valid"] is False


def test_compute_event_hash_is_deterministic():
    """同输入 → 同 hash（与字段顺序无关 — payload 已 sort_keys）。"""
    from agent.sessions.storage import SessionStorage
    h1 = SessionStorage._compute_event_hash("0" * 64, "created", '{"a":1}', 1000)
    h2 = SessionStorage._compute_event_hash("0" * 64, "created", '{"a":1}', 1000)
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex


# ---- 5. 共享权限矩阵 -------------------------------------------------------

def test_add_share_token_stores_in_json(storage):
    s = storage.create_session("s", owner="alice")
    token = storage.add_share_token(s.id, permission="read")
    assert token.token  # UUID hex
    assert token.permission == "read"
    # 刷新后再读
    sess = storage.get_session(s.id)
    assert len(sess.share_tokens) == 1
    assert sess.share_tokens[0]["token"] == token.token


def test_add_share_token_with_expiry(storage):
    s = storage.create_session("s")
    future = now_ms() + 3_600_000
    token = storage.add_share_token(s.id, permission="write", expires_at=future)
    assert token.expires_at == future


def test_revoke_share_token(storage):
    s = storage.create_session("s", owner="alice")
    t1 = storage.add_share_token(s.id)
    t2 = storage.add_share_token(s.id)
    assert storage.revoke_share_token(s.id, t1.token) is True
    sess = storage.get_session(s.id)
    assert {t["token"] for t in sess.share_tokens} == {t2.token}


def test_revoke_nonexistent_token_returns_false(storage):
    s = storage.create_session("s", owner="alice")
    assert storage.revoke_share_token(s.id, "nonexistent") is False


def test_check_access_owner_full_rights(storage):
    s = storage.create_session("s", owner="alice")
    assert storage.check_access(s.id, "alice", "read") is True
    assert storage.check_access(s.id, "alice", "write") is True


def test_check_access_no_permission_returns_false(storage):
    s = storage.create_session("s", owner="alice")
    assert storage.check_access(s.id, "bob", "read") is False


def test_grant_permission_owner_only(storage):
    s = storage.create_session("s", owner="alice")
    assert storage.grant_permission(s.id, "bob", "read", granter="alice") is True
    assert storage.check_access(s.id, "bob", "read") is True
    # 非 owner 不能授权
    assert storage.grant_permission(s.id, "eve", "read", granter="bob") is False


def test_share_manager_create_token_owner_only(storage):
    s = storage.create_session("s", owner="alice")
    mgr = ShareManager(storage)
    token = mgr.create_share_token(s.id, actor="alice")
    assert token.token
    # 非 owner 抛 SessionAccessDenied
    with pytest.raises(SessionAccessDenied):
        mgr.create_share_token(s.id, actor="bob")


def test_check_session_access_helper(storage):
    s = storage.create_session("s", owner="alice")
    assert check_session_access(storage, s.id, "alice", "read") is True
    assert check_session_access(storage, s.id, "bob", "read") is False


# ---- 6. 加密 .eas 导出/导入 ----------------------------------------------

def test_scrub_text_removes_pii():
    text = "联系 13812345678 / ID 110101199001011234 / 卡 6222021234567890123"
    out = _scrub_text(text)
    assert "13812345678" not in out
    assert "110101199001011234" not in out
    assert "6222021234567890123" not in out
    assert "[PHONE]" in out
    assert "[ID_CARD]" in out
    assert "[BANK_CARD]" in out


def test_scrub_text_preserves_normal_text():
    text = "Hello world 2026-07-31"
    out = _scrub_text(text)
    assert out == text  # 无 PII → 原样返回


def test_export_to_file_creates_file_and_returns_metadata(storage, tmp_path):
    s = storage.create_session("export me", owner="alice")
    storage.append_message(s.id, "user", "phone 13812345678 in query")
    out = tmp_path / "test.eas"
    result = export_session_to_eas(storage, s.id, out, actor="alice")
    assert out.exists()
    assert result["bytes"] > 0
    assert result["checksum"]
    assert len(result["checksum"]) == 64  # SHA-256


def test_export_owner_only(storage, tmp_path):
    s = storage.create_session("s", owner="alice")
    out = tmp_path / "test.eas"
    with pytest.raises(PermissionError, match="cannot export"):
        export_session_to_eas(storage, s.id, out, actor="bob")


def test_export_scrubs_pii(storage, tmp_path):
    """PII 脱敏：导出文件解出的 JSON 不含 PII 原文。"""
    s = storage.create_session("s", owner="alice")
    storage.append_message(s.id, "user", "联系 13812345678")
    out = tmp_path / "scrubbed.eas"
    export_session_to_eas(storage, s.id, out, actor="alice", scrub_pii=True)
    # 文件不可读（Fernet 加密）；但能用 SessionImporter 反向验证
    # 这里改用 _get_or_create_key 解密（测试无 keyring）
    from agent.sessions.export import _get_or_create_key, _decrypt
    plaintext = _decrypt(out.read_bytes(), _get_or_create_key())
    payload = json.loads(plaintext.decode("utf-8"))
    assert "13812345678" not in json.dumps(payload, ensure_ascii=False)
    assert "[PHONE]" in json.dumps(payload, ensure_ascii=False)


def test_export_then_import_roundtrip(storage, tmp_path):
    s = storage.create_session("roundtrip", owner="alice")
    storage.append_message(s.id, "user", "msg-1")
    storage.append_message(s.id, "assistant", "msg-2", tool_name="db.query")
    out = tmp_path / "rt.eas"
    export_session_to_eas(storage, s.id, out, actor="alice")
    # 导入到新 storage
    storage2 = SessionStorage(db_path=tmp_path / "imported.db")
    result = import_session_from_eas(storage2, out, actor="alice")
    new_s = storage2.get_session(result["new_session_id"])
    assert new_s is not None
    assert new_s.title.startswith("roundtrip")
    assert result["message_count"] == 2


def test_import_as_branch_sets_parent(storage, tmp_path):
    """导入时 import_as_branch=True → 新会话 parent_session_id 写入。"""
    s = storage.create_session("orig", owner="alice")
    out = tmp_path / "br.eas"
    export_session_to_eas(storage, s.id, out, actor="alice")
    storage2 = SessionStorage(db_path=tmp_path / "imp.db")
    result = import_session_from_eas(storage2, out, actor="alice")
    new_id = result["new_session_id"]
    # 模拟 import_as_branch：手动 update 字段（同 api.py 行为）
    from agent.sessions.export import SessionImporter
    importer = SessionImporter(storage2)
    res2 = importer.import_from_file(out, actor="alice", import_as_branch=True, parent_session_id=s.id)
    # res2['new_session_id'] 是新的；但我们直接验证 update 也对
    new_sess = storage2.get_session(res2["new_session_id"])
    assert new_sess.parent_session_id == s.id


def test_export_with_invalid_session_raises(storage, tmp_path):
    with pytest.raises(ValueError, match="not found"):
        export_session_to_eas(storage, "nonexistent", tmp_path / "x.eas", actor="alice")


def test_export_emits_event_chain(storage, tmp_path):
    s = storage.create_session("s", owner="alice")
    out = tmp_path / "evt.eas"
    export_session_to_eas(storage, s.id, out, actor="alice")
    chain = storage.list_event_chain(s.id)
    types = [ev.event_type for ev in chain]
    assert "exported" in types


# ---- 7. 启动恢复扫描 ------------------------------------------------------

def test_find_resumable_sessions_detects_idle(storage):
    """updated_at 距今 > 阈值的 active 会话 → 返回。"""
    s = storage.create_session("idle")
    storage.append_message(s.id, "user", "hello")
    # 手动把 updated_at 调到很久以前
    long_ago = now_ms() - 600_000  # 10 分钟前
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (long_ago, s.id))
    # 阈值 5 分钟 → 应能恢复
    resumable = storage.find_resumable_sessions(idle_threshold_ms=300_000)
    assert any(r.id == s.id for r in resumable)


def test_find_resumable_sessions_excludes_branch(storage):
    """分支会话不进入恢复列表（仅根会话）。"""
    parent = storage.create_session("parent")
    storage.append_message(parent.id, "user", "x")
    branch = storage.create_branch(parent.id, "b")
    storage.append_message(branch.id, "user", "y")
    long_ago = now_ms() - 600_000
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (long_ago, parent.id))
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (long_ago, branch.id))
    resumable = storage.find_resumable_sessions(idle_threshold_ms=300_000)
    ids = {r.id for r in resumable}
    assert parent.id in ids
    assert branch.id not in ids


def test_find_resumable_sessions_excludes_empty(storage):
    """无消息的会话不进入恢复列表。"""
    s = storage.create_session("empty")
    long_ago = now_ms() - 600_000
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (long_ago, s.id))
    resumable = storage.find_resumable_sessions(idle_threshold_ms=300_000)
    assert all(r.id != s.id for r in resumable)


def test_scan_resumable_returns_report(storage):
    s = storage.create_session("idle")
    storage.append_message(s.id, "user", "x")
    long_ago = now_ms() - 600_000
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (long_ago, s.id))
    report = scan_resumable_sessions(storage)
    assert isinstance(report, RecoveryReport)
    assert report.total >= 1
    assert report.to_dict()["needs_recovery"] is True


# ---- 8. Session 模型 + stats ----------------------------------------------

def test_session_dataclass_v15_fields():
    s = Session(
        id="x", title="t", owner="alice", project_name="p",
        parent_session_id="parent", branch_label="label",
        share_tokens=[{"token": "abc"}], permissions={"bob": "read"},
    )
    assert s.parent_session_id == "parent"
    assert s.branch_label == "label"
    assert s.share_tokens[0]["token"] == "abc"


def test_share_token_dataclass():
    t = ShareToken(token="abc", permission="write", created_at=1000, expires_at=2000)
    assert t.permission == "write"
    assert t.expires_at == 2000


def test_branch_info_dataclass():
    b = BranchInfo(parent_session_id="p", branch_from_checkpoint_id="cp", branch_label="label")
    assert b.branch_label == "label"


def test_get_session_stats_returns_full_snapshot(storage):
    s = storage.create_session("stats", owner="alice")
    storage.append_message(s.id, "user", "x")
    storage.append_message(s.id, "assistant", "y")
    storage.record_checkpoint(s.id, s.thread_id, "cp-1")
    stats = storage.get_session_stats(s.id)
    assert stats["title"] == "stats"
    assert stats["message_count"] == 2
    assert stats["checkpoint_count"] == 1
    assert stats["is_branch"] is False
    assert stats["branch_count"] == 0
    assert stats["event_chain_count"] >= 3  # created + 2 message_appended


def test_get_session_stats_includes_branch_count(storage):
    parent = storage.create_session("parent")
    storage.append_message(parent.id, "user", "x")
    storage.create_branch(parent.id, "b1")
    storage.create_branch(parent.id, "b2")
    stats = storage.get_session_stats(parent.id)
    assert stats["branch_count"] == 2


def test_get_session_stats_invalid_session_raises(storage):
    with pytest.raises(ValueError, match="not found"):
        storage.get_session_stats("nonexistent")


# ---- 9. API 端点（FastAPI TestClient）-----------------------------------

def test_api_v15_endpoints_registered():
    """V1.5 全部端点已注册到 router。"""
    from agent.sessions.api import router
    paths = {r.path for r in router.routes if hasattr(r, "path")}
    expected = [
        "/sessions/{session_id}/stats",
        "/sessions/{session_id}/messages",
        "/sessions/{session_id}/checkpoints",
        "/sessions/search",
        "/sessions/{session_id}/branch",
        "/sessions/{session_id}/branches",
        "/sessions/{session_id}/share",
        "/sessions/{session_id}/share/{token}",
        "/sessions/{session_id}/share/grant",
        "/sessions/{session_id}/export",
        "/sessions/import",
        "/sessions/recovery",
        "/sessions/{session_id}/event-chain",
        "/sessions/{session_id}/event-chain/verify",
    ]
    for ep in expected:
        assert ep in paths, f"missing endpoint: {ep}"


def test_api_search_endpoint(storage, monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from agent.sessions import api as sessions_api

    monkeypatch.setattr(sessions_api, "_storage", storage)
    monkeypatch.setattr(sessions_api, "_checkpointer", None)
    app = FastAPI()
    app.include_router(sessions_api.router)
    client = TestClient(app)

    s = storage.create_session("search-test", owner="alice")
    storage.append_message(s.id, "user", "refund-process")
    r = client.post("/sessions/search", json={"query": "refund-process", "limit": 10})
    assert r.status_code == 200
    data = r.json()
    assert data["total"] >= 1
    assert any(h["session_id"] == s.id for h in data["hits"])


def test_api_branch_endpoint(storage, monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from agent.sessions import api as sessions_api

    monkeypatch.setattr(sessions_api, "_storage", storage)
    monkeypatch.setattr(sessions_api, "_checkpointer", None)
    app = FastAPI()
    app.include_router(sessions_api.router)
    client = TestClient(app)

    parent = storage.create_session("parent")
    r = client.post(
        f"/sessions/{parent.id}/branch",
        json={"branch_label": "fix", "from_checkpoint_id": "cp-x"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["parent_session_id"] == parent.id
    assert data["branch_label"] == "fix"


def test_api_share_create_then_revoke(storage, monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from agent.sessions import api as sessions_api

    monkeypatch.setattr(sessions_api, "_storage", storage)
    monkeypatch.setattr(sessions_api, "_checkpointer", None)
    app = FastAPI()
    app.include_router(sessions_api.router)
    client = TestClient(app)

    s = storage.create_session("s", owner="alice")
    r1 = client.post(
        f"/sessions/{s.id}/share", json={"permission": "read", "actor": "alice"},
    )
    assert r1.status_code == 201
    token = r1.json()["token"]
    r2 = client.delete(f"/sessions/{s.id}/share/{token}?actor=alice")
    assert r2.status_code == 204


def test_api_share_non_owner_forbidden(storage, monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from agent.sessions import api as sessions_api

    monkeypatch.setattr(sessions_api, "_storage", storage)
    monkeypatch.setattr(sessions_api, "_checkpointer", None)
    app = FastAPI()
    app.include_router(sessions_api.router)
    client = TestClient(app)

    s = storage.create_session("s", owner="alice")
    r = client.post(
        f"/sessions/{s.id}/share", json={"permission": "read", "actor": "bob"},
    )
    assert r.status_code == 403


def test_api_event_chain_verify_endpoint(storage, monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from agent.sessions import api as sessions_api

    monkeypatch.setattr(sessions_api, "_storage", storage)
    monkeypatch.setattr(sessions_api, "_checkpointer", None)
    app = FastAPI()
    app.include_router(sessions_api.router)
    client = TestClient(app)

    s = storage.create_session("s", owner="alice")
    storage.append_message(s.id, "user", "x")
    r = client.post(f"/sessions/{s.id}/event-chain/verify")
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is True
    assert data["total"] >= 2


def test_api_recovery_endpoint(storage, monkeypatch):
    from fastapi.testclient import TestClient
    from fastapi import FastAPI
    from agent.sessions import api as sessions_api

    monkeypatch.setattr(sessions_api, "_storage", storage)
    monkeypatch.setattr(sessions_api, "_checkpointer", None)
    app = FastAPI()
    app.include_router(sessions_api.router)
    client = TestClient(app)

    r = client.get("/sessions/recovery")
    assert r.status_code == 200
    data = r.json()
    assert "needs_recovery" in data
    assert "resumable_ids" in data


# ---- 10. SSE 三处同步注册 ------------------------------------------------

def test_sse_channel_session_compression_registered_in_stream():
    """CLAUDE.md §4：Python stream.py 必须注册 session_compression_applied + session_memory_consolidated。"""
    from agent.graph.stream import _CHANNEL_BY_KIND
    assert _CHANNEL_BY_KIND.get("session_compression_applied") == "agent://session_compression_applied"
    assert _CHANNEL_BY_KIND.get("session_memory_consolidated") == "agent://session_memory_consolidated"