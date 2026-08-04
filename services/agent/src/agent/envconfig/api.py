"""envconfig.api —— FastAPI 路由层。

暴露给 Tauri 桌面端 + 直接 HTTP 调用的接口：
    GET  /envconfig/                    → 所有环境（来自 index.json）
    GET  /envconfig/{env}               → 单个环境的脱敏配置（占位符）
    POST /envconfig/{env}               → 保存（带 scrub 防御）
    POST /envconfig/{env}/activate      → 标记 active
    DELETE /envconfig/{env}             → 删除（不删 keychain）
    POST /envconfig/export              → 加密导出（multipart 或 JSON body）
    POST /envconfig/import              → 导入（返回 placeholders 让 UI 提示用户去绑）

**重要**：本路由绝不返回明文密钥。所有敏感字段都是 `__KEYRING_REF:...__` 占位符。
    真正注入明文是 Rust 端在 Tauri command 里查 keychain 后塞进配置对象的过程。

2026-07-09 变更：环境名不再限于 4 项 preset，支持自由命名。
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException

from pydantic import BaseModel

from .export import export_configs, import_configs
from .models import EnvConfig, Environment
from .scrub import PlaceholderMissing
from .storage import (
    delete_env,
    get_active_env,
    list_envs,
    load_env,
    load_env_or_default,
    save_env,
    set_active_env,
)


# 与 models._ENV_PATTERN 保持同步。直接构造 `Environment(name)` 不会触发 Pydantic
# 校验，所以这里手动校验一次，URL 路径里一旦给了非法格式就立刻 400。
_ENV_PATTERN = re.compile(r"^[^\W\d][\w.\-]{0,62}$", re.UNICODE)


router = APIRouter(prefix="/envconfig", tags=["envconfig"])


# ---- 响应模型 --------------------------------------------------------------


class EnvListItem(BaseModel):
    environment: str
    label: str
    description: str
    active: bool
    updated_at: str
    configured: bool  # 是否已有保存的配置（preset 还没编辑 = false）


class EnvListResponse(BaseModel):
    active: str | None
    environments: list[EnvListItem]


class ImportRequest(BaseModel):
    """导入时把密文 base64 / passphrase 一起传过来。"""

    passphrase: str
    ciphertext_base64: str
    plaintext_ok: bool = False


class ImportResponse(BaseModel):
    env_count: int
    placeholders: list[str]
    environments: list[dict[str, Any]]  # 脱敏后的 dict 列表


class ExportRequest(BaseModel):
    passphrase: str
    environments: list[str]  # 要导出的 env 名列表


class ExportResponse(BaseModel):
    ciphertext_base64: str
    env_count: int
    placeholder_count: int
    plaintext_bytes: int
    ciphertext_bytes: int


def _parse_env_name(name: str) -> Environment:
    """验证 env 名格式；不合法抛 400。

    不再有「未知 env 名 404」——任何合法格式的 env 名都可使用。
    """
    if not _ENV_PATTERN.match(name):
        raise HTTPException(
            400,
            f"环境名非法：必须匹配 ^[a-z][a-z0-9._-]{{0,62}}$，got {name!r}",
        )
    return Environment(name)


# ---- 列表 / 详情 / 增删 ---------------------------------------------------
# 注意：/export 和 /import 必须放在 /{env_name} 之前 —— FastAPI 路由匹配按声明顺序，
# 否则 /export 会被 /{env_name} 拦截成"未知环境 export" 404。


@router.get("/", response_model=EnvListResponse)
def api_list() -> EnvListResponse:
    entries = list_envs()
    active = get_active_env()
    return EnvListResponse(
        active=active.environment if active else None,
        environments=[
            EnvListItem(
                environment=e.environment,
                label=e.label,
                description=e.description,
                active=e.active,
                updated_at=e.updated_at,
                configured=_env_has_content(e.environment),
            )
            for e in entries
        ],
    )


# ---- 导入 / 导出（先于 /{env_name} 注册）----------------------------------


@router.post("/export", response_model=ExportResponse)
def api_export(req: ExportRequest) -> ExportResponse:
    if not req.environments:
        raise HTTPException(400, "environments 不能为空")
    configs: list[EnvConfig] = []
    for name in req.environments:
        env = _parse_env_name(name)
        try:
            configs.append(load_env(env))
        except FileNotFoundError:
            raise HTTPException(404, f"环境 {name} 未配置")

    with tempfile.NamedTemporaryFile(suffix=".eae", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        res = export_configs(configs, tmp_path, req.passphrase)
        blob = tmp_path.read_bytes()
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    import base64 as _b64

    return ExportResponse(
        ciphertext_base64=_b64.b64encode(blob).decode("ascii"),
        env_count=res.env_count,
        placeholder_count=res.placeholder_count,
        plaintext_bytes=res.plaintext_bytes,
        ciphertext_bytes=res.ciphertext_bytes,
    )


@router.post("/import", response_model=ImportResponse)
def api_import(req: ImportRequest) -> ImportResponse:
    import base64 as _b64

    try:
        blob = _b64.b64decode(req.ciphertext_base64)
    except Exception as e:
        raise HTTPException(400, f"ciphertext_base64 解码失败: {e}")
    with tempfile.NamedTemporaryFile(suffix=".eae", delete=False) as tmp:
        tmp.write(blob)
        tmp_path = Path(tmp.name)
    try:
        try:
            result = import_configs(
                tmp_path, req.passphrase, plaintext_ok=req.plaintext_ok
            )
        except PlaceholderMissing as e:
            raise HTTPException(400, str(e))
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(400, str(e))
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass

    return ImportResponse(
        env_count=result.env_count,
        placeholders=result.placeholders,
        environments=[
            {
                "environment": c.environment,
                "label": c.label,
                "description": c.description,
                "databases": [_db_to_dict(d) for d in c.databases],
                "api_gateways": [_api_to_dict(a) for a in c.api_gateways],
                "mcp_servers": [m.model_dump(mode="json") for m in c.mcp_servers],
                "target_servers": [t.model_dump(mode="json") for t in c.target_servers],
            }
            for c in result.configs
        ],
    )


# ---- /{env_name} 系列（再向后，避免被 export/import 误吞） ----------------


@router.get("/{env_name}")
def api_get(env_name: str) -> dict[str, Any]:
    env = _parse_env_name(env_name)
    # 即使 env 没配置过也返空配置 —— UI 可以直接进入编辑
    cfg = load_env_or_default(env)
    return {
        "environment": str(cfg.environment),
        "label": cfg.label,
        "description": cfg.description,
        "databases": [_db_to_dict(d) for d in cfg.databases],
        "api_gateways": [_api_to_dict(a) for a in cfg.api_gateways],
        "mcp_servers": [m.model_dump(mode="json") for m in cfg.mcp_servers],
        "target_servers": [t.model_dump(mode="json") for t in cfg.target_servers],
    }


@router.post("/{env_name}")
def api_save(env_name: str, body: dict[str, Any] = Body(...)) -> dict[str, Any]:
    env = _parse_env_name(env_name)
    if body.get("environment") != str(env):
        raise HTTPException(400, "URL 环境与 body.environment 不一致")
    try:
        cfg = EnvConfig.model_validate(body)
    except Exception as e:
        raise HTTPException(400, f"配置校验失败: {e}")
    save_env(cfg)
    return {"ok": True, "environment": str(env)}


@router.post("/{env_name}/activate")
def api_activate(env_name: str) -> dict[str, Any]:
    env = _parse_env_name(env_name)
    set_active_env(env)
    return {"ok": True, "active": str(env)}


@router.delete("/{env_name}")
def api_delete(env_name: str) -> dict[str, Any]:
    env = _parse_env_name(env_name)
    removed = delete_env(env)
    return {"ok": True, "removed": removed}


# ---- helpers --------------------------------------------------------------


def _db_to_dict(d) -> dict[str, Any]:
    out = d.model_dump(mode="json")
    # password 已经被 SecretStr 渲染为 "**********" —— 仍可作为占位符
    return out


def _api_to_dict(a) -> dict[str, Any]:
    out = a.model_dump(mode="json")
    return out


def _env_has_content(env: Environment) -> bool:
    """判断 env 是否被编辑过 —— 数据库/API/MCP/目标服务器任一项非空就算。

    旧代码用 env_file(env).exists() 判断；单文件方案下不再分文件，
    改成"条目是否非空"。这条规则对用户行为没影响：preset 刚 seed 时
    都是空数组；用户编辑后才会有内容。
    """
    try:
        cfg = load_env(env)
    except FileNotFoundError:
        return False
    return bool(
        cfg.databases or cfg.api_gateways or cfg.mcp_servers or cfg.target_servers
    )
