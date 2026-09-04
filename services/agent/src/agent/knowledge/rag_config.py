"""knowledge.rag_config —— 本地知识库混合检索参数持久化 + 数据根路径解析。

设计（2026-09-03 改为落库 + 热应用）：
    - 参数持久化到 kb.db 的 kb_config 表（与向量/源文件同一可复制单元，
      拷 knowledge/ 目录即迁移参数）；不再依赖单独的 rag_config.json；
    - load_rag_config()：Agent 启动时从 kb.db 读取，逐字段范围校验后覆盖 settings
      （内存态）；若库内无配置但旧 rag_config.json 存在 → 一次性导入并落库（迁移）；
      均缺失/损坏 → 用默认值，best-effort，绝不阻断启动；
    - save_rag_config(patch)：白名单键 + 范围裁剪后写 kb.db，并**热应用**到 settings
      （查询期参数保存即生效，无需重启）；索引期参数（分块/父块/上下文前缀）同时
      生效于新入库，但已索引数据需重建才一致 → 回传 needs_reindex 清单供前端提示一键重建。

数据根（迁移自包含）：kb.db（含 kb_config）+ 上传文件 files/ 全落 rag_kb_dir()，
生产 = 安装目录/knowledge（EAIDE_DATA_ROOT 注入），复制该目录到新环境即可用。

路径红线：库/配置内零绝对路径，上传文件只存相对 files/ 的路径。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent.config import settings

logger = logging.getLogger("agent.knowledge.rag_config")

_CONFIG_NAME = "rag_config.json"
_FILES_SUBDIR = "files"
_DB_NAME = "kb.db"

# 可编辑参数白名单：key -> (类型, 下界, 上界)。路径类（rag_kb_dir / *_onnx_dir）
# 属部署配置，仅走环境变量，不进设置面板，避免用户改坏迁移语义。
_RAG_FIELDS: dict[str, tuple[str, float | None, float | None]] = {
    "rag_enabled": ("bool", None, None),
    "rag_chunk_size": ("int", 128, 4096),
    "rag_chunk_overlap": ("float", 0.0, 0.5),
    "rag_parent_size": ("int", 512, 16000),
    "rag_top_k": ("int", 1, 20),
    "rag_candidate_multiplier": ("int", 1, 10),
    "rag_rrf_k": ("int", 1, 500),
    "rag_bm25_enabled": ("bool", None, None),
    "rag_vector_enabled": ("bool", None, None),
    "rag_contextual_prefix_enabled": ("bool", None, None),
    "rag_max_file_mb": ("int", 1, 500),
    "rag_preload_on_startup": ("bool", None, None),
    "rag_rerank_enabled": ("bool", None, None),
    "rag_rerank_top_n": ("int", 1, 100),
    # 大模型知识库验证（总开关 + 各增强子开关）
    "rag_llm_enhance_enabled": ("bool", None, None),
    "rag_llm_contextual_enabled": ("bool", None, None),
    "rag_hyde_enabled": ("bool", None, None),
    "rag_query_expansion_enabled": ("bool", None, None),
}

# 索引期参数：改了影响分块/向量化的产物，已入库数据需重建才一致（新入库立即生效）。
# 其余均为查询期参数，保存后热应用于下一次检索，无需重建也无需重启。
_INDEX_TIME_FIELDS: frozenset[str] = frozenset(
    {
        "rag_chunk_size",
        "rag_chunk_overlap",
        "rag_parent_size",
        "rag_contextual_prefix_enabled",
        "rag_llm_contextual_enabled",
    }
)


# ---- 数据根路径解析 ---------------------------------------------------------


def kb_dir() -> Path:
    """知识库数据根目录（kb.db + files/ + rag_config.json 的父级）。

    优先级：settings.rag_kb_dir（env/JSON 显式）> data_root()/knowledge。
    生产 data_root = 安装目录（EAIDE_DATA_ROOT 注入），复制该目录即迁移。
    """
    raw = (settings.rag_kb_dir or "").strip()
    if raw:
        return Path(raw).expanduser()
    from agent.paths import data_root

    return data_root() / "knowledge"


def kb_db_path() -> str:
    """混合检索库路径（chunks + parent + FTS5 + vec0 + meta 同库，单文件迁移）。"""
    return str(kb_dir() / _DB_NAME)


def kb_files_dir() -> Path:
    """上传源文件的复制入库目录（迁移自包含，不依赖用户原路径）。"""
    return kb_dir() / _FILES_SUBDIR


def rag_config_path() -> Path:
    """参数 JSON 路径（落在数据根内，随目录复制迁移）。"""
    return kb_dir() / _CONFIG_NAME


# ---- 校验 / 裁剪 ------------------------------------------------------------


def _coerce(key: str, value: Any) -> Any:
    """按白名单类型转换 + 范围裁剪；非法值返 None（调用方跳过）。"""
    spec = _RAG_FIELDS.get(key)
    if spec is None:
        return None
    kind, lo, hi = spec
    try:
        if kind == "bool":
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "yes", "on")
            return bool(value)
        if kind == "int":
            iv: float = int(value)
        elif kind == "float":
            iv = float(value)
        else:  # pragma: no cover - 白名单保证不会到这
            return None
    except (TypeError, ValueError):
        return None
    if lo is not None and iv < lo:
        iv = lo
    if hi is not None and iv > hi:
        iv = hi
    return int(iv) if kind == "int" else iv


def current_config() -> dict[str, Any]:
    """当前生效参数快照（读 settings，供 GET /config 与面板展示）。"""
    return {k: getattr(settings, k) for k in _RAG_FIELDS if hasattr(settings, k)}


# ---- 加载（启动时应用）/ 保存（热应用）------------------------------------


def _storage() -> Any:
    """延迟取存储单例（避免与 storage 的模块级循环导入）。"""
    from agent.knowledge.storage import get_default_storage

    return get_default_storage()


def _apply_to_settings(data: dict[str, Any]) -> list[str]:
    """把原始键值逐字段校验裁剪后写入 settings（内存态），返回已应用的键。"""
    applied: list[str] = []
    for key, raw in data.items():
        val = _coerce(key, raw)
        if val is None:
            continue
        setattr(settings, key, val)
        applied.append(key)
    return applied


def _migrate_legacy_json() -> dict[str, Any]:
    """一次性把旧 rag_config.json 导入 kb.db（库内无配置时才迁）；迁完保留旧文件不删。"""
    path = rag_config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    clean = {k: v for k, v in data.items() if k in _RAG_FIELDS}
    if clean:
        try:
            _storage().set_config_many({k: str(v) for k, v in clean.items()})
            logger.info("rag_config migrated from legacy json -> kb.db: %s", sorted(clean))
        except Exception as exc:  # pragma: no cover - 落库失败不阻断启动
            logger.warning("rag_config legacy migration failed: %s", exc)
    return clean


def load_rag_config() -> dict[str, Any]:
    """启动从 kb.db 读取 -> 校验裁剪 -> 覆盖 settings（内存态）。返回生效后的快照。

    库内无配置但旧 rag_config.json 存在 → 一次性导入并落库（迁移）。
    best-effort：库/文件缺失损坏、字段非法均静默跳过，用默认值，不阻断启动。
    """
    try:
        stored = _storage().get_all_config()
    except Exception as exc:  # pragma: no cover - 库不可用时降级默认值
        logger.warning("rag_config load from kb.db failed: %s", exc)
        stored = {}
    if not stored:
        stored = {k: str(v) for k, v in _migrate_legacy_json().items()}
    applied = _apply_to_settings(stored)
    if applied:
        logger.info("rag_config applied from kb.db: %s", sorted(applied))
    return current_config()


def save_rag_config(patch: dict[str, Any]) -> dict[str, Any]:
    """白名单键 + 范围裁剪后写 kb.db，并**热应用**到 settings（查询期参数保存即生效）。

    返回 {"ok", "restart_required"(=False), "changed", "hot_applied", "needs_reindex", "config"}：
      - hot_applied：本次改动中属查询期、已即时生效的键；
      - needs_reindex：本次改动中属索引期的键（已入库数据需重建才一致，前端据此提示）。
    调用方（API 层）负责在返回后重置检索/入库单例，使缓存的 top_k/reranker 等按新参数重建。
    """
    clean: dict[str, Any] = {}
    for key, raw in (patch or {}).items():
        val = _coerce(key, raw)
        if val is None:
            continue
        clean[key] = val
    if not clean:
        return {
            "ok": True,
            "restart_required": False,
            "changed": [],
            "hot_applied": [],
            "needs_reindex": [],
            "config": current_config(),
        }
    try:
        _storage().set_config_many({k: str(v) for k, v in clean.items()})
    except Exception as exc:
        logger.warning("rag_config save to kb.db failed: %s", exc)
        return {
            "ok": False,
            "restart_required": False,
            "config": current_config(),
            "error": str(exc),
        }
    # 热应用：全部改动写回 settings（查询期立即生效；索引期对新入库生效，旧数据待重建）
    _apply_to_settings(clean)
    changed = sorted(clean)
    needs_reindex = sorted(k for k in clean if k in _INDEX_TIME_FIELDS)
    hot_applied = sorted(k for k in clean if k not in _INDEX_TIME_FIELDS)
    logger.info("rag_config saved+hot-applied: changed=%s needs_reindex=%s", changed, needs_reindex)
    return {
        "ok": True,
        "restart_required": False,
        "changed": changed,
        "hot_applied": hot_applied,
        "needs_reindex": needs_reindex,
        "config": current_config(),
    }


def index_time_keys() -> tuple[str, ...]:
    """索引期参数键（改了需重建才对已入库数据生效）。"""
    return tuple(k for k in _RAG_FIELDS if k in _INDEX_TIME_FIELDS)


def editable_keys() -> tuple[str, ...]:
    """设置面板可编辑的参数键（白名单顺序）。"""
    return tuple(_RAG_FIELDS)
