"""MCP 工具注册 — search_symbol / get_file_symbols / ai_infer_definition / explain_symbol。

注册为 LangGraph tool_node 的标准 Function；mcp-server（如果有）通过
stdio 调用。本文件暴露纯 Python 函数供 FastAPI /codenav/jump / explain 复用。
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from agent.codenav.llm_client import (
    CodenavLLMClient,
    build_client_from_config,
    get_default_client,
    resolve_codenav_backend,
)
from agent.codenav.models import JumpResult, Symbol
from agent.codenav.query import SymbolQuery

logger = logging.getLogger(__name__)


def _default_db_path() -> str:
    """默认 SQLite 路径：%APPDATA%/eaide/workspace_index.db（Windows）"""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "eaide", "workspace_index.db")
    return os.path.expanduser("~/.eaide/workspace_index.db")


def _get_query() -> SymbolQuery:
    db = os.environ.get("EAIDE_WORKSPACE_INDEX_DB", _default_db_path())
    return SymbolQuery(db)


# ---------------------------------------------------------------------------
# MCP-style tool functions
# ---------------------------------------------------------------------------

async def search_symbol(name: str, kind: Optional[str] = None, limit: int = 10) -> list[dict]:
    """精确/模糊查询 SQLite 符号库（< 50ms）。"""
    q = _get_query()
    return [s.__dict__ for s in q.search(name, kind=kind, limit=limit)]


async def get_file_symbols(file_path: str) -> list[dict]:
    """列出文件所有符号。"""
    q = _get_query()
    return [s.__dict__ for s in q.get_file_symbols(file_path)]


async def ai_infer_definition(
    symbol: str,
    current_file: str,
    context: str,
    llm_client: CodenavLLMClient | None = None,
) -> dict:
    """LLM 语义推断——仅当 search_symbol 返回空时调用。

    优先级：注入 client > router.db.feature_backend('codenav') > 环境变量 > mock
    """
    if llm_client is None:
        cfg = await resolve_codenav_backend()
        if cfg:
            llm_client = build_client_from_config(cfg)
    if llm_client and llm_client.configured:
        result = await llm_client.infer_definition(symbol, current_file, context)
        if result:
            return result
    return {
        "file": current_file,
        "line": 1,
        "confidence": 0.0,
        "reasoning": "no LLM configured (mock)",
    }


async def explain_symbol(
    symbol: str,
    current_file: str,
    line: int,
    context: str,
    selection: Optional[tuple[int, int, str]] = None,  # (start_line, end_line, text)
    llm_client: CodenavLLMClient | None = None,
) -> dict:
    """解释符号语义。

    Args:
        selection: 用户从编辑器选中的范围（start_line, end_line, text）。
                   传入时改写 system prompt —— 「你正在解释用户选中的代码 (Lstart-Lend)」。

    Returns: {"text": str, "source": "llm"|"mock", "confidence": float, "backend": str|None}
    """
    import time
    t0 = time.time()
    if llm_client is None:
        # V1：先尝试同步读 feature_backend 单例（修 get_default_client 旧版 async bug）
        from agent.codenav.llm_client import get_default_client
        llm_client = get_default_client()
    if llm_client and llm_client.configured:
        text = await llm_client.explain_symbol(
            symbol=symbol,
            current_file=current_file,
            line=line,
            context=context,
            selection=selection,
        )
        latency_ms = int((time.time() - t0) * 1000)
        if text:
            _emit_log(
                "codenav.explain",
                symbol=symbol,
                model=llm_client.model,
                latency_ms=latency_ms,
                status="ok",
            )
            return {"text": text, "source": "llm", "confidence": 0.85, "backend": llm_client.model}
    # 未配置 LLM —— 简洁兜底（原文位置信息不再写出来 —— 用户已在 UI 看到）
    latency_ms = int((time.time() - t0) * 1000)
    _emit_log(
        "codenav.explain",
        symbol=symbol,
        status="mock",
        latency_ms=latency_ms,
        note="未配置 LLM —— 走 mock 兜底",
    )
    return {
        "text": "（mock）语义解释占位 —— 启用真 LLM 后将生成完整说明。",
        "source": "mock",
        "confidence": 0.0,
        "backend": None,
    }


def _emit_log(category: str, **fields) -> None:
    """发一条 agent://log SSE 事件 —— Codex/Claude 风格执行链路渲染。

    前端 Xterm 终端订阅此事件，按 category 着色。
    """
    import asyncio
    import json as _json
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # 在 FastAPI handler 里：把 emit 挂到 loop 上
            loop.create_task(_push_log(category, fields))
            return
    except RuntimeError:
        pass
    # 脚本里 / 启动阶段：直接同步发
    asyncio.run(_push_log(category, fields))


async def _push_log(category: str, fields: dict) -> None:
    """Emit codenav audit/explain log via standard logger."""
    line = f"[{category}] " + " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info(line)


# ---------------------------------------------------------------------------
# /codenav/jump 解析
# ---------------------------------------------------------------------------

def _pick_best_match(results: list[Symbol], current_file: str) -> Symbol:
    """从查询结果中挑最佳匹配：同文件 > 同目录 > 其他。"""
    def priority(s: Symbol) -> tuple[int, str]:
        if s.file_path == current_file:
            return (0, s.file_path)
        # 同目录
        if os.path.dirname(s.file_path) == os.path.dirname(current_file):
            return (1, s.file_path)
        return (2, s.file_path)
    return min(results, key=priority)


async def resolve_jump(
    symbol: str,
    current_file: str,
    context: str = "",
    llm_client: CodenavLLMClient | None = None,  # noqa: ARG001 (向后兼容)
) -> JumpResult:
    """符号跳转解析：纯 SQLite 索引（VSCode 风格），无 LLM 降级。

    未命中时返回 confidence=0、source='not_found'，前端应提示
    "未找到定义" 而非跳转到一个 LLM 幻觉的位置。
    """
    q = _get_query()
    results = q.search(symbol, limit=10)
    if results:
        best = _pick_best_match(results, current_file)
        return JumpResult(
            file_path=best.file_path,
            line=best.start_line,
            confidence=1.0,
            source="local_index",
        )
    # 未命中：不跳 —— 避免 LLM 幻觉把用户带到错的文件
    return JumpResult(
        file_path="",
        line=0,
        confidence=0.0,
        source="not_found",
        note=f"未在 SQLite 索引中找到 '{symbol}'（不再使用 LLM 推断）",
    )
