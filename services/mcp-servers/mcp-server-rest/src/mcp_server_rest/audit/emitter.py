"""Audit emitter — non-blocking write."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime


def audit(action: str, payload: dict) -> None:
    path = os.environ.get("EAIDE_AUDIT_DB", "audit.sqlite") + ".jsonl"
    line = json.dumps(
        {"action": action, "payload": payload, "ts": datetime.now(UTC).isoformat()},
        ensure_ascii=False,
    )
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
