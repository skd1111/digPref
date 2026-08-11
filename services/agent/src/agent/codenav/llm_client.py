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

import json
import logging
import os
import re
from collections.abc import AsyncIterator
from typing import ClassVar

import httpx

from agent.llm.json_discipline import extract_json, parse_with_retry
from agent.llm.prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)


async def _read_backend_from_db(name: str) -> dict | None:
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


async def resolve_codenav_backend(preferred_name: str | None = None) -> dict | None:
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
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 20.0,
        max_context: int | None = None,
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
    ) -> dict | None:
        if not self.configured:
            return None
        prompt = render_prompt(
            load_prompt("codenav/infer"),
            SYMBOL=symbol,
            CURRENT_FILE=current_file,
            CONTEXT=context[:4000],
        )

        async def _call(hint: str, last: str) -> str:
            user_prompt = prompt + (f"\n\n{hint}" if hint else "")
            return await self._chat("", user_prompt, max_tokens=300) or ""

        raw = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
        return _coerce_infer(raw) if isinstance(raw, dict) else None

    async def explain_symbol(
        self,
        symbol: str,
        current_file: str,
        line: int,
        context: str,
        selection: tuple[int, int, str] | None = None,  # (start_line, end_line, text)
        max_tokens: int = 500,
    ) -> str | None:
        """解释符号语义；可选 `selection` 表示用户选中的代码段（自动改写 prompt）。

        selection 传入时：
          - system prompt 改成「重点围绕这段被选中的代码解释」
          - user message 拼接「用户选中的代码」+ 行号范围

        max_tokens 可调大：推理型模型（MiniMax-M3 / DeepSeek-R1 等）的 think
        段也计入输出 token，预算太小时 think 未闭合 → 正文剥离后为空。
        """
        if not self.configured:
            return None
        system, prompt = self._build_explain_prompt(symbol, current_file, line, context, selection)
        return await self._chat(system, prompt, max_tokens=max_tokens)

    def _build_explain_prompt(
        self,
        symbol: str,
        current_file: str,
        line: int,
        context: str,
        selection: tuple[int, int, str] | None = None,
    ) -> tuple[str, str]:
        """构造解释 prompt；选中代码时切换为「围绕选区解释」。"""
        if selection and len(selection) == 3:
            start_line, end_line, sel_text = selection
            selection_block = (
                f"用户选中范围 L{start_line}-L{end_line}：\n```\n{sel_text[:4000]}\n```"
            )
        else:
            selection_block = "（无）"
        user = render_prompt(
            load_prompt("codenav/explain"),
            SYMBOL=symbol,
            CURRENT_FILE=current_file,
            LINE=str(line),
            CONTEXT=context[:4000],
            SELECTION_BLOCK=selection_block,
        )
        return "", user

    async def explain_symbol_stream(
        self,
        symbol: str,
        current_file: str,
        line: int,
        context: str,
        selection: tuple[int, int, str] | None = None,  # (start_line, end_line, text)
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        """流式解释符号语义：逐块 yield 清洗后的正文（自动剥离 think 推理内容）。

        与 explain_symbol 共用 prompt 构造；仅在 HTTP 层开启 stream 并做增量过滤。
        未配置 LLM 时 yield 空（调用方走 mock 兜底）。

        max_tokens 默认 1024（而非 500）：推理型模型的 think 段也计入输出
        token，预算太小时 think 被截断未闭合 → 正文剥离后为空（表现为
        "成功但无解释内容"）。
        """
        if not self.configured:
            return
        system, prompt = self._build_explain_prompt(symbol, current_file, line, context, selection)
        truncated_user = self._truncate_context(system, prompt, max_tokens=max_tokens)
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": truncated_user},
            ],
            "temperature": 0.2,
            "max_tokens": max_tokens,
            "stream": True,
        }
        think_filter = _ThinkStreamFilter()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as client:
                async with client.stream("POST", url, json=payload, headers=headers) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        delta = (choices[0].get("delta") or {}).get("content")
                        if not delta:
                            continue
                        cleaned = think_filter.feed(delta)
                        if cleaned:
                            yield cleaned
        except Exception as e:
            logger.warning("codenav explain stream call failed: %s", e)
            return
        tail = think_filter.flush()
        if tail:
            yield tail

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
        return f"{head}\n\n[…已截断 {omitted} 字符以适配 {self.max_context} tokens 窗口…]\n\n{tail}"

    async def _chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 500,
        temperature: float = 0.2,
    ) -> str | None:
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
        return strip_think(msg.get("content") or "")


