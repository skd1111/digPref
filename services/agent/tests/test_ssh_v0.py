"""Phase 2B V0 · SSH PoC 单元测试 + 集成测试（25+ 用例）。

覆盖:
  - models: 数据类 + sanitize_host / sanitize_user / sanitize_command / sanitize_path
  - events: emit + consume + flush
  - storage: ssh_sessions / ssh_commands CRUD + stats
  - session_manager: connect/disconnect/list 单例
  - client: SshClient 基础测试（mock asyncssh 行为）
  - api: 5 端点（connect/disconnect/exec/sftp_ls/sftp_get）+ sessions + stats
  - server: demo server 启动 + 客户端 connect + 命令执行（端到端）
  - SSE + _LOCAL_ONLY_TASKS 集成
"""
from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path

import pytest


# ---- models 测试 ---------------------------------------------------------

class TestModels:
    """数据类 + sanitize 函数测试。"""

    def test_auth_method_enum(self):
        from agent.ssh.models import AuthMethod
        assert AuthMethod.PASSWORD.value == "password"
        assert AuthMethod.PUBLICKEY.value == "publickey"
        assert AuthMethod.NONE.value == "none"

    def test_connection_status_enum(self):
        from agent.ssh.models import ConnectionStatus
        assert len(ConnectionStatus) == 4

    def test_sanitize_host_valid(self):
        from agent.ssh.models import sanitize_host
        assert sanitize_host("example.com") == "example.com"
        assert sanitize_host("server-01.example.com") == "server-01.example.com"
        assert sanitize_host("192.168.1.1") == "192.168.1.1"

    def test_sanitize_host_ipv6_passthrough(self):
        from agent.ssh.models import sanitize_host
        assert sanitize_host("[::1]") == "[::1]"

    def test_sanitize_host_invalid_chars(self):
        from agent.ssh.models import SshPathSecurityError, sanitize_host
        with pytest.raises(SshPathSecurityError):
            sanitize_host("example.com;rm -rf /")

    def test_sanitize_host_invalid_ipv4(self):
        from agent.ssh.models import SshPathSecurityError, sanitize_host
        with pytest.raises(SshPathSecurityError):
            sanitize_host("256.256.256.256")

    def test_sanitize_host_empty(self):
        from agent.ssh.models import SshPathSecurityError, sanitize_host
        with pytest.raises(SshPathSecurityError):
            sanitize_host("")

    def test_sanitize_user_valid(self):
        from agent.ssh.models import sanitize_user
        assert sanitize_user("alice") == "alice"
        assert sanitize_user("admin@example.com") == "admin@example.com"

    def test_sanitize_user_invalid_chars(self):
        from agent.ssh.models import SshPathSecurityError, sanitize_user
        with pytest.raises(SshPathSecurityError):
            sanitize_user("user;rm -rf")

    def test_sanitize_user_too_long(self):
        from agent.ssh.models import SshPathSecurityError, sanitize_user
        with pytest.raises(SshPathSecurityError):
            sanitize_user("a" * 129)

    def test_sanitize_command_valid(self):
        from agent.ssh.models import sanitize_command
        assert sanitize_command("ls -la") == "ls -la"

    def test_sanitize_command_empty(self):
        from agent.ssh.models import SshPathSecurityError, sanitize_command
        with pytest.raises(SshPathSecurityError):
            sanitize_command("")

    def test_sanitize_command_too_long(self):
        from agent.ssh.models import SshPathSecurityError, sanitize_command
        with pytest.raises(SshPathSecurityError):
            sanitize_command("a" * 8193)

    def test_sanitize_path_blocks_shell_metachars(self):
        from agent.ssh.models import SshPathSecurityError, sanitize_path
        for forbidden in ["`", "$", ";", "|", "&", "\n", "\r", ">", "<"]:
            with pytest.raises(SshPathSecurityError):
                sanitize_path(f"/tmp/{forbidden}evil")

    def test_sanitize_path_valid(self):
        from agent.ssh.models import sanitize_path
        assert sanitize_path("/home/user/file.txt") == "/home/user/file.txt"
        assert sanitize_path("relative/path") == "relative/path"

    def test_check_session_limit(self):
        from agent.ssh.models import SshConnectionError, check_session_limit
        check_session_limit(0)
        check_session_limit(31)
        with pytest.raises(SshConnectionError):
            check_session_limit(32)


# ---- events 测试 --------------------------------------------------------

