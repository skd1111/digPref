"""Tests for safety/write_detector + safety/policy."""
from __future__ import annotations

import pytest

from agent.safety.policy import policy_for
from agent.safety.write_detector import is_write_call


class TestWriteDetector:
    def test_read_call(self):
        assert is_write_call({"name": "db.query", "args": {"sql": "SELECT 1"}}) is False

    def test_insert_sql(self):
        assert is_write_call({"name": "db.execute", "args": {"sql": "INSERT INTO t VALUES (1)"}}) is True

    def test_update_sql(self):
        assert is_write_call({"name": "db.execute", "args": {"sql": "UPDATE t SET x=1 WHERE id=1"}}) is True

    def test_drop_sql(self):
        assert is_write_call({"name": "db.execute", "args": {"sql": "DROP TABLE users"}}) is True

    def test_name_with_write_token(self):
        assert is_write_call({"name": "ssh.exec"}) is True

    def test_risk_level_overrides(self):
        assert is_write_call({"name": "db.query", "args": {}, "risk_level": "high"}) is True


class TestPolicy:
    def test_read_auto_approved(self):
        d = policy_for({"name": "db.query", "args": {"sql": "SELECT 1"}, "risk_level": "read"})
        assert d.decision == "approve"
        assert d.risk_level == "read"

    def test_write_needs_hitl(self):
        d = policy_for({"name": "db.execute", "args": {"sql": "UPDATE t SET x=1 WHERE id=1"},
                        "risk_level": "medium"})
        assert d.decision == "needs_hitl"
        assert d.risk_level == "medium"

    def test_critical_always_hitl(self):
        d = policy_for({"name": "db.execute", "args": {"sql": "DROP TABLE users"},
                        "risk_level": "critical"})
        assert d.decision == "needs_hitl"
        assert "critical" in d.reason

    def test_missing_risk_defaults_to_read(self):
        d = policy_for({"name": "db.query", "args": {"sql": "SELECT 1"}})
        assert d.risk_level == "read"
        assert d.decision == "approve"

    def test_name_only_derives_risk(self):
        d = policy_for({"name": "ssh.exec", "args": {}})
        assert d.risk_level == "medium"
        assert d.decision == "needs_hitl"