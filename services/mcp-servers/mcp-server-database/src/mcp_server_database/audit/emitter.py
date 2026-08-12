"""Audit emitter — best-effort dual-write to JSONL + SQLite (if available).

The shared audit SQLite is the same file the Agent and Tauri shell write to,
so we (a) always append a JSONL sidecar for safety, and (b) attempt an
SQLite insert if the file exists. SQLite is opened in append mode and the
insert is wrapped in a try/except so a transient SQLite error never breaks
the actual tool call.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def audit(action: str, payload: dict[str, Any] | None = None) -> None:
    payload = payload or {}
    rec = {
        "action": action,
        "payload": payload,
        "ts": datetime.now(UTC).isoformat(),
    }

    # 1. JSONL sidecar (always)
    try:
        _write_jsonl(rec)
    except Exception:
        pass

    # 2. SQLite (best effort)
    try:
        _write_sqlite(rec)
    except Exception:
        pass


# ---- Writers ---------------------------------------------------------------


def _write_jsonl(rec: dict) -> None:
    path = Path(os.environ.get("EAIDE_AUDIT_JSONL", "audit.sqlite.jsonl"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def _write_sqlite(rec: dict) -> None:
    db_path = Path(os.environ.get("EAIDE_AUDIT_DB", "audit.sqlite"))
    if not db_path.exists():
        return  # not bootstrapped yet — JSONL is the source of truth until it is
    conn = sqlite3.connect(str(db_path), timeout=2.0)
    try:
        conn.execute(
            "INSERT INTO audit(action, payload, ts) VALUES (?, ?, ?)",
            (rec["action"], json.dumps(rec["payload"], ensure_ascii=False, default=str), rec["ts"]),
        )
        conn.commit()
    finally:
        conn.close()