# ---- JSON 容错 ------------------------------------------------------------


def strip_think(text: str) -> str:
    """剥离模型输出中的 think/推理内容，只保留正文。

    兼容两种常见格式（大小写不敏感）：
      - DeepSeek-R1 风格：<think>...</think>
      - Markdown 风格：```think ... ```
    尾部未闭合的 think 块（流式截断残留）一并丢弃。
    """
    if not text:
        return text
    cleaned = _THINK_BLOCK_RE.sub("", text)
    # 尾部未闭合块：从最后一个未闭合的开启标记处截断（大小写不敏感）
    lowered = cleaned.lower()
    for marker in ("<think", "```think"):
        idx = lowered.rfind(marker)
        if idx != -1:
            cleaned = cleaned[:idx]
    return cleaned.strip()


_THINK_BLOCK_RE = re.compile(
    r"<think>.*?</think>|```think\s*.*?```",
    re.DOTALL | re.IGNORECASE,
)


class _ThinkStreamFilter:
    """流式 think 剥离状态机：逐块过滤，只吐出非思考内容。

    处理 chunk 被切在标签中间的情况：未闭合时保留少量尾部缓冲，
    等闭合标记凑齐后再继续输出；大小写变体同样识别。
    """

    _OPENERS = ("<think", "```think")
    _CLOSERS: ClassVar[dict[str, str]] = {"<think": "</think>", "```think": "```"}

    def __init__(self) -> None:
        self._buf = ""
        self._in_think = False
        self._close_tag = ""

    def feed(self, chunk: str) -> str:
        self._buf += chunk
        out: list[str] = []
        while True:
            if not self._in_think:
                pos = -1
                tag = ""
                lowered = self._buf.lower()
                for t in self._OPENERS:
                    p = lowered.find(t)
                    if p != -1 and (pos == -1 or p < pos):
                        pos, tag = p, t
                if pos == -1:
                    # 缓冲尾部可能是被切开的开启标记前缀 → 暂缓输出，等下一块凑齐
                    if self._has_partial_opener(lowered):
                        break
                    out.append(self._buf)
                    self._buf = ""
                    break
                out.append(self._buf[:pos])
                self._buf = self._buf[pos + len(tag) :]
                self._in_think = True
                self._close_tag = self._CLOSERS[tag]
            else:
                pos = self._buf.lower().find(self._close_tag)
                if pos == -1:
                    # 仍处于思考段：整段丢弃；保留少量尾部以便跨 chunk 识别闭合标记
                    keep = min(len(self._buf), len(self._close_tag) + 4)
                    self._buf = self._buf[-keep:]
                    break
                self._buf = self._buf[pos + len(self._close_tag) :]
                self._in_think = False
        return "".join(out)

    @staticmethod
    def _has_partial_opener(lowered: str) -> bool:
        """判断缓冲尾部是否是某个开启标记的前缀（标签被切块时兜底）。"""
        for t in ("<think", "```think"):
            for i in range(1, len(t)):
                if lowered.endswith(t[:i]):
                    return True
        return False

    def flush(self) -> str:
        """流结束：未闭合的思考尾巴直接丢弃，其余缓冲作为正文输出。"""
        if self._in_think:
            return ""
        return self._buf


def _parse_infer_json(content: str) -> dict | None:
    """共享容错解析：围栏/think/前后缀（spec §4.5），兼容既有调用。"""
    data = extract_json(content, want="object")
    if not isinstance(data, dict):
        logger.info("codenav llm returned unparseable content: %r", str(content)[:200])
        return None
    return _coerce_infer(data)


def _coerce_infer(raw: dict) -> dict | None:
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


def _sync_read_bound_backend() -> dict | None:
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
            cur = conn.execute("SELECT backend_name FROM feature_backend WHERE feature='codenav'")
            row = cur.fetchone()
        finally:
            conn.close()
        if row and row[0]:
            return {"backend_name": row[0]}
        return None
    except Exception:
        return None


def _build_client_from_row(row) -> CodenavLLMClient | None:
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


def _sync_read_first_enabled_backend() -> tuple | None:
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
