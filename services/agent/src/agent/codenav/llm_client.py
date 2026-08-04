"""Phase 2F 代码导航 LLM 客户端。

配置优先级（高 → 低）：
  1. preferred_name（直接覆盖；测试用）
  2. router.db.feature_backend(feature='codenav') → llm_backends.name
     + Windows Credential Manager 取 api_key_ref
  3. 环境变量 EAIDE_CODENAV_LLM_{BASE_URL,MODEL,API_KEY}
  4. 未配 → mock

未配置 → mock，便于离线开发。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

async def _read_backend_from_db(name: str) -> Optional[dict]:
    """从 router.db 读 backend 配置。api_key_ref 列直接存明文 key（配置文件模式）。"""
    try:
        from agent.llm.storage import get_backend
        backend = await get_backend(name)
        if not backend:
            return None
        return {
            "name": backend.name,
            "type": backend.type,
            "base_url": backend.base_url,
            "model": backend.model_name,
            "api_key": backend.api_key_ref or "",
            "max_context": backend.max_context,
        }
    except Exception as e:
        logger.warning("read backend from db failed name=%s err=%s", name, e)
        return None


async def resolve_codenav_backend(preferred_name: Optional[str] = None) -> Optional[dict]:
    """按优先级返回代码导航用的 backend 配置。"""
    if preferred_name:
        b = await _read_backend_from_db(preferred_name)
        if b and b.get("base_url") and b.get("model"):
            return b
    try:
        from agent.llm.storage import get_feature_backend
        bound = await get_feature_backend("codenav")
        if bound:
            b = await _read_backend_from_db(bound)
            if b and b.get("base_url") and b.get("model"):
                return b
    except Exception as e:
        logger.info("get_feature_backend('codenav') failed: %s", e)
    base_url = os.environ.get("EAIDE_CODENAV_LLM_BASE_URL", "")
    model = os.environ.get("EAIDE_CODENAV_LLM_MODEL", "")
    api_key = os.environ.get("EAIDE_CODENAV_LLM_API_KEY", "")
    if base_url and model:
        return {
            "name": "env",
            "type": "env",
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
            "max_context": None,  # env 路径无法知 max_context，由 client 走模型默认
        }
    return None


def build_client_from_config(cfg: dict, timeout_s: float = 20.0) -> CodenavLLMClient:
    """把 backend dict 转成 CodenavLLMClient 实例。

    `cfg['max_context']` 透传到客户端：用于截断过长的 context 字段，
    避免超过用户配置的窗口大小。
    """
    max_ctx = cfg.get("max_context")
    if max_ctx is not None:
        try:
            max_ctx = int(max_ctx)
            if max_ctx <= 0:
                max_ctx = None
        except (TypeError, ValueError):
            max_ctx = None
    return CodenavLLMClient(
        base_url=cfg.get("base_url", ""),
        model=cfg.get("model", ""),
        api_key=cfg.get("api_key", ""),
        timeout_s=timeout_s,
        max_context=max_ctx,
    )


# ---- OpenAI 兼容客户端 ------------------------------------------------------

class CodenavLLMClient:
    """OpenAI 兼容 chat completions。"""

    # OpenAI 协议没有原生 max_context 字段；客户端按 chars/token 估算，
    # 超出预算时截断 user message 里的 context 部分。
    _CHARS_PER_TOKEN = 4
    _MIN_KEEP_CHARS = 256  # 至少保留 256 chars 的 context（避免截到 0）

    def __init__(
        self,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_s: float = 20.0,
        max_context: Optional[int] = None,
    ):
        """代码导航 LLM 客户端。

        Args:
            base_url: OpenAI 兼容服务地址。
            model: 模型名。
            api_key: Bearer Token（来自 keyring 占位符解析）。
            timeout_s: HTTP 超时秒数。
            max_context: 上下文窗口大小（tokens）。None=不截断（让模型走默认窗口）。
                配置时按 chars/token=4 估算 system+user 长度，超出则截断 user.content。
                与主对话的 PrivateLLMClient 策略对齐（参见 services/agent/src/agent/llm/private_llm.py）。
        """
        self.base_url = (base_url or os.environ.get("EAIDE_CODENAV_LLM_BASE_URL", "")).rstrip("/")
        self.model = model or os.environ.get("EAIDE_CODENAV_LLM_MODEL", "")
        self.api_key = api_key or os.environ.get("EAIDE_CODENAV_LLM_API_KEY", "")
        self.timeout_s = timeout_s
        self.max_context = max_context

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.model)

    async def infer_definition(
        self,
        symbol: str,
        current_file: str,
        context: str,
    ) -> Optional[dict]:
        if not self.configured:
            return None
        prompt = _INFER_USER_TEMPLATE.format(
            symbol=symbol,
            current_file=current_file,
            context=context[:4000],
        )
        content = await self._chat(_INFER_SYSTEM, prompt, max_tokens=300)
        if not content:
            return None
        return _parse_infer_json(content)

    async def explain_symbol(
        self,
        symbol: str,
        current_file: str,
        line: int,
        context: str,
        selection: Optional[tuple[int, int, str]] = None,  # (start_line, end_line, text)
    ) -> Optional[str]:
        """解释符号语义；可选 `selection` 表示用户选中的代码段（自动改写 prompt）。

        selection 传入时：
          - system prompt 改成「重点围绕这段被选中的代码解释」
          - user message 拼接「用户选中的代码」+ 行号范围
        """
        if not self.configured:
            return None
        if selection and len(selection) == 3:
            start_line, end_line, sel_text = selection
            prompt = _EXPLAIN_USER_WITH_SELECTION_TEMPLATE.format(
                symbol=symbol,
                current_file=current_file,
                line=line,
                start_line=start_line,
                end_line=end_line,
                selection_text=sel_text[:4000],
                context=context[:4000],
            )
            return await self._chat(_EXPLAIN_SYSTEM_WITH_SELECTION, prompt, max_tokens=500)
        prompt = _EXPLAIN_USER_TEMPLATE.format(
            symbol=symbol,
            current_file=current_file,
            line=line,
            context=context[:4000],
        )
        return await self._chat(_EXPLAIN_SYSTEM, prompt, max_tokens=500)

    def _truncate_context(
        self,
        system: str,
        user: str,
        max_tokens: int,
    ) -> str:
        """按 max_context 截断 user 消息（保留 system 全文）。

        策略：system 永远保留；user 超出预算时截断尾部并加省略号标记。
        估算：chars/token = 4（中文 / 代码 / 英文混合场景的经验值）。
        """
        if self.max_context is None or self.max_context <= 0:
            return user
        # 预算 = (窗口 - 给响应的 max_tokens) × chars_per_token
        budget_tokens = max(256, self.max_context - max_tokens)
        budget_chars = budget_tokens * self._CHARS_PER_TOKEN
        system_chars = len(system)
        if system_chars >= budget_chars:
            # system 本身就超了：直接截 user 到 1 个最小可读块
            return user[: self._MIN_KEEP_CHARS]
        remaining = budget_chars - system_chars
        if len(user) <= remaining:
            return user
        # 截断 user，保留头尾（保留 symbol 上下文完整性）
        keep_each_side = max(self._MIN_KEEP_CHARS, remaining // 2)
        if keep_each_side * 2 > remaining:
            keep_each_side = remaining // 2
        if keep_each_side < self._MIN_KEEP_CHARS:
            return user[:remaining] + "\n\n[…已截断…]"
        head = user[:keep_each_side]
        tail = user[-keep_each_side:]
        omitted = len(user) - keep_each_side * 2
        return (
            f"{head}\n\n[…已截断 {omitted} 字符以适配 {self.max_context} tokens 窗口…]\n\n{tail}"
        )

    async def _chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 500,
        temperature: float = 0.2,
    ) -> Optional[str]:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # 按 max_context 截断 user 消息（保留 system 全文）
        truncated_user = self._truncate_context(system, user, max_tokens)

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": truncated_user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                r = await client.post(url, json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
        except Exception as e:
            logger.warning("codenav llm call failed: %s", e)
            return None
        choices = data.get("choices") or []
        if not choices:
            return None
        msg = choices[0].get("message") or {}
        return msg.get("content") or ""


# ---- Prompts + JSON 容错 ---------------------------------------------------

_INFER_SYSTEM = (
    "你是一个代码导航助手。根据用户提供的上下文（当前文件 + 上下文片段），"
    "推断符号最可能的定义位置。请只返回 JSON，不要返回其他文字。"
)
_INFER_USER_TEMPLATE = (
    "符号: {symbol}\n"
    "当前文件: {current_file}\n"
    "上下文（最多 4000 字符）:\n{context}\n\n"
    "请返回 JSON：{{\"file\": \"绝对路径或仓库相对路径\", \"line\": 行号, "
    "\"confidence\": 0.0-1.0, \"reasoning\": \"推断依据（<= 80 字）\"}}"
)
_EXPLAIN_SYSTEM = (
    "你是一个资深软件工程师。基于当前文件上下文，简洁地解释所给符号的用途、"
    "关键调用、注意事项。返回中文 Markdown，<= 300 字。"
)
_EXPLAIN_SYSTEM_WITH_SELECTION = (
    "你是一个资深软件工程师。用户在编辑器里选中了一段代码（行号见下方），"
    "请**重点围绕这段被选中的代码**解释其用途、关键逻辑、与周围代码的关系、"
    "潜在问题或改进点。返回中文 Markdown，<= 300 字。"
)
_EXPLAIN_USER_TEMPLATE = (
    "符号: {symbol}\n"
    "当前文件: {current_file}\n"
    "所在行: {line}\n\n"
    "上下文（最多 4000 字符）:\n{context}\n"
)
_EXPLAIN_USER_WITH_SELECTION_TEMPLATE = (
    "符号: {symbol}\n"
    "当前文件: {current_file}\n"
    "所在行: {line}\n"
    "用户选中范围: L{start_line}-L{end_line}\n\n"
    "用户选中的代码（请围绕它解释）：\n```\n{selection_text}\n```\n\n"
    "上下文（最多 4000 字符）:\n{context}\n"
)


def _parse_infer_json(content: str) -> Optional[dict]:
    content = content.strip()
    try:
        return _coerce_infer(json.loads(content))
    except json.JSONDecodeError:
        pass
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", content, re.DOTALL)
    if fence:
        try:
            return _coerce_infer(json.loads(fence.group(1)))
        except json.JSONDecodeError:
            pass
    m = re.search(r"\{[^{}]*\}", content, re.DOTALL)
    if m:
        try:
            return _coerce_infer(json.loads(m.group(0)))
        except json.JSONDecodeError:
            pass
    logger.info("codenav llm returned unparseable content: %r", content[:200])
    return None


def _coerce_infer(raw: dict) -> Optional[dict]:
    if not isinstance(raw, dict):
        return None
    file_path = raw.get("file") or raw.get("file_path") or ""
    line = raw.get("line") or 1
    try:
        line = int(line)
    except (TypeError, ValueError):
        line = 1
    confidence = raw.get("confidence") or 0.5
    try:
        confidence = float(confidence)
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.5
    return {
        "file": str(file_path),
        "line": line,
        "confidence": confidence,
        "reasoning": str(raw.get("reasoning") or "")[:200],
    }


# ---- 单例（每次重建，避免 async 嵌套陷阱） ---------------------------------

_default: CodenavLLMClient | None = None


def _sync_read_bound_backend() -> Optional[dict]:
    """同步读 router.db.feature_backend —— 绕开 asyncio 嵌套地狱。

    feature_backend 表只有 (feature, backend_name, updated_at) 三列；
    小数据量下 sync read 完全可以。LLM 真值仍走异步 resolve_codenav_backend。
    """
    import sqlite3
    try:
        from agent.config import settings
        db_path = settings.llm_router_db_path
        conn = sqlite3.connect(db_path, timeout=2)
        try:
            # 确保 feature_backend 表存在（仅创建此表，不引入 codenav schema）
            conn.execute(
                "CREATE TABLE IF NOT EXISTS feature_backend ("
                "  feature TEXT PRIMARY KEY,"
                "  backend_name TEXT,"
                "  updated_at REAL"
                ")"
            )
            conn.commit()
            cur = conn.execute(
                "SELECT backend_name FROM feature_backend WHERE feature='codenav'"
            )
            row = cur.fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return {"backend_name": row[0]}
        return None
    except Exception:
        return None


def _build_client_from_row(row) -> Optional[CodenavLLMClient]:
    """从 llm_backends 表的一行构建 client。row=(name, base_url, api_key_ref, model_name, max_context)。

    api_key_ref 列直接存明文 API Key（配置文件模式，不走系统凭据管理器）。
    """
    if not row or not row[1] or not row[3]:
        return None
    return CodenavLLMClient(
        base_url=row[1],
        api_key=row[2] or "",
        model=row[3],
        max_context=row[4] if row[4] else None,
    )


def _sync_read_first_enabled_backend() -> Optional[tuple]:
    """同步读 llm_backends 里第一个 enabled=1 且有 base_url+model 的行。"""
    import sqlite3
    try:
        from agent.config import settings
        conn = sqlite3.connect(settings.llm_router_db_path, timeout=2)
        try:
            cur = conn.execute(
                "SELECT name, base_url, api_key_ref, model_name, max_context "
                "FROM llm_backends WHERE enabled=1 AND base_url!='' AND model_name!='' "
                "ORDER BY rowid LIMIT 1"
            )
            return cur.fetchone()
        finally:
            conn.close()
    except Exception:
        return None


def get_default_client() -> CodenavLLMClient:
    """返回进程级单例 client。

    V2：自动回落第一个可用 backend —— 用户无需手动绑定。

    优先级：
      1. router.db.feature_backend('codenav') 显式绑定
      2. llm_backends 里第一个 enabled=1 的 backend（自动发现）
      3. 环境变量 EAIDE_CODENAV_LLM_*
      4. 裸 CodenavLLMClient()（mock）
    """
    global _default
    if _default is not None:
        return _default
    import sqlite3
    from agent.config import settings

    # 1) 显式绑定
    bound = _sync_read_bound_backend()
    if bound and bound.get("backend_name"):
        try:
            conn = sqlite3.connect(settings.llm_router_db_path, timeout=2)
            try:
                cur = conn.execute(
                    "SELECT name, base_url, api_key_ref, model_name, max_context "
                    "FROM llm_backends WHERE name=? AND enabled=1",
                    (bound["backend_name"],),
                )
                row = cur.fetchone()
            finally:
                conn.close()
            client = _build_client_from_row(row)
            if client:
                _default = client
                return _default
        except Exception:
            pass

    # 2) 自动发现：第一个 enabled 的 backend
    try:
        row = _sync_read_first_enabled_backend()
        client = _build_client_from_row(row)
        if client:
            _default = client
            return _default
    except Exception:
        pass

    # 3/4) 兜底：环境变量 / mock
    _default = CodenavLLMClient()
    return _default


def reset_default_client() -> None:
    """重置单例。下次 get_default_client() 会重新读 feature_backend。"""
    global _default
    _default = None
