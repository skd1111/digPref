"""ToolCatalog —— 合并 builtin + MCP 的统一工具目录。

- `summaries()`：轻量摘要（name / description / category / keywords），
  供动态工具编排器选候选工具（不可直接调用）；
- `definitions(names)`：完整定义（含 parameters JSON Schema），注册后可调用；
- `execute(name, args, state)`：执行工具，返回结果 dict；写 / 高危调用在未审批时
  返回 `awaiting_approval=True`（由循环暂停并交 hitl_gate）。
"""
from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any

from agent.builtin.models import BUILTIN_TOOL_NAMES
from agent.builtin.registry import (
    TOOL_DESCRIPTIONS,
    TOOL_RISK_LEVEL,
    get_default_registry,
)
from agent.config import settings
from agent.safety.write_detector import is_write_call


# builtin 工具的关键词（帮助编排器从摘要里选工具）
_BUILTIN_KEYWORDS: dict[str, list[str]] = {
    "read_file": ["读文件", "文件内容", "read"],
    "write_file": ["写文件", "创建文件", "write"],
    "edit_file": ["改文件", "替换", "edit"],
    "list_dir": ["列目录", "目录", "ls"],
    "grep": ["搜索", "grep", "查找文本"],
    "calculator": ["计算", "算术", "calculator"],
    "json_parse": ["json", "解析"],
    "json_format": ["json", "格式化"],
    "regex_match": ["正则", "regex", "匹配"],
    "url_parse": ["url", "链接解析"],
    "stat_file": ["文件信息", "元数据", "stat"],
    "mkdir": ["创建目录", "mkdir"],
    "delete_file": ["删除", "delete"],
    "move_file": ["移动", "重命名", "move"],
    "find": ["查找文件", "find"],
    "glob": ["glob", "通配"],
    "hash": ["哈希", "md5", "sha256"],
    "base64": ["base64", "编码", "解码"],
    "shell": ["命令", "shell", "执行命令"],
    "datetime_now": ["时间", "日期", "现在", "当前时间"],
    "uuid4": ["uuid", "唯一标识"],
    "http_get": ["http", "get", "接口", "api", "网页"],
    "csv_parse": ["csv", "表格", "解析"],
    "text_split": ["切分", "分段", "长文本"],
    "http_post": ["post", "提交", "写接口", "调用接口", "创建", "api"],
    "git_status": ["git", "状态", "变更", "工作区"],
    "git_diff": ["git", "diff", "差异", "改动"],
    "git_log": ["git", "提交历史", "log", "记录"],
    "git_commit": ["git", "提交", "commit", "提交代码"],
    "symbol_search": ["符号", "函数", "类", "symbol", "代码导航", "定义"],
    "file_symbols": ["文件符号", "symbol", "大纲", "结构"],
    "biznav_features": ["功能点", "业务导航", "biznav", "业务功能"],
}


# MCP 工具关键词提取：从工具名 + 描述中切出候选词（编排器选工具靠 keywords 命中）。
_MCP_KEYWORD_STOPWORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "for", "with", "from", "into", "this", "that",
    "tool", "tools", "use", "using", "can", "will", "via", "based", "given",
    "工具", "使用", "调用", "通过", "进行", "一个", "或者", "以及", "支持",
})


def _extract_mcp_keywords(name: str, description: str) -> list[str]:
    """从 MCP 工具名与描述提取轻量关键词（去重，最多 8 个）。

    规则：工具名按 . / _ / - 切块全部保留；描述取长度 ≥ 2 的中文连续段
    与长度 ≥ 3 的英文单词，过滤停用词。
    """
    out: list[str] = []
    seen: set[str] = set()

    def _push(word: str) -> None:
        w = word.strip().lower()
        if len(w) < 2 or w in _MCP_KEYWORD_STOPWORDS or w in seen:
            return
        seen.add(w)
        out.append(w)

    for part in re.split(r"[._\-]+", name or ""):
        _push(part)
    for cn in re.findall(r"[\u4e00-\u9fff]{2,}", description or ""):
        _push(cn)
    for en in re.findall(r"[A-Za-z]{3,}", description or ""):
        _push(en)
    return out[:8]


