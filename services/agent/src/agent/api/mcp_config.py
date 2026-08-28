"""MCP 服务器配置管理端点 —— 设置页「MCP」面板的后端。

管理全局 `mcp.yaml`（stdio transport 注册表，与 `infra/config/mcp.example.yaml`
同构）：

    GET  /mcp-config        → 读取当前注册表
    PUT  /mcp-config        → 整表覆盖保存（校验 + 原子写盘）
    POST /mcp-config/test   → 对单个 server 做真实 stdio 握手 + list_tools
    POST /mcp-config/reload → 重读 mcp.yaml 并重建运行中的 MCP 连接

安全约束：
    - 配置里的敏感值只允许 `__KEYRING_REF:<account>__` 占位符（真实密钥
      经 OS keychain 注入），保存时若发现 env 值疑似明文密钥则硬拒。
    - server 名 / command 做白名单式校验，防止注入任意命令。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from mcp import StdioServerParameters
from pydantic import BaseModel, Field, field_validator

from agent.audit.store import audit
from agent.config import settings
from agent.mcp.registry import ServerRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/mcp-config", tags=["mcp-config"])

_NAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")

# env 值疑似明文密钥的启发式（与 envconfig/export._looks_like_plaintext_secret 对齐）
_SECRET_KEY_HINTS = ("key", "token", "secret", "password", "credential")
# 占位符前缀（与 envconfig.scrub.PLACEHOLDER_PREFIX 一致）
_KEYRING_REF_PREFIX = "__KEYRING_REF:"

# 连通性测试硬超时（秒）—— 进程启动 + initialize + list_tools
_TEST_TIMEOUT_SEC = 20.0


class McpServerSpec(BaseModel):
    """mcp.yaml 中单个 server 条目（与 envconfig.McpServerEntry 同构，去掉名字字段）。"""

    command: str = Field(..., min_length=1, max_length=256)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    auto_start: bool = False
    working_dir: str | None = None

    @field_validator("args", "allowed_tools")
    @classmethod
    def _str_items(cls, v: list[str]) -> list[str]:
        for item in v:
            if not isinstance(item, str) or len(item) > 512:
                raise ValueError("列表项必须是不超过 512 字符的字符串")
        return v

    @field_validator("env")
    @classmethod
    def _env_values(cls, v: dict[str, str]) -> dict[str, str]:
        for k, val in v.items():
            if not isinstance(val, str):
                raise ValueError(f"env 值必须是字符串: {k!r}")
            lowered = k.lower()
            if any(h in lowered for h in _SECRET_KEY_HINTS) and len(val) >= 8:
                if not val.startswith(_KEYRING_REF_PREFIX):
                    raise ValueError(
                        f"env.{k} 疑似明文密钥（违反凭证红线）。"
                        f"请改用 __KEYRING_REF:<account>__ 占位符并在凭证面板绑定。"
                    )
        return v


class McpConfigSaveRequest(BaseModel):
    servers: dict[str, McpServerSpec]

    @field_validator("servers")
    @classmethod
    def _server_names(cls, v: dict[str, McpServerSpec]) -> dict[str, McpServerSpec]:
        for name in v:
            if not _NAME_RE.match(name) or len(name) > 64:
                raise ValueError(f"server 名不合法（仅允许字母/数字/. _ -，≤64 字符）: {name!r}")
        return v


class McpConfigTestRequest(McpServerSpec):
    """测试请求 = 一条 server 条目（名字可缺省）。"""

    name: str = "probe"


# ---- 读写 ------------------------------------------------------------------


def _config_path() -> str:
    return settings.mcp_config_path


def load_mcp_yaml() -> tuple[dict[str, Any], bool]:
    """读取 mcp.yaml；文件不存在返回 ({}, False)。"""
    path = _config_path()
    if not os.path.exists(path):
        return {}, False
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    servers = data.get("servers") or {}
    if not isinstance(servers, dict):
        raise HTTPException(status_code=500, detail="mcp.yaml 结构异常: servers 不是映射")
    return servers, True


def save_mcp_yaml(servers: dict[str, dict[str, Any]]) -> None:
    """原子写盘：先写临时文件再替换，避免半截 YAML。"""
    path = _config_path()
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# mcp.yaml — registered MCP servers (stdio transport)\n")
        f.write("# 由设置页「MCP」管理；手工编辑后请点「重新加载」。\n")
        yaml.safe_dump({"servers": servers}, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)


@router.get("")
async def get_mcp_config() -> dict[str, Any]:
    servers, exists = load_mcp_yaml()
    return {"path": _config_path(), "exists": exists, "servers": servers}


@router.put("")
async def put_mcp_config(body: McpConfigSaveRequest) -> dict[str, Any]:
    servers = {name: spec.model_dump(mode="json") for name, spec in body.servers.items()}
    save_mcp_yaml(servers)
    await audit("mcp.config.save", {"servers": sorted(servers.keys())})
    logger.info("mcp.yaml saved: %s", sorted(servers.keys()))
    return {"ok": True, "servers": servers}


# ---- 连通性测试 --------------------------------------------------------------


@router.post("/test")
async def test_mcp_server(body: McpConfigTestRequest) -> dict[str, Any]:
    """真实拉起目标 server 并做 MCP 握手，返回工具清单。

    与生产链路完全同构：ServerRegistry → McpClient（stdio），
    保证「测试通过 = Agent 可用」。
    """
    from agent.mcp.client import McpClient

    params = StdioServerParameters(
        command=body.command,
        args=body.args,
        env=body.env or None,
        cwd=body.working_dir,
    )
    registry = ServerRegistry({body.name: params})

    async def _probe() -> list[dict[str, Any]]:
        async with McpClient(registry) as client:
            return await client.list_tools()

    try:
        tools = await asyncio.wait_for(_probe(), timeout=_TEST_TIMEOUT_SEC)
    except TimeoutError:
        return {"ok": False, "error": f"超时（{_TEST_TIMEOUT_SEC:.0f}s）：进程可能挂起或无响应"}
    except FileNotFoundError:
        return {"ok": False, "error": f"找不到命令 {body.command!r}（不在 PATH 中？）"}
    except Exception as exc:
        logger.warning("mcp test failed for %s: %s", body.name, exc)
        return {"ok": False, "error": str(exc)}

    return {
        "ok": True,
        "tools": [
            {"name": t.get("name", ""), "description": t.get("description", "") or ""}
            for t in tools
        ],
    }


# ---- 热重载 ------------------------------------------------------------------


@router.post("/reload")
async def reload_mcp_config() -> dict[str, Any]:
    """重读 mcp.yaml 并重建运行中的 MCP 客户端连接池。

    延迟 import main，避免 api ↔ main 循环依赖。
    """
    from agent.main import reload_mcp_clients

    servers = await reload_mcp_clients()
    await audit("mcp.config.reload", {"servers": servers})
    return {"ok": True, "servers": servers}
