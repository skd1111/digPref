"""内网 LLM 客户端 —— 兼容 OpenAI Chat Completions 协议。

用于复杂任务（规划、汇总），与 OllamaClient 保持相同的方法签名。
配置缺失时 Router 自动回退到 Ollama。

内网模型特征（DeepSeek-RD-Llama-70B-Int8）：
    - 在 JSON 答案之前会先输出 `<THINK>…</THINK>` 思维块
    - _strip_think() 在 JSON 解析前将其剥离
    - 正则同时匹配大小写变体（防御性编程）

设计参考：
    - OpenAI Codex CLI 的沙箱执行模式：所有 LLM 调用都有超时保护
    - VSCode 扩展系统的分层设计：清晰的接口边界，便于替换后端
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx

from agent.dual.prompt_loader import FINAL_ANSWER_STYLE
from agent.llm.json_discipline import RETRY_PROMPT, extract_json
from agent.llm.prompts import (
    current_time_text,
    format_history_brief,
    load_prompt,
    normalize_message,
)
from agent.llm.stream_utils import ThinkBlockFilter
from agent.llm.token_usage import record_openai_usage
from agent.llm.types import Intent

logger = logging.getLogger(__name__)

# ---- 思考块剥离 ----------------------------------------------------------------
# 同时匹配大小写变体：文档说大写，但防御性编程不假设格式。
_THINK_BLOCK_RE = re.compile(r"<\s*THINK\s*>.*?<\s*/\s*THINK\s*>", re.IGNORECASE | re.DOTALL)


def _strip_think(text: str) -> str:
    """剥离 LLM 响应中的 <THINK>…</THINK> 块，并去除 JSON 前的非 JSON 文本。

    处理以下场景：
    1. <THINK>…</THINK>{"intent":"query"}      → {"intent":"query"}
    2. Some text <THINK>…</THINK>{"intent":"query"} → {"intent":"query"}
    3. <THINK>…</THINK>                           → ""（纯思考，无 JSON）
    """
    if not text:
        return text
    # 第一步：剥离思考块
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    # 第二步：如果剥离后仍有非 JSON 前言（"Some text {"），
    # 从第一个 { 或 [ 的位置截断
    if cleaned:
        brace = cleaned.find("{")
        bracket = cleaned.find("[")
        start = 0
        if brace >= 0 and bracket >= 0:
            start = min(brace, bracket)
        elif brace >= 0:
            start = brace
        elif bracket >= 0:
            start = bracket
        if start > 0:
            cleaned = cleaned[start:]
    return cleaned.strip()


def _raise_http_with_body(r: httpx.Response) -> None:
    """raise_for_status + 把服务端错误正文带进异常信息（BUGFIX #137）。

    云端 4xx 的响应体（如 MiniMax 400 回的 220 字节 JSON，含上下文超限/
    参数非法等具体原因）是排障唯一线索；只留 'Client error 400 Bad Request'
    会让日志与用户可见终答都变黑盒（2026-08-25 实测：工具循环在工具成功后
    因下一轮 LLM 调用 400 整轮硬停，用户只看到一句 HTTPStatusError 无法定位）。
    """
    try:
        r.raise_for_status()
    except httpx.HTTPStatusError as exc:
        detail = (r.text or "").strip()[:500]
        logger.error("LLM HTTP %s from %s: %s", r.status_code, r.url, detail or "(空响应体)")
        raise httpx.HTTPStatusError(
            f"{exc} | 服务端返回：{detail or '(空响应体)'}",
            request=exc.request,
            response=exc.response,
        ) from exc


# ---- 多厂商协议适配（2026-08-27，BUGFIX #159） ---------------------------------
# OpenAI 兼容 ≠ OpenAI 相同：各家对 OpenAI 专属参数的容忍度不一，
# 400 时按响应体关键字做一次性参数适配重试，避免整个任务因参数名差异硬失败。

# 429 同后端退避重试的等待上限（秒）：超过则直接上抛切降级链，避免长尾阻塞。
_RETRY_AFTER_CAP_S = 3.0


def _unsupported_param(body_text: str, payload: dict) -> str | None:
    """从 400 响应体识别是哪个参数不被后端支持（关键字匹配，大小写不敏感）。"""
    lowered = (body_text or "").lower()
    if "response_format" in lowered or "json_schema" in lowered:
        if "response_format" in payload:
            return "response_format"
    if "max_completion_tokens" in lowered or "max_tokens" in lowered:
        if "max_tokens" in payload:
            return "max_tokens"
    return None


def _retry_after_seconds(r: httpx.Response) -> float | None:
    """解析 429 的 Retry-After 头；缺失 / 非法 / 超上限返回 None。"""
    raw = (r.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        wait = float(raw)
    except ValueError:
        return None
    if wait < 0 or wait > _RETRY_AFTER_CAP_S:
        return None
    return wait


# ---- PrivateLLMClient ---------------------------------------------------------


class PrivateLLMClient:
    """内网企业 LLM 异步 HTTP 客户端。

    实现与 OllamaClient 完全相同的方法签名：
        - classify_intent(text) → Intent
        - plan(intent, user_prompt, history, tool_specs) → (list[dict], str)
        - repair_call(original, error, history) → dict
        - summarise(intent, user_prompt, plan, results) → (str, list[str])

    内部使用 OpenAI 兼容的 /chat/completions 端点。
    解析失败时优雅降级（返回安全的默认值），不抛异常。
    """

    # OpenAI 协议本身没有 max_context 字段；客户端在发请求前主动按
    # `max_context` 截断 history（OpenAI 风格: ~4 chars/token 估算）。
    # 历史超长时保留 system + 最近若干轮 user/assistant。
    _CHARS_PER_TOKEN = 4
    _HEADROOM_FOR_RESPONSE = 1024  # 给生成结果留 1K tokens 余量

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_context: int | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        """内网 LLM 客户端初始化。

        Args:
            base_url: 内网 LLM 网关（OpenAI 兼容协议）。
            api_key: Bearer Token（来自 keyring 占位符解析）。
            model: 模型名（如 'DeepSeek-RD-Llama-70B-Int8'）。
            max_context: 上下文窗口大小（tokens）。None=不截断 history。
                配置时按 `chars_per_token=4` 估算 message 长度，超长 history 从中部丢消息。
            max_output_tokens: 全局输出上限（gen_limits 两级回退）。配置时每次
                chat 请求注入 `max_tokens`，限制一次生成的最大 token 数防过度输出。
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_context = max_context
        self.max_output_tokens = max_output_tokens
        # Token 计量标签：内网后端 "private"；云端客户端由 _build_cloud_client 改为 "cloud"
        self.usage_label = "private"
        # 延迟创建客户端（避免 import 时产生副作用）
        self._client: httpx.AsyncClient | None = None

    def _truncate_history(self, messages: list[dict]) -> list[dict]:
        """按 max_context 截断 history：保留 system + 最近若干 user/assistant 轮。

        OpenAI 协议没有原生 max_context 字段；客户端层主动裁剪。
        裁剪策略：system 永远保留；从尾部向前累加 user/assistant，超出预算则停止。
        """
        if self.max_context is None or self.max_context <= 0:
            return messages
        budget_tokens = max(256, self.max_context - self._HEADROOM_FOR_RESPONSE)
        budget_chars = budget_tokens * self._CHARS_PER_TOKEN

        # 分离 system 与非 system
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]
        # system 单独算预算
        system_chars = sum(len(str(m.get("content", ""))) for m in system_msgs)
        if system_chars >= budget_chars:
            # system 本身就超了：截 system 末尾
            keep_chars = max(512, budget_chars // 2)
            truncated = [
                {**m, "content": str(m.get("content", ""))[-keep_chars:]} for m in system_msgs
            ]
            return truncated

        # 留 budget 给非 system 消息；从尾部向前累加
        remaining = budget_chars - system_chars
        kept: list[dict] = []
        for m in reversed(non_system):
            content_len = len(str(m.get("content", "")))
            if content_len > remaining:
                # 单条消息太长：直接截它
                if remaining > 256:
                    kept.append({**m, "content": str(m.get("content", ""))[:remaining]})
                break
            kept.append(m)
            remaining -= content_len
        kept.reverse()
        return system_msgs + kept

    def _auth_headers(self) -> dict[str, str]:
        """请求头：api_key 为空时不发 Authorization（内网免鉴权端点）。

        背景（BUGFIX #109）：无条件拼 `Bearer {api_key}`，key 为空时
        httpx 直接拒发非法头 `b'Bearer '`，本来可用的免鉴权内网后端被误判不可用。
        与 engine_api / codenav 的「key 非空才带头」约定对齐。
        """
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @property
    def client(self) -> httpx.AsyncClient:
        """获取或创建共享的 httpx 客户端（复用连接池）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(90.0, connect=10.0),
                headers=self._auth_headers(),
            )
        return self._client

    # ---- HTTP 底层 -----------------------------------------------------------

    @staticmethod
    def _adapt_payload(payload: dict[str, Any], param: str) -> dict[str, Any]:
        """400 参数适配：去掉/替换不支持的参数后生成新 payload（原对象不变）。"""
        adapted = dict(payload)
        if param == "response_format":
            adapted.pop("response_format", None)
        elif param == "max_tokens":
            # OpenAI o1 系之后改用 max_completion_tokens；部分后端反之。
            adapted["max_completion_tokens"] = adapted.pop("max_tokens")
        return adapted

    async def _post_chat(
        self,
        payload: dict[str, Any],
        *,
        client: httpx.AsyncClient,
        timeout: httpx.Timeout | None = None,
    ) -> dict:
        """POST /chat/completions + 多厂商兼容重试（BUGFIX #159）。

        两类重试，均最多一次，避免对同一错误反复打后端：
        1. 429 限流：解析 Retry-After（≤3s）同后端退避重发一次；缺失/超限/再次 429
           直接上抛（走降级链）。
        2. 400 参数不支持：按响应体关键字识别（response_format / max_tokens），
           适配后重发一次（json_schema 降级为纯 prompt 约束、max_tokens 换名）。
        其余错误原样上抛（_raise_http_with_body 已带响应体正文）。
        """
        url = f"{self.base_url}/chat/completions"
        current = payload
        adapted = False
        rate_retried = False
        while True:
            if timeout is not None:
                r = await client.post(url, json=current, timeout=timeout)
            else:
                r = await client.post(url, json=current)
            if r.status_code == 429 and not rate_retried:
                wait = _retry_after_seconds(r)
                if wait is not None:
                    logger.info("LLM 429 rate limited, retrying after %.1fs", wait)
                    rate_retried = True
                    await asyncio.sleep(wait)
                    continue
            elif r.status_code == 400 and not adapted:
                param = _unsupported_param(r.text or "", current)
                if param:
                    logger.warning(
                        "LLM 400: 参数 %s 不被后端支持，适配后重试（%s）",
                        param,
                        (r.text or "")[:200],
                    )
                    current = self._adapt_payload(current, param)
                    adapted = True
                    continue
            _raise_http_with_body(r)
            return cast(dict[str, Any], r.json())

    async def _chat_completion(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        temperature: float = 0.1,
    ) -> dict:
        """调用 OpenAI 兼容 chat completions 端点。

        参数
        ----------
        messages : 消息列表
        response_format : 可选的 JSON Schema 用于结构化输出
        temperature : 采样温度，默认 0.1（低随机性适合工具调用）

        返回
        -------
        dict : 解析后的 JSON 响应体（已剥离思考块）
        """
        # 按 max_context 截断 history（system 永远保留 + 最近 N 轮 user/assistant）
        truncated = self._truncate_history(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": truncated,
            "temperature": temperature,
        }
        # 最大输出长度（gen_limits 全局上限）：限制一次生成的最大 token 数
        if self.max_output_tokens and self.max_output_tokens > 0:
            payload["max_tokens"] = self.max_output_tokens
        if response_format:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": response_format,
                },
            }

        body = await self._post_chat(payload, client=self.client)
        # 安全取嵌套字段：防御 LLM 返回格式异常
        choices = body.get("choices", [])
        if not choices:
            raise ValueError("LLM 返回了空的 choices 列表")
        content = choices[0].get("message", {}).get("content", "")
        # Token 计量（上传=prompt_tokens / 下载=completion_tokens）；缺失时按字符数估算
        record_openai_usage(
            body,
            backend=self.usage_label,
            model=self.model,
            fallback_messages=truncated,
            fallback_output=str(content or ""),
        )
        parsed = extract_json(content)
        if parsed is None:
            raise ValueError(f"LLM 输出无法解析为 JSON: {content[:200]}")
        return cast(dict[str, Any], parsed)

    async def _chat_json_with_retry(
        self,
        messages: list[dict],
        *,
        response_format: dict | None = None,
        temperature: float = 0.1,
    ) -> dict:
        """_chat_completion + 第四层重试：解析失败时把错误提示喂回模型重发一次。"""
        last_output = ""
        for attempt in range(2):
            msgs = messages
            if attempt:
                hint = RETRY_PROMPT.replace("{last_output}", last_output[:2000])
                msgs = [*messages, {"role": "user", "content": hint}]
            try:
                return await self._chat_completion(
                    msgs,
                    response_format=response_format,
                    temperature=temperature,
                )
            except ValueError as exc:
                last_output = str(exc)
        raise ValueError("LLM 输出两次均无法解析为 JSON")

    # ---- 原生 Function Calling（OpenAI 模式，2026-08-07）--------------------

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        tool_choice: str = "auto",
        temperature: float = 0.1,
        timeout: float | None = None,
    ) -> dict:
        """OpenAI 原生工具调用：传 tools 参数，返原始响应体（不做 JSON 解析）。

        Returns:
            {"content": str | None, "tool_calls": [{id, name, arguments(dict)}],
             "raw_message": dict}

        失败抛异常（httpx / HTTP 状态码错误），由调用方降级到提示词协议。
        """
        truncated = self._truncate_history(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": truncated,
            "temperature": temperature,
            "tools": tools,
            "tool_choice": tool_choice,
        }
        if self.max_output_tokens and self.max_output_tokens > 0:
            payload["max_tokens"] = self.max_output_tokens
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout or 90.0, connect=5.0),
            headers=self._auth_headers(),
        ) as client:
            body = await self._post_chat(payload, client=client)
        choices = body.get("choices") or []
        if not choices:
            raise ValueError("LLM 返回了空的 choices 列表")
        msg = choices[0].get("message") or {}
        record_openai_usage(
            body,
            backend=self.usage_label,
            model=self.model,
            fallback_messages=truncated,
            fallback_output=str(msg.get("content") or ""),
        )
        tool_calls: list[dict[str, Any]] = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except (json.JSONDecodeError, TypeError):
                args = {}
            tool_calls.append(
                {
                    "id": str(tc.get("id") or f"call_{len(tool_calls)}"),
                    "name": str(fn.get("name") or ""),
                    "arguments": args if isinstance(args, dict) else {},
                }
            )
        return {
            "content": msg.get("content"),
            "tool_calls": tool_calls,
            "raw_message": msg,
        }

    async def supports_tool_calling(self) -> bool:
        """能力探测：后端是否支持 tools 参数（短超时，失败即返 False）。"""
        try:
            await self.chat_with_tools(
                [{"role": "user", "content": "ping"}],
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "noop_probe",
                            "description": "能力探测，不要调用",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                tool_choice="none",
                timeout=5.0,
            )
            return True
        except Exception as exc:  # 不支持 / 不可达 → 回退提示词协议
            logger.debug("tool calling probe failed: %s", exc)
            return False

    # ---- 意图分类 ------------------------------------------------------------

    async def classify_intent(self, text: str) -> Intent:
        """分类用户意图。

        注意：Router 通常将 intent 任务路由到 Ollama（数据敏感性），
        此实现保留作为 fallback。
        """
        from agent.observability.cot_log import cot as cot_log

        try:
            result = await self._chat_json_with_retry(
                [
                    {"role": "system", "content": load_prompt("intent")},
                    {"role": "user", "content": text},
                ],
                response_format={
                    "type": "object",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": ["query", "mutate", "orchestrate", "chitchat"],
                        }
                    },
                    "required": ["intent"],
                },
            )
        except Exception as exc:
            cot_log("private.classify_intent.error", text=text, error=repr(exc))
            return "query"

        intent = result.get("intent", "query")
        cot_log("private.classify_intent.result", text=text, result=result, intent=intent)
        if intent not in {"query", "mutate", "orchestrate", "chitchat"}:
            return "query"
        return cast(Intent, intent)

    async def analyze_intent(
        self, text: str, history: list | None = None, page_context: str = ""
    ) -> dict:
        """结构化意图分析（Intent Router）。失败抛异常，由 LMRouter 降级链接管。

        page_context（2026-08-14）：当前页签/场景一行描述，非空时拼进 user 消息。
        """
        from agent.llm.types import IntentAnalysis  # 避免循环导入
        from agent.observability.cot_log import cot as cot_log

        # 动态 Few-Shot（2026-08-31）：案例库检索相似历史成功案例拼入 system，
        # 检索故障/空库静默回退基础模板（不影响主链路）
        try:
            from agent.graph.intent_memory import compose_intent_system_prompt

            system_prompt = await compose_intent_system_prompt(text)
        except Exception:
            system_prompt = load_prompt("intent_router")
        msgs: list[dict] = [{"role": "system", "content": system_prompt}]
        for h in (history or [])[-4:]:
            parsed = normalize_message(h)
            if parsed is None:
                continue
            role, content = parsed
            msgs.append({"role": role, "content": content})
        user_content = text
        if page_context.strip():
            user_content = f"[页面上下文：{page_context.strip()}]\n\n{text}"
        msgs.append({"role": "user", "content": user_content})
        result = await self._chat_json_with_retry(
            msgs,
            response_format={
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "enum": ["query", "mutate", "orchestrate", "chitchat"],
                    },
                    "need_tool": {"type": "boolean"},
                    "need_clarification": {"type": "boolean"},
                },
                "required": ["intent"],
            },
        )
        if not isinstance(result, dict) or not result:
            cot_log("private.analyze_intent.empty", text=text)
            raise ValueError("intent analysis output is empty")
        cot_log("private.analyze_intent.parsed", text=text, payload=result)
        return IntentAnalysis.from_raw(result, fallback_text=text, backend="private").to_dict()

    # ---- 计划生成 ------------------------------------------------------------

    async def plan(
        self,
        *,
        intent: Intent,
        user_prompt: str,
        history: list,
        tool_specs: list[dict],
    ) -> tuple[list[dict], str]:
        """生成工具执行计划。

        返回 (步骤列表, 解释文本)。LLM 调用失败时返回空列表。
        """
        from agent.builtin.registry import get_default_registry

        sys_prompt = (
            load_prompt("planner").replace(
                "<<TOOL_SPECS>>",
                json.dumps(tool_specs, ensure_ascii=False, indent=2),
            )
            + "\n\n"
            + get_default_registry().generate_tool_descriptions()
        )
        msgs: list[dict] = [{"role": "system", "content": sys_prompt}]
        for h in history[-6:]:
            parsed = normalize_message(h)
            if parsed is None:
                continue
            role, content = parsed
            msgs.append({"role": role, "content": content})
        msgs.append({"role": "user", "content": user_prompt})

        try:
            result = await self._chat_json_with_retry(
                msgs,
                response_format={
                    "type": "object",
                    "properties": {
                        "explanation": {"type": "string"},
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "server": {"type": "string"},
                                    "name": {"type": "string"},
                                    "args": {"type": "object"},
                                    "risk_level": {
                                        "type": "string",
                                        "enum": ["read", "low", "medium", "high", "critical"],
                                    },
                                    "rationale": {"type": "string"},
                                },
                                "required": ["server", "name", "args", "risk_level", "rationale"],
                            },
                        },
                    },
                    "required": ["explanation", "steps"],
                },
            )
        except Exception:
            return [], "planner failed to produce structured output"
        steps = result.get("steps") or []
        return steps, result.get("explanation", "")

    # ---- 修复工具调用 --------------------------------------------------------

    async def repair_call(
        self,
        *,
        original: dict,
        error: str,
        history: list,
    ) -> dict:
        """根据错误信息重新生成工具调用参数。

        失败时返回原始调用（让上游 retry 逻辑处理）。
        """
        sys_prompt = load_prompt("repair")
        msgs = [
            {"role": "system", "content": sys_prompt},
            {
                "role": "user",
                "content": (
                    f"Original tool call:\n{json.dumps(original, indent=2, default=str)}\n\n"
                    f"Error message:\n{error}\n\n"
                    "Return a corrected tool call (same JSON shape)."
                ),
            },
        ]
        try:
            return await self._chat_json_with_retry(
                msgs,
                response_format={
                    "type": "object",
                    "properties": {
                        "server": {"type": "string"},
                        "name": {"type": "string"},
                        "args": {"type": "object"},
                        "risk_level": {
                            "type": "string",
                            "enum": ["read", "low", "medium", "high", "critical"],
                        },
                        "rationale": {"type": "string"},
                    },
                    "required": ["server", "name", "args", "risk_level"],
                },
            )
        except Exception:
            return original

    # ---- 汇总回答 ------------------------------------------------------------

    async def summarise(
        self,
        *,
        intent: Intent,
        user_prompt: str,
        plan: list[dict],
        results: list[dict],
        history: list | None = None,
    ) -> tuple[str, list[str]]:
        """汇总工具执行结果，生成自然语言终答。

        失败时返回原始结果的文字描述。
        """
        sys_prompt = (
            load_prompt("system") + "\n\n" + load_prompt("summarise") + "\n\n" + FINAL_ANSWER_STYLE
        )
        results_brief = json.dumps(results, ensure_ascii=False, indent=2, default=str)
        plan_brief = json.dumps(plan, ensure_ascii=False, indent=2, default=str)
        # 会话历史简报（BUGFIX #135）：终答此前只看见当轮 user_prompt，
        # 跨轮追问模型会反问「没有明确任务指令」；拼入最近几轮原文恢复连贯。
        history_brief = format_history_brief(history)
        try:
            result = await self._chat_json_with_retry(
                [
                    {"role": "system", "content": sys_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Intent: {intent}\n"
                            f"User question: {user_prompt}\n\n"
                            + (
                                f"Recent conversation (当前问题可能建立在这些对话之上，保持上下文连贯):\n{history_brief}\n\n"
                                if history_brief
                                else ""
                            )
                            + (
                                # 当前时间注入（BUGFIX #112）：模型对「今天」无可靠感知，
                                # 不注入会凭训练知识编造日期；summarise.md §5.1 以此为唯一基准。
                                f"Current time: {current_time_text()}\n\n"
                                f"Plan executed:\n{plan_brief}\n\n"
                                f"Tool results (may be truncated):\n{results_brief}\n\n"
                                "Produce the final answer."
                            )
                        ),
                    },
                ],
                response_format={
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                        "sources": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["answer"],
                },
                temperature=0.3,
            )
        except (httpx.HTTPError, OSError) as exc:
            # 传输层失败（后端不可达 / 超时 / 4xx / 5xx）：上抛让 Router
            # 降级到下一级后端（ollama / cloud），并记日志便于排查。
            logger.warning("private LLM summarise transport failed: %s", exc)
            raise
        except Exception as exc:
            logger.warning("private LLM summarise parse failed: %s", exc)
            return "我无法综合最终答案。下面是工具返回的原始结果。", []
        return result.get("answer", ""), result.get("sources", [])

    # ---- 原始对话（biznav 功能点提取等自由格式输出场景） ----------------

    async def extract_chat(self, messages: list[dict]) -> str:
        """原始对话：messages 透传，返回原始文本（不做 json.loads）。

        与 summarise / _chat_completion 的区别：不强制 response_format、
        不把输出解析成 JSON 对象——biznav 提取提示词要求模型输出 JSON 数组，
        由调用方（extractor._parse_llm_json）自行容错解析。
        """
        truncated = self._truncate_history(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": truncated,
            "temperature": 0.1,
        }
        if self.max_output_tokens and self.max_output_tokens > 0:
            payload["max_tokens"] = self.max_output_tokens
        # 提取场景 prompt 大（最多 8 个文件片段）且要求输出长 JSON，
        # 共享 client 的 90s 默认超时不够 → 单请求放宽到 300s；
        # 但 connect 阶段仍限 10s —— 否则后端不可达时整个请求会
        # 卡在 TCP 连接阶段最长 300s，chat 前端表现为长时间无响应。
        body = await self._post_chat(
            payload,
            client=self.client,
            timeout=httpx.Timeout(300.0, connect=10.0),
        )
        choices = body.get("choices", [])
        if not choices:
            raise ValueError("LLM 返回了空的 choices 列表")
        content = choices[0].get("message", {}).get("content", "")
        record_openai_usage(
            body,
            backend=self.usage_label,
            model=self.model,
            fallback_messages=truncated,
            fallback_output=str(content or ""),
        )
        return _strip_think(content)

    # ---- 流式对话（2026-09-03 回答逐字流式） ------------------------------

    async def chat_stream(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.3,
        timeout: float = 300.0,
    ) -> AsyncIterator[str]:
        """流式对话（OpenAI 兼容 SSE）：逐 `data:` 行解析 choices[0].delta.content。

        终答路径专用：不带 response_format（结构化 JSON 信封与逐字流式冲突），
        think 块由 ThinkBlockFilter 增量抑制（内网推理模型 <THINK> 前奏不漏给前端）；
        多厂商 400 参数适配不做流式内重试（失败直接上抛，Router 切降级链 /
        非流式兜底）；流式帧无 usage 字段，Token 计量 V1 缺省（非流式路径不受影响）。
        """
        truncated = self._truncate_history(messages)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": truncated,
            "temperature": temperature,
            "stream": True,
        }
        if self.max_output_tokens and self.max_output_tokens > 0:
            payload["max_tokens"] = self.max_output_tokens
        filt = ThinkBlockFilter()
        url = f"{self.base_url}/chat/completions"
        try:
            # connect 限 10s（同 extract_chat）：后端不可达时快速上抛切降级链
            async with self.client.stream(
                "POST", url, json=payload, timeout=httpx.Timeout(timeout, connect=10.0)
            ) as r:
                if r.status_code >= 400:
                    # 流式响应不能直接 r.text：先 aread 再拼错误正文（BUGFIX #137 同源语义）
                    detail = (await r.aread()).decode("utf-8", errors="replace").strip()[:500]
                    logger.error(
                        "LLM stream HTTP %s from %s: %s", r.status_code, url, detail or "(空响应体)"
                    )
                    r.raise_for_status()
                async for line in r.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if data == "[DONE]":
                        break
                    try:
                        frame = json.loads(data)
                    except ValueError:
                        continue  # 半截帧/保活噪声，不中断主流程
                    choices = frame.get("choices") or []
                    if not choices:
                        continue
                    piece = str((choices[0].get("delta") or {}).get("content") or "")
                    if piece:
                        out = filt.feed(piece)
                        if out:
                            yield out
        except httpx.HTTPError as exc:
            logger.warning("private LLM chat_stream transport failed: %s", exc)
            raise
        # 流正常收尾（[DONE] 或自然 EOF）：放行扣留尾巴
        tail = filt.flush()
        if tail:
            yield tail
