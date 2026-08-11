#!/usr/bin/env python3
"""seed-audit-db.py — bootstrap an empty audit SQLite with the canonical schema."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    action    TEXT NOT NULL,
    payload   TEXT NOT NULL,
    ts        TEXT NOT NULL,
    operator  TEXT,
    run_id    TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit(action);
CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit(run_id);
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", default="audit.sqlite")
    args = p.parse_args()
    path = Path(args.db)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.close()
    print(f"✓ audit schema ready at {path}")


if __name__ == "__main__":
    main()
