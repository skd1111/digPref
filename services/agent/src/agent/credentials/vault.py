"""Resolve a credential by key.

Resolution order:
    1. Environment variable:  EAIDE_SECRET_<KEY_UPPER>
    2. HTTP call to Tauri IPC proxy (when running side-by-side)
    3. Encrypted YAML at ~/.eaide/secrets.yaml (development only)
"""
from __future__ import annotations

import os
from pathlib import Path

import httpx
import yaml
from cryptography.fernet import Fernet


def get(key: str) -> str | None:
    env = os.environ.get(f"EAIDE_SECRET_{key.upper()}")
    if env:
        return env
    # 已知缺口：当 Agent 与 Tauri 桌面端并排运行时，应通过 HTTP proxy 向
    # Rust 侧请求 keyring 中存储的凭证。当前仅支持环境变量和开发 YAML 两种
    # 来源，桌面端凭证集成待后续版本实现。
    return _read_dev_yaml(key)


def _read_dev_yaml(key: str) -> str | None:
    path = Path("~/.eaide/secrets.yaml").expanduser()
    if not path.exists():
        return None
    # Optional symmetric encryption with EAIDE_DEV_KEY env var.
    raw = path.read_bytes()
    if (k := os.environ.get("EAIDE_DEV_KEY")):
        raw = Fernet(k.encode()).decrypt(raw)
    data = yaml.safe_load(raw) or {}
    return data.get(key)