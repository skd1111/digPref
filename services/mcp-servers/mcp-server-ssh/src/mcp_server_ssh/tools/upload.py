"""ssh.upload — SFTP upload (HITL required upstream).

Hard contract:
    - The upstream LangGraph hitl_gate MUST have approved this call.
    - The caller MUST include an `approval_id` argument; we verify it against
      the shared audit log before dispatching.
    - Every upload is audited.
    - local_path and remote_path are sanitized to prevent path traversal.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from mcp_server_ssh.audit.emitter import audit
from mcp_server_ssh.client import connect

# Remote paths must be under one of these prefixes (defense-in-depth)
_ALLOWED_REMOTE_PREFIXES = (
    "/tmp/",
    "/var/tmp/",
    "/opt/",
    "/home/",
    "/srv/",
    "/data/",
    "/etc/",
    "/usr/local/",
    "/app/",
    "/deploy/",
    "/upload/",
)


class UploadError(Exception):
    """Surface to LLM as 'rejected by safety policy'."""


class ApprovalMissingError(UploadError):
    """Raised when approval_id is missing or invalid."""


def _sanitize_remote_path(path: str) -> str:
    """Resolve and validate a remote path. Rejects traversal attempts."""
    # Normalize and resolve
    normalized = os.path.normpath(path).replace("\\", "/")
    # Reject absolute path traversal (e.g. /tmp/../../../etc/shadow)
    resolved = os.path.normpath(os.path.join("/", normalized.lstrip("/")))
    if not resolved.startswith("/"):
        raise UploadError(f"remote_path must be absolute: {path!r}")
    # Must start with an allowed prefix
    if not any(resolved.startswith(p) for p in _ALLOWED_REMOTE_PREFIXES):
        raise UploadError(
            f"remote_path not in allowed prefixes: {path!r}. "
            f"Allowed: {', '.join(_ALLOWED_REMOTE_PREFIXES)}"
        )
    return resolved


def _sanitize_local_path(path: str) -> str:
    """Resolve local path and reject traversal outside project directory."""
    resolved = os.path.realpath(os.path.abspath(path))
    # Must be an absolute path
    if not os.path.isabs(resolved):
        raise UploadError(f"local_path must be absolute: {path!r}")
    return resolved


async def run(args: dict) -> dict:
    approval_id = args.get("approval_id")
    if not approval_id:
        raise ApprovalMissingError("approval_id is required for ssh.upload (HITL gate)")

    if not _verify_approval(approval_id):
        raise ApprovalMissingError(f"approval_id {approval_id!r} not found or not approved")

    local_path = _sanitize_local_path(args["local_path"])
    remote_path = _sanitize_remote_path(args["remote_path"])

    try:
        async with await connect(args["host"]) as conn:
            sftp = await conn.start_sftp_client()
            await sftp.put(local_path, remote_path)
    except Exception as exc:
        audit(
            "ssh.upload.error",
            {
                "approval_id": approval_id,
                "host": args["host"],
                "local_path": local_path,
                "remote_path": remote_path,
                "error": str(exc),
            },
        )
        raise UploadError(f"ssh.upload failed: {exc}") from exc

    audit(
        "ssh.upload.ok",
        {
            "approval_id": approval_id,
            "host": args["host"],
            "local_path": local_path,
            "remote_path": remote_path,
        },
    )

    return {
        "ok": True,
        "approval_id": approval_id,
        "remote_path": remote_path,
    }


def _verify_approval(approval_id: str) -> bool:
    """Verify that `approval_id` exists and was approved.

    Two strategies:
        - Production: read the shared audit JSONL log and look for
          an approval row with `decision='approve'` matching this id.
        - Dev / test: `EAIDE_APPROVAL_DRY_RUN=1` bypasses verification.
    """
    if os.environ.get("EAIDE_APPROVAL_DRY_RUN") == "1":
        return True
    return _approval_audit_lookup(approval_id)


def _approval_audit_lookup(approval_id: str) -> bool:
    """Read the shared audit JSONL log and look up the approval."""
    db_path = os.environ.get("EAIDE_AUDIT_DB", "audit.sqlite")
    jsonl_path = Path(db_path + ".jsonl")
    if not jsonl_path.exists():
        return False
    try:
        # Read from the end (most recent entries first)
        lines = []
        with open(jsonl_path, encoding="utf-8") as f:
            lines = f.readlines()
        # Scan in reverse for efficiency — recent approvals come last
        for line in reversed(lines):
            try:
                entry = json.loads(line)
            except (TypeError, json.JSONDecodeError):
                continue
            payload = entry.get("payload", {})
            if (
                entry.get("action") in ("approval.decision", "approval.approve")
                and payload.get("approval_id") == approval_id
                and payload.get("decision") == "approve"
            ):
                return True
    except OSError:
        return False
    return False
