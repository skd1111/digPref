"""Shared pytest fixtures."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Make the src/ layout importable without installing the package.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Every test gets a fresh tmp working dir + isolated env vars."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EAIDE_AUDIT_JSONL", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("EAIDE_AUDIT_DB", str(tmp_path / "audit.sqlite"))
    monkeypatch.setenv("EAIDE_DB_TOOL_TIMEOUT_SEC", "5")
    monkeypatch.setenv("EAIDE_DB_DEFAULT_ROW_LIMIT", "50")
    monkeypatch.setenv("EAIDE_DB_ENFORCE_READONLY_ACCOUNT", "true")
    # disable HITL approval check in dry-run mode for tool unit tests
    monkeypatch.setenv("EAIDE_APPROVAL_DRY_RUN", "1")
    yield