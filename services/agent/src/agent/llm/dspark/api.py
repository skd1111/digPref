"""DSpark API router —— 4 端点 + 草稿模型路径编辑。

V0 范围：
    GET  /dspark/config                 当前 DSparkConfig
    GET  /dspark/policies               当前 PolicyMap（按 task_category）
    GET  /dspark/recent                 最近 N 条决策（默认 20，最大 100）
    POST /dspark/reload                 重新从 yaml 加载策略
    POST /dspark/draft-model-path       设置草稿模型路径（持久化到 dspark.json）
    POST /dspark/config                 全量配置更新（持久化 + 审计）

审计：所有写入端点（draft-model-path / config）落 audit.sqlite，
actor_type='system'，event_type='dspark_config_change'（与设计文档 §3.4 + CLAUDE.md 一致）。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent.audit.store import audit as audit_log
from agent.llm.dspark.config import DSparkConfig, SpeculativePolicy
from agent.llm.dspark.engine import engine
from agent.llm.dspark.policy import (
    _decide_dspark_with_reason,
    load_speculative_policies,
    set_local_only_tasks,
)


logger = logging.getLogger(__name__)


router = APIRouter(prefix="/dspark", tags=["dspark"])


# ---- 全量配置持久化（dspark.json）-------------------------------------------


def _dspark_persist_path() -> Path:
    """配置持久化文件位置。

    V0 优先用 EAIDE_DSPARK_PERSIST_PATH（test 隔离），否则放
    `<EAIDE_AUDIT_DB_PATH 同目录>/dspark.json`，最后回退 cwd。
    """
    explicit = os.environ.get("EAIDE_DSPARK_PERSIST_PATH")
    if explicit:
        return Path(explicit)
    audit_db = os.environ.get("EAIDE_AUDIT_DB_PATH")
    if audit_db:
        parent = Path(audit_db).parent
        return parent / "dspark.json"
    return Path("dspark.json")


def _audit_db_path() -> str:
    """审计 DB 绝对路径（用于 audit() 显式 db_path 参数）。

    优先级：
        1. EAIDE_AUDIT_DB_PATH env 显式设置（绝对路径）
        2. settings.audit_db_path 解析为绝对路径（基于 cwd）

    显式传给 audit() 可避免 pytest async 测试间 monkeypatch.chdir 不稳定时，
    写入路径与测试期望路径错位。
    """
    from agent.config import settings
    explicit = os.environ.get("EAIDE_AUDIT_DB_PATH")
    if explicit:
        return explicit
    p = Path(settings.audit_db_path)
    if not p.is_absolute():
        p = Path.cwd() / p
    return str(p.resolve())


def _load_dspark_config() -> dict[str, Any] | None:
    """读持久化的全量配置（启动时调一次）。返回 None 表示文件不存在/损坏。"""
    p = _dspark_persist_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[DSpark] failed to load %s: %s", p, e)
        return None


def _save_dspark_config(cfg: DSparkConfig) -> None:
    """写全量持久化配置。"""
    p = _dspark_persist_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        dump = {
            "draft_model_path": cfg.draft_model_path,
            "context_size": cfg.context_size,
            "gpu_layers": cfg.gpu_layers,
            "enable_global": cfg.enable_global,
            "short_output_threshold": cfg.short_output_threshold,
        }
        p.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.error("[DSpark] failed to persist to %s: %s", p, e)
        raise HTTPException(status_code=500, detail=f"persist failed: {e}") from e


# keep old names as backward-compatible wrappers (tests use these)
def _load_persisted_path() -> str | None:
    """[deprecated] 仅返回 draft_model_path；新代码用 _load_dspark_config。"""
    data = _load_dspark_config()
    if not data:
        return None
    v = data.get("draft_model_path")
    return v if isinstance(v, str) and v.strip() else None


def _save_persisted_path(path: str | None) -> None:
    """[deprecated] 仅保存 draft_model_path；新代码用 _save_dspark_config。"""
    # 读出现有配置，只更新 draft_model_path
    existing = _load_dspark_config() or {}
    existing["draft_model_path"] = path or ""
    p = _dspark_persist_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.error("[DSpark] failed to persist to %s: %s", p, e)
        raise HTTPException(status_code=500, detail=f"persist failed: {e}") from e


# ---- 内部状态（由 main.py 挂载时 init） ------------------------------------


class DSparkRuntime:
    """单例：DSpark 全局状态（被 main.py 持有）。"""

    config: DSparkConfig
    yaml_path: str | None
    policy_map: dict[str, SpeculativePolicy]

    def __init__(
        self,
        config: DSparkConfig,
        yaml_path: str | None,
        policy_map: dict[str, SpeculativePolicy],
    ) -> None:
        self.config = config
        self.yaml_path = yaml_path
        self.policy_map = policy_map

    def reload_policies(self) -> int:
        """重新加载 yaml；返回加载后的策略数。"""
        self.policy_map = load_speculative_policies(Path(self.yaml_path) if self.yaml_path else None)
        logger.info("[DSpark] reloaded policies: %d entries", len(self.policy_map))
        return len(self.policy_map)

    def set_draft_model_path(self, new_path: str | None) -> None:
        """运行时设置草稿模型路径（POST /dspark/draft-model-path 调一次）。

        - new_path 为 None / 空串 → 清空路径（DSpark 全局禁用）
        - 否则直接覆盖 config.draft_model_path（不校验文件存在，由 llama.cpp 加载时报错兜底）
        """
        self.config = self.config.model_copy(
            update={"draft_model_path": new_path if new_path else None}
        )
        logger.info("[DSpark] draft_model_path set to %s", new_path)

    def update_config(self, updates: dict[str, Any]) -> None:
        """运行时更新配置（POST /dspark/config 调一次）。"""
        allowed = {"draft_model_path", "context_size", "gpu_layers",
                    "enable_global", "short_output_threshold"}
        clean = {k: v for k, v in updates.items() if k in allowed and v is not None}
        if clean:
            self.config = self.config.model_copy(update=clean)
            logger.info("[DSpark] config updated: %s", list(clean.keys()))


_runtime: DSparkRuntime | None = None


def get_runtime() -> DSparkRuntime:
    if _runtime is None:
        raise HTTPException(status_code=503, detail="DSpark runtime not initialized")
    return _runtime


def init_dspark_runtime(
    *,
    config: DSparkConfig,
    yaml_path: str | None,
    local_only_tasks: list[str] | None = None,
    persisted_draft_path: str | None = None,
) -> DSparkRuntime:
    """由 main.py 的 lifespan 调用一次。

    `persisted_draft_path` 优先级高于 config.draft_model_path（来自 env var）。
    """
    global _runtime
    if local_only_tasks is not None:
        set_local_only_tasks(local_only_tasks)
    pm = load_speculative_policies(Path(yaml_path) if yaml_path else None)
    # 持久化路径覆盖 config 默认
    if persisted_draft_path:
        config = config.model_copy(update={"draft_model_path": persisted_draft_path})
    _runtime = DSparkRuntime(config=config, yaml_path=yaml_path, policy_map=pm)
    return _runtime


def reset_dspark_runtime() -> None:
    """测试夹具用。"""
    global _runtime
    _runtime = None


# ---- 端点 ------------------------------------------------------------------


@router.get("/config")
def get_config() -> dict[str, Any]:
    rt = get_runtime()
    return {
        "draft_model_path": rt.config.draft_model_path,
        "context_size": rt.config.context_size,
        "gpu_layers": rt.config.gpu_layers,
        "short_output_threshold": rt.config.short_output_threshold,
        "enable_global": rt.config.enable_global,
        "yaml_path": rt.yaml_path,
        "profile_count": len(rt.policy_map),
        "stats": engine.stats(),
    }


@router.get("/policies")
def get_policies() -> list[dict[str, Any]]:
    rt = get_runtime()
    out: list[dict[str, Any]] = []
    for cat, pol in sorted(rt.policy_map.items()):
        out.append({
            "task_category": cat,
            "mode": pol.mode,
            "n_draft": pol.n_draft,
            "draft_p_min": pol.draft_p_min,
            "enabled": pol.enabled,
        })
    return out


@router.get("/recent")
def get_recent(limit: int = 20) -> list[dict[str, Any]]:
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="limit must be in [1, 100]")
    items = engine.recent(limit=limit)
    return [
        {
            "ts": r.ts,
            "task_category": r.task_category,
            "speculative_enabled": r.speculative_enabled,
            "n_draft": r.n_draft,
            "draft_p_min": r.draft_p_min,
            "backend": r.backend,
            "reason": r.reason,
            "max_tokens": r.max_tokens,
        }
        for r in items
    ]


@router.post("/reload")
def reload() -> dict[str, Any]:
    rt = get_runtime()
    n = rt.reload_policies()
    return {"ok": True, "policy_count": n}


class DraftModelPathBody(BaseModel):
    """POST /dspark/draft-model-path body"""

    path: str | None = Field(
        default=None,
        description="草稿模型 GGUF 绝对路径；null/空串 = 清空（全局禁用 DSpark）",
    )


@router.post("/draft-model-path")
async def set_draft_model_path(body: DraftModelPathBody) -> dict[str, Any]:
    """设置草稿模型路径（运行时生效 + 持久化到 dspark.json + 审计落库）。

    V0 不校验文件存在 —— V1 接 llama.cpp 时才报错。空串 / None 等价于禁用 DSpark。
    """
    rt = get_runtime()
    old_path = rt.config.draft_model_path
    new_path = body.path if body.path else None
    rt.set_draft_model_path(new_path)
    _save_dspark_config(rt.config)
    # 审计：actor_type='system'（UI 自动发起，不附 user_id）；event_type 与设计文档一致
    await audit_log(
        "dspark_config_change",
        {
            "actor_type": "system",
            "event_type": "dspark_draft_model_change",
            "changed_fields": ["draft_model_path"],
            "old": {"draft_model_path": old_path},
            "new": {"draft_model_path": rt.config.draft_model_path},
            "persisted_to": str(_dspark_persist_path()),
        },
        db_path=_audit_db_path(),
    )
    return {
        "ok": True,
        "draft_model_path": rt.config.draft_model_path,
        "persisted_to": str(_dspark_persist_path()),
    }


# ---- POST /config: 全量配置更新 ----------------------------------------------


class DSparkConfigUpdateBody(BaseModel):
    """POST /dspark/config body —— 所有字段可选，只更新传了非 null 的字段。"""

    draft_model_path: str | None = Field(default=None)
    context_size: int | None = Field(default=None, ge=512, le=262144)
    gpu_layers: int | None = Field(default=None, ge=-1, le=999)
    enable_global: bool | None = Field(default=None)
    short_output_threshold: int | None = Field(default=None, ge=1)


@router.post("/config")
async def update_config(body: DSparkConfigUpdateBody) -> dict[str, Any]:
    """更新 DSpark 运行时配置 + 持久化到 dspark.json + 审计落库。

    只更新 body 中非 None 的字段；至少需要传一个字段。
    """
    rt = get_runtime()
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="no fields to update")
    # 记录变更前快照（只关心被更新的字段）
    old_snapshot = {k: getattr(rt.config, k) for k in updates.keys()}
    rt.update_config(updates)
    new_snapshot = {k: getattr(rt.config, k) for k in updates.keys()}
    _save_dspark_config(rt.config)
    await audit_log(
        "dspark_config_change",
        {
            "actor_type": "system",
            "event_type": "dspark_config_change",
            "changed_fields": list(updates.keys()),
            "old": old_snapshot,
            "new": new_snapshot,
            "persisted_to": str(_dspark_persist_path()),
        },
        db_path=_audit_db_path(),
    )
    return {"ok": True, "config": get_config()}


# ---- 决策辅助（V0 留给 router.route() 内部调用，不暴露 HTTP） -------------


def decide_for_task(task_category: str, max_tokens: int) -> tuple[SpeculativePolicy, str]:
    """对外辅助：决策 + 返回 reason 字符串（用于 metrics 记录）。

    转发到 policy._decide_dspark_with_reason —— 保证与 decide_dspark 顺序一致。
    """
    from agent.llm.dspark.policy import get_local_only_tasks
    rt = _runtime
    if rt is None:
        # runtime 未初始化 → 走 helper 的 off-no-runtime 分支
        return _decide_dspark_with_reason(
            config=None,
            task_category=task_category,
            max_tokens=max_tokens,
            policies=None,
            local_only_tasks=get_local_only_tasks(),
        )
    return _decide_dspark_with_reason(
        config=rt.config,
        task_category=task_category,
        max_tokens=max_tokens,
        policies=rt.policy_map,
        local_only_tasks=get_local_only_tasks(),
    )