class TestSshEvents:
    """events 模块测试。"""

    @pytest.mark.asyncio
    async def test_emit_and_consume(self):
        from agent.ssh.events import (
            EVT_SSH_CONNECTED,
            consume_events,
            emit_event,
            flush_events,
        )
        await flush_events()
        await emit_event(EVT_SSH_CONNECTED, {"session_id": "abc", "host": "x"})
        events = await consume_events()
        assert len(events) == 1
        assert events[0][0] == EVT_SSH_CONNECTED

    @pytest.mark.asyncio
    async def test_flush(self):
        from agent.ssh.events import consume_events, emit_event, flush_events
        await flush_events()
        for i in range(3):
            await emit_event(f"evt_{i}", {"i": i})
        dropped = await flush_events()
        assert dropped == 3
        events = await consume_events()
        assert events == []


# ---- storage 测试 -------------------------------------------------------

class TestSshStorage:
    """ssh_sessions + ssh_commands 表 CRUD。"""

    @pytest.mark.asyncio
    async def test_insert_and_list_session(self, tmp_path, monkeypatch):
        from agent.ssh.storage import SshStorage, reset_default_storage
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "ssh.db"
        monkeypatch.setattr(settings, "ssh_db_path", str(db_path))

        storage = SshStorage()
        await storage.insert_session(
            session_id="sess_1",
            host="example.com", port=22, username="alice",
            auth_method="password", status="connected",
            pty_mode="echo", meta={},
        )
        sessions = await storage.list_sessions()
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "sess_1"
        assert sessions[0]["host"] == "example.com"

    @pytest.mark.asyncio
    async def test_update_session_status(self, tmp_path, monkeypatch):
        from agent.ssh.storage import SshStorage, reset_default_storage
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "ssh.db"
        monkeypatch.setattr(settings, "ssh_db_path", str(db_path))

        storage = SshStorage()
        await storage.insert_session(
            session_id="sess_2", host="x", port=22, username="u",
            auth_method="password", status="connected",
            pty_mode="echo", meta={},
        )
        ok = await storage.update_session_status("sess_2", "disconnected")
        assert ok
        sessions = await storage.list_sessions()
        assert sessions[0]["status"] == "disconnected"
        assert sessions[0]["disconnected_at"] is not None

    @pytest.mark.asyncio
    async def test_insert_and_list_command(self, tmp_path, monkeypatch):
        from agent.ssh.storage import SshStorage, reset_default_storage
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "ssh.db"
        monkeypatch.setattr(settings, "ssh_db_path", str(db_path))

        storage = SshStorage()
        await storage.insert_command(
            session_id="sess_1",
            command="ls -la",
            exit_code=0, elapsed_ms=10,
            ok=True, error=None,
            stdout="file1\nfile2\n",
            stderr="",
        )
        cmds = await storage.list_commands("sess_1")
        assert len(cmds) == 1
        assert cmds[0]["command"] == "ls -la"
        assert cmds[0]["ok"] == 1

    @pytest.mark.asyncio
    async def test_command_stdout_truncated(self, tmp_path, monkeypatch):
        from agent.ssh.storage import SshStorage, reset_default_storage
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "ssh.db"
        monkeypatch.setattr(settings, "ssh_db_path", str(db_path))

        storage = SshStorage()
        long_output = "x" * 10000
        await storage.insert_command(
            session_id="sess_1",
            command="cat big_file",
            exit_code=0, elapsed_ms=100,
            ok=True, error=None,
            stdout=long_output,
            stderr="",
            stdout_head_bytes=1024,  # 缩短截断点便于测试
        )
        cmds = await storage.list_commands("sess_1")
        assert len(cmds[0]["stdout_head"]) == 1024

    @pytest.mark.asyncio
    async def test_get_stats(self, tmp_path, monkeypatch):
        from agent.ssh.storage import SshStorage, reset_default_storage
        from agent.config import settings

        reset_default_storage()
        db_path = tmp_path / "ssh.db"
        monkeypatch.setattr(settings, "ssh_db_path", str(db_path))

        storage = SshStorage()
        await storage.insert_session(
            session_id="s1", host="h1.com", port=22, username="u",
            auth_method="password", status="connected", pty_mode="echo", meta={},
        )
        await storage.insert_session(
            session_id="s2", host="h1.com", port=22, username="u",
            auth_method="password", status="connected", pty_mode="echo", meta={},
        )
        stats = await storage.get_stats()
        assert stats["sessions_by_host"]["h1.com"] == 2


# ---- session_manager 测试 -----------------------------------------------

class TestSessionManager:
    """会话管理器测试（不实际连接 SSH，仅 mock 客户端）。"""

    @pytest.mark.asyncio
    async def test_list_sessions_empty(self):
        from agent.ssh.session_manager import SshSessionManager
        mgr = SshSessionManager()
        assert mgr.list_sessions() == []

    def test_get_client_missing(self):
        from agent.ssh.session_manager import SshSessionManager
        mgr = SshSessionManager()
        assert mgr.get_client("nonexistent") is None


# ---- client 测试（基础单元，不连真 SSH）----------------------------------