class ToolCatalog:
    """builtin + MCP 统一工具目录。"""

    def __init__(self, mcp: Any | None = None, builtin_registry: Any | None = None) -> None:
        self._mcp = mcp
        self._registry = builtin_registry or get_default_registry()
        self._mcp_specs: tuple | None = None
        self._mcp_lock = asyncio.Lock()

    # ---- 摘要 / 定义 ------------------------------------------------------

    async def summaries(self) -> list[dict]:
        """轻量工具摘要（不包含参数定义，不可直接调用）。"""
        out: list[dict] = []
        for name in BUILTIN_TOOL_NAMES:
            out.append({
                "name": name,
                "description": TOOL_DESCRIPTIONS.get(name, ""),
                "category": "builtin",
                "keywords": _BUILTIN_KEYWORDS.get(name, []),
            })
        for spec in await self._get_mcp_specs():
            server = str(spec.get("server") or "mcp")
            name = str(spec.get("name") or "")
            if not name:
                continue
            description = str(spec.get("description") or "")
            out.append({
                "name": f"{server}.{name}",
                "description": description,
                "category": server,
                "keywords": _extract_mcp_keywords(f"{server}.{name}", description),
            })
        return out

    async def definitions(self, names: list[str] | None = None) -> list[dict]:
        """完整工具定义（含 parameters schema）。names=None 表示全量。"""
        want = set(names) if names is not None else None
        out: list[dict] = []
        for name in BUILTIN_TOOL_NAMES:
            if want is not None and name not in want:
                continue
            schema = self._registry.schema(name)
            if schema is None:
                continue
            out.append({
                "name": name,
                "description": TOOL_DESCRIPTIONS.get(name, ""),
                "parameters": schema,
                "server": "builtin",
                "risk": TOOL_RISK_LEVEL.get(name, "read"),
            })
        for spec in await self._get_mcp_specs():
            server = str(spec.get("server") or "mcp")
            name = str(spec.get("name") or "")
            full = f"{server}.{name}"
            if want is not None and full not in want:
                continue
            out.append({
                "name": full,
                "description": str(spec.get("description") or ""),
                "parameters": spec.get("inputSchema") or {"type": "object", "properties": {}},
                "server": server,
                "risk": "read",
            })
        return out

    # ---- 执行 --------------------------------------------------------------

    async def execute(self, name: str, args: dict, state: dict) -> dict:
        """执行工具；写 / 高危未审批时返回 awaiting_approval=True。"""
        if name in BUILTIN_TOOL_NAMES:
            return await self._execute_builtin(name, args, state)
        return await self._execute_mcp(name, args, state)

    async def _execute_builtin(self, name: str, args: dict, state: dict) -> dict:
        from agent.builtin.dispatcher import dispatcher

        call = {
            "server": "builtin",
            "name": name,
            "args": args,
            "call_id": uuid.uuid4().hex,
        }
        delta = await dispatcher().dispatch(call, state)
        if delta is None:
            return {"name": name, "ok": False, "error": "builtin_dispatch_none"}
        if delta.get("awaiting_approval"):
            return {
                "awaiting_approval": True,
                "pending_tool_call": delta.get("pending_tool_call") or call,
            }
        return _normalise_dispatcher_result(name, delta)

    async def _execute_mcp(self, name: str, args: dict, state: dict) -> dict:
        specs = await self._get_mcp_specs()
        match = next(
            (
                s for s in specs
                if f"{s.get('server')}.{s.get('name')}" == name
            ),
            None,
        )
        if match is None or self._mcp is None:
            return {"name": name, "ok": False, "error": "unknown_tool"}
        call = {
            "server": str(match["server"]),
            "name": str(match["name"]),
            "args": args,
            "call_id": uuid.uuid4().hex,
        }
        needs_hitl = is_write_call(call)
        approved = state.get("approval_decision") == "approve"
        if needs_hitl and not approved:
            return {
                "awaiting_approval": True,
                "pending_tool_call": call,
            }
        try:
            result = await self._mcp.invoke(
                call,
                timeout_sec=settings.tool_timeout_sec,
                row_limit=settings.row_limit,
            )
        except Exception as exc:  # noqa: BLE001
            return {"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if isinstance(result, dict):
            return {
                "name": name,
                "ok": bool(result.get("ok", True)),
                "result": result,
                "error": result.get("error") if result.get("ok") is False else None,
            }
        return {"name": name, "ok": True, "result": result}

    # ---- 内部 --------------------------------------------------------------

    async def _get_mcp_specs(self) -> tuple:
        if self._mcp is None:
            return ()
        if self._mcp_specs is not None:
            return self._mcp_specs
        async with self._mcp_lock:
            if self._mcp_specs is not None:
                return self._mcp_specs
            try:
                specs = await self._mcp.list_tools()
            except Exception:  # noqa: BLE001
                return ()
            self._mcp_specs = tuple(specs)
        return self._mcp_specs


def _normalise_dispatcher_result(name: str, delta: dict) -> dict:
    """把 builtin dispatcher 的状态增量归一化成统一结果 dict。"""
    tool_result = delta.get("tool_result")
    if isinstance(tool_result, dict):
        return {
            "name": name,
            "ok": bool(tool_result.get("ok", True)),
            "result": tool_result.get("content"),
            "error": tool_result.get("error"),
            "meta": tool_result.get("meta") or {},
            "risk_level": tool_result.get("risk_level", "read"),
        }
    tool_error = delta.get("tool_error")
    if tool_error:
        return {"name": name, "ok": False, "error": str(tool_error)}
    return {"name": name, "ok": False, "error": "empty_dispatcher_result"}
