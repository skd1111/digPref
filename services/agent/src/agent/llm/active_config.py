"""LLM active 后端统一配置 —— router.db 为唯一长期事实源。

背景（双轨制统一）：
    历史上 Tauri 侧把后端选择写在 %APPDATA%/eaide/llm-config.json，
    Agent 启动时由 Rust 读出转成环境变量注入；而模型注册表在 router.db。
    两套配置导致"db 里启用了模型但仍走 mock"的坑。

统一后的解析优先级（Agent 启动 / PUT /router/active）：
    1. 显式 EAIDE_LLM_BACKEND 环境变量（开发/运维/测试覆盖，如 mock 单测）
    2. 遗留 llm-config.json 邮箱（Tauri 在 Agent 离线时的兜底写入）
       —— 消费式读取：导入 router.db 后删除文件
    3. router.db llm_kv.llm_active（JSON）
    4. 默认 {"active": "ollama"} —— 不再静默掉进 mock

约束：本模块禁止 import agent.config —— main.py 在 settings 加载前就要
调用 apply_active()，db 路径直接读环境变量 EAIDE_LLM_ROUTER_DB_PATH。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KV_KEY = "llm_active"
DEFAULT_CONFIG: dict[str, Any] = {"active": "ollama"}
_VALID_ACTIVE = {"mock", "ollama", "private", "custom"}


def _db_path() -> str:
    return os.environ.get("EAIDE_LLM_ROUTER_DB_PATH", "router.db")


def _legacy_json_path() -> Path:
    """json 镜像统一落在安装目录：<工作目录>/config/llm-config.json。

    Tauri 拉起 Agent 时 cwd = 安装目录，且注入 EAIDE_CONFIG_DIR=<安装目录>/config；
    开发模式 cwd = 项目根（config/ 同样存在）。
    """
    cfg_dir = os.environ.get("EAIDE_CONFIG_DIR")
    if cfg_dir:
        return Path(cfg_dir) / "llm-config.json"
    return Path("config") / "llm-config.json"


# ---- 同步 kv（sqlite3；启动路径与 API 都可用）------------------------------


def _kv_read() -> dict[str, Any] | None:
    try:
        path = _db_path()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        try:
            con.execute(
                "CREATE TABLE IF NOT EXISTS llm_kv ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
            )
            row = con.execute("SELECT value FROM llm_kv WHERE key = ?", (KV_KEY,)).fetchone()
        finally:
            con.close()
    except Exception as exc:
        logger.warning("active_config kv read failed: %s", exc)
        return None
    if row is None:
        return None
    try:
        data = json.loads(row[0])
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


def _kv_write(cfg: dict[str, Any]) -> None:
    path = _db_path()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS llm_kv ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT (datetime('now')))"
        )
        con.execute(
            "INSERT INTO llm_kv (key, value, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
            (KV_KEY, json.dumps(cfg, ensure_ascii=False)),
        )
        con.commit()
    finally:
        con.close()


def _read_legacy_json() -> dict[str, Any] | None:
    p = _legacy_json_path()
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("active_config legacy json read failed: %s", exc)
        return None


# ---- 解析 / 应用 -----------------------------------------------------------


def resolve_active(*, consume_legacy: bool = True, force_consume: bool = False) -> dict[str, Any]:
    """按优先级解析 active 配置。

    consume_legacy=True（启动路径）：遗留 json 作为邮箱被消费（导入 db 后删除）。
    consume_legacy=False（只读端点）：仅回显，不产生副作用。
    force_consume：测试用，绕过 pytest 环境保护显式触发消费。
    """
    env = os.environ.get("EAIDE_LLM_BACKEND", "").strip().lower()
    if env in _VALID_ACTIVE:
        return {"active": env}
    legacy = _read_legacy_json()
    if legacy and str(legacy.get("active", "")).lower() in _VALID_ACTIVE:
        # pytest 环境下只读不消费，避免单测误删用户真实配置（force_consume 可绕过）
        if consume_legacy and (force_consume or not os.environ.get("PYTEST_CURRENT_TEST")):
            try:
                _kv_write(legacy)
            except Exception as exc:
                logger.warning("active_config legacy migration failed: %s", exc)
            with contextlib.suppress(OSError):
                _legacy_json_path().unlink(missing_ok=True)
        return legacy
    db = _kv_read()
    if db and str(db.get("active", "")).lower() in _VALID_ACTIVE:
        return db
    return dict(DEFAULT_CONFIG)


def load_saved_active() -> dict[str, Any]:
    """只读：返回持久化的完整配置（db 优先，遗留 json 次之，不看 env）。

    供 GET /router/active 回显给前端编辑；区别于 resolve_active（生效态）。
    """
    db = _kv_read()
    if db and str(db.get("active", "")).lower() in _VALID_ACTIVE:
        return db
    legacy = _read_legacy_json()
    if legacy and str(legacy.get("active", "")).lower() in _VALID_ACTIVE:
        return legacy
    return dict(DEFAULT_CONFIG)


def apply_active(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """解析（或直接用给定配置）→ 写入进程环境变量 + 同步 settings 字段。

    必须在 agent.config 首次实例化之前调用才能让 settings 生效；
    运行中调用时会尽力热更新已构造的 settings 字段（新 LMRouter 实例即生效）。
    """
    if cfg is None:
        cfg = resolve_active()
    active = str(cfg.get("active", "ollama")).lower()
    if active not in _VALID_ACTIVE:
        active = "ollama"
    cfg = {**cfg, "active": active}  # 归一化后的副本（非法值兜底 ollama）
    os.environ["EAIDE_LLM_BACKEND"] = active

    ollama = cfg.get("ollama") or {}
    private_like = cfg.get("private" if active == "private" else "custom") or {}
    if active == "ollama":
        if ollama.get("base_url"):
            os.environ["EAIDE_OLLAMA_BASE_URL"] = str(ollama["base_url"])
        if ollama.get("model"):
            os.environ["EAIDE_OLLAMA_MODEL"] = str(ollama["model"])
    elif active in ("private", "custom"):
        if private_like.get("base_url"):
            os.environ["EAIDE_PRIVATE_LLM_BASE_URL"] = str(private_like["base_url"])
        if private_like.get("api_key"):
            os.environ["EAIDE_PRIVATE_LLM_API_KEY"] = str(private_like["api_key"])
        if private_like.get("model"):
            os.environ["EAIDE_PRIVATE_LLM_MODEL"] = str(private_like["model"])

    # 热更新已构造的 settings（settings 读 env 只在实例化时；运行中切换靠这里）
    try:
        from agent.config import settings

        if active == "ollama":
            if ollama.get("base_url"):
                settings.ollama_base_url = str(ollama["base_url"])
            if ollama.get("model"):
                settings.ollama_model = str(ollama["model"])
        elif active in ("private", "custom"):
            if private_like.get("base_url"):
                settings.private_llm_base_url = str(private_like["base_url"])
            if private_like.get("api_key"):
                settings.private_llm_api_key = str(private_like["api_key"])
            if private_like.get("model"):
                settings.private_llm_model = str(private_like["model"])
    except Exception as exc:
        logger.debug("active_config settings hot-update skipped: %s", exc)
    logger.info("active_config applied: active=%s", active)
    return cfg


def save_active(cfg: dict[str, Any]) -> None:
    """持久化到 router.db，并镜像写遗留 json（兼容 Tauri 旧读取路径）。"""
    _kv_write(cfg)
    try:
        p = _legacy_json_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("active_config legacy json mirror write failed: %s", exc)