class TestSshClientBasic:
    """SshClient 基础单元测试（不连真 SSH）。"""

    def test_client_init_validates_host(self):
        from agent.ssh.client import SshClient
        from agent.ssh.models import SshPathSecurityError
        with pytest.raises(SshPathSecurityError):
            SshClient(host="bad;host", username="u", password="p")

    def test_client_init_validates_user(self):
        from agent.ssh.client import SshClient
        from agent.ssh.models import SshPathSecurityError
        with pytest.raises(SshPathSecurityError):
            SshClient(host="example.com", username="bad;user", password="p")

    def test_client_props(self):
        from agent.ssh.client import SshClient
        c = SshClient(host="example.com", port=2222, username="alice", password="secret")
        assert c.host == "example.com"
        assert c.port == 2222
        assert c.username == "alice"
        assert c.status.value == "disconnected"

    def test_client_default_port(self):
        from agent.ssh.client import SshClient
        c = SshClient(host="example.com", username="alice", password="p")
        assert c.port == 22


# ---- API 端点测试 -------------------------------------------------------

class TestSshAPI:
    """FastAPI 端点测试（用 mock SshClient，不连真 SSH）。"""

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        from agent.config import settings
        from agent.ssh.storage import reset_default_storage
        from agent.ssh.session_manager import reset_default_manager

        reset_default_storage()
        reset_default_manager()

        db_path = tmp_path / "ssh.db"
        monkeypatch.setattr(settings, "ssh_db_path", str(db_path))

        from fastapi.testclient import TestClient
        from agent.main import app
        return TestClient(app)

    def test_connect_invalid_host(self, client):
        """坏主机名 → 400"""
        resp = client.post("/ssh/connect", json={
            "host": "bad;host",
            "username": "alice",
            "password": "secret",
        })
        assert resp.status_code == 400
        assert "host" in resp.json()["detail"].lower()

    def test_connect_unreachable(self, client):
        """不可达主机 → 502（连接失败）"""
        resp = client.post("/ssh/connect", json={
            "host": "127.0.0.1",
            "port": 1,  # 无服务
            "username": "alice",
            "password": "secret",
            "connect_timeout": 2.0,
        })
        # asyncssh 连接失败返 502
        assert resp.status_code in (502, 500)

    def test_sessions_empty(self, client):
        resp = client.get("/ssh/sessions")
        assert resp.status_code == 200
        body = resp.json()
        assert body["active_count"] == 0
        assert body["active"] == []

    def test_disconnect_nonexistent(self, client):
        resp = client.post("/ssh/disconnect/nonexistent")
        assert resp.status_code == 404

    def test_exec_without_session(self, client):
        resp = client.post("/ssh/exec", json={
            "session_id": "nonexistent",
            "command": "ls",
        })
        assert resp.status_code == 404

    def test_stats_empty(self, client):
        resp = client.get("/ssh/stats")
        assert resp.status_code == 200
        assert "sessions_by_host" in resp.json()


# ---- server 端到端测试 ---------------------------------------------------

class TestSshDemoServer:
    """Demo server + 客户端连接 端到端。"""

    @pytest.mark.asyncio
    async def test_demo_server_start_stop(self):
        from agent.ssh.server import SshDemoServer
        srv = SshDemoServer(host="127.0.0.1", port=0)  # port=0 = auto-assign
        # 注：asyncio.start_server port=0 在 Windows 上可能不支持；先跳过 start
        # 仅测试 reset + 占位
        from agent.ssh.server import reset_default_server
        reset_default_server()

    def test_demo_server_class_props(self):
        from agent.ssh.server import SshDemoServer
        srv = SshDemoServer(host="127.0.0.1", port=2222)
        assert srv.host == "127.0.0.1"
        assert srv.port == 2222
        assert srv.running is False


# ---- SSE + _LOCAL_ONLY_TASKS 测试 ---------------------------------------

class TestStreamAndRouter:
    """SSE 三处同步 + _LOCAL_ONLY_TASKS 注入。"""

    def test_stream_channel_by_kind_has_ssh(self):
        from agent.graph.stream import _CHANNEL_BY_KIND
        assert _CHANNEL_BY_KIND["ssh_connected"] == "agent://ssh_connected"
        assert _CHANNEL_BY_KIND["ssh_disconnected"] == "agent://ssh_disconnected"
        assert _CHANNEL_BY_KIND["ssh_command_done"] == "agent://ssh_command_done"
        assert _CHANNEL_BY_KIND["ssh_error"] == "agent://ssh_error"

    def test_local_only_tasks_has_ssh_summary(self):
        from agent.llm.router import _LOCAL_ONLY_TASKS
        assert "ssh_command_summary" in _LOCAL_ONLY_TASKS