"""Ollama HTTP client — used for data-sensitive tasks (intent, repair)."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx

from agent.dual.prompt_loader import FINAL_ANSWER_STYLE
from agent.llm.circuit_breaker import CircuitBreakerRegistry
from agent.llm.json_discipline import extract_json, parse_with_retry
from agent.llm.prompts import (
    current_time_text,
    format_history_brief,
    load_prompt,
    normalize_message,
)
from agent.llm.stream_utils import ThinkBlockFilter
from agent.llm.token_usage import record_ollama_usage
from agent.llm.types import Intent

logger = logging.getLogger("agent.llm.ollama")

# 降级熔断器（BUGFIX #88，复用 circuit_breaker 标准实现）：
# 调不通立即切下一级（不在本级重试）；连续 3 次失败 → Open 拒绝探测 30s，
# 避免 Ollama 未启动时每次调用白耗 ~2s 连接尝试；到期 Half-Open 自动探测恢复。
_OLLAMA_BREAKER_REGISTRY = CircuitBreakerRegistry(failure_threshold=3, reset_timeout_s=30.0)


def _ollama_breaker(base_url: str):
    return _OLLAMA_BREAKER_REGISTRY.get_or_create(f"ollama:{base_url}")


def _summarise_system_prompt() -> str:
    """终答 system prompt：总纲（system.md）+ 汇总模板 + 双模式回答风格。"""
    return load_prompt("system") + "\n\n" + load_prompt("summarise") + "\n\n" + FINAL_ANSWER_STYLE


class OllamaUnavailableError(Exception):
    """Raised when Ollama is unreachable — caller should fall back or abort."""


class OllamaClient:
    """Thin async client for the Ollama REST API."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        max_context: int | None = None,
        keep_alive: str = "10m",
        enabled: bool = True,
        max_output_tokens: int | None = None,
    ) -> None:
        """Ollama 客户端初始化。

        Args:
            base_url: Ollama 服务地址（默认 http://127.0.0.1:11434）。
            model: 模型名（如 'qwen2.5:14b'）。
            max_context: 上下文窗口大小（tokens）。None=不设置（让 Ollama 走模型默认）。
                当配置时，每次 chat 请求会注入 `options.num_ctx`，让 Ollama 把 KV cache 限制
                在用户配置的窗口大小内——避免超长 history 触发显存爆掉。
            keep_alive: Phase 17 L5 —— 会话期内模型不卸载（KV cache 留存），
                同会话多轮前缀可复用；"0" 表示用完即卸（省内存）。
            enabled: 端侧模型是否已配置（BUGFIX #89）。未配置时所有调用直接判不可用，
                零探测零等待，降级链照常继续。
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_context = max_context
        self.keep_alive = keep_alive
        self.enabled = enabled
        # 全局输出上限（gen_limits 两级回退）→ Ollama options.num_predict
        self.max_output_tokens = max_output_tokens

    # ---- HTTP primitives ---------------------------------------------------

    def _build_chat_payload(
        self,
        messages: list[dict],
        *,
        format: dict | None = None,
        options: dict | None = None,
    ) -> dict[str, Any]:
        """组装 /api/chat 请求体（_chat 与 chat_stream 共用，语义完全一致）。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            # Phase 17 L5：模型常驻，同会话 KV cache 可复用（前缀命中提速）
            "keep_alive": self.keep_alive,
        }
        if format:
            payload["format"] = format
        # 合并 options：max_context → num_ctx（Ollama 协议字段名）
        merged_options: dict[str, Any] = {}
        if self.max_context is not None and self.max_context > 0:
            merged_options["num_ctx"] = self.max_context
        if options:
            merged_options.update(options)
        # 最大输出长度（gen_limits 全局上限）→ num_predict；调用点显式传了则取较小值
        if self.max_output_tokens and self.max_output_tokens > 0:
            existing = merged_options.get("num_predict")
            merged_options["num_predict"] = (
                min(int(existing), self.max_output_tokens)
                if isinstance(existing, int) and existing > 0
                else self.max_output_tokens
            )
        if merged_options:
            payload["options"] = merged_options
        return payload

    async def _chat(
        self,
        messages: list[dict],
        *,
        format: dict | None = None,
        options: dict | None = None,
        timeout: float = 30.0,
    ) -> dict:
        if not self.enabled:
            raise OllamaUnavailableError("端侧 Ollama 未配置，跳过探测")
        breaker = _ollama_breaker(self.base_url)
        if not breaker.allow():
            raise OllamaUnavailableError(
                f"Ollama at {self.base_url} 近期连续不可达，熔断中（稍后自动探测）"
            )
        payload = self._build_chat_payload(messages, format=format, options=options)
        try:
            # connect 阶段限 5s：Ollama 在同机，连不上就是服务没起，
            # 没必要用完整的生成超时去等 TCP 连接。
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as c:
                r = await c.post(f"{self.base_url}/api/chat", json=payload)
                r.raise_for_status()
                body = r.json()
                breaker.on_success()
                # Token 计量（上传=prompt_eval_count / 下载=eval_count）；缺失时按字符数估算
                msg = body.get("message") or {}
                record_ollama_usage(
                    body,
                    model=self.model,
                    fallback_input="\n".join(str(m.get("content", "") or "") for m in messages),
                    fallback_output=str(msg.get("content", "") or ""),
                )
                return body["message"]
        except httpx.ConnectError as exc:
            logger.error("Ollama _chat: connection refused at %s: %s", self.base_url, exc)
            breaker.on_failure()
            raise OllamaUnavailableError(
                f"Ollama is not reachable at {self.base_url}. Ensure the Ollama service is running."
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error("Ollama _chat: request timed out after %.0fs", timeout)
            breaker.on_failure()
            raise OllamaUnavailableError(f"Ollama request timed out after {timeout:.0f}s") from exc

    async def chat_stream(
        self,
        messages: list[dict],
        *,
        options: dict | None = None,
        timeout: float = 120.0,
    ) -> AsyncIterator[str]:
        """流式对话（2026-09-03 回答逐字流式）：stream=True 按 NDJSON 帧产出增量。

        终答路径专用：不传 format（结构化 JSON 信封与逐字流式冲突），think 块由
        ThinkBlockFilter 增量抑制；熔断 / keep_alive / num_ctx / num_predict 语义与
        _chat 一致；done 帧记 Token 计量（best-effort，无 done 帧时按 EOF 成功收尾）。
        """
        if not self.enabled:
            raise OllamaUnavailableError("端侧 Ollama 未配置，跳过探测")
        breaker = _ollama_breaker(self.base_url)
        if not breaker.allow():
            raise OllamaUnavailableError(
                f"Ollama at {self.base_url} 近期连续不可达，熔断中（稍后自动探测）"
            )
        payload = self._build_chat_payload(messages, format=None, options=options)
        payload["stream"] = True
        filt = ThinkBlockFilter()
        fallback_input = "\n".join(str(m.get("content", "") or "") for m in messages)
        acc: list[str] = []
        done_frame: dict | None = None
        try:
            # connect 阶段限 5s（同 _chat）；读超时用完整生成预算，流式帧间隔远小于总时长
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as c:
                async with c.stream("POST", f"{self.base_url}/api/chat", json=payload) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            frame = json.loads(line)
                        except ValueError:
                            continue  # 半截行/非 JSON 噪声，不中断主流程
                        msg = frame.get("message") or {}
                        piece = str(msg.get("content") or "")
                        if piece:
                            out = filt.feed(piece)
                            if out:
                                acc.append(out)
                                yield out
                        if frame.get("done"):
                            done_frame = frame
                            break
        except httpx.ConnectError as exc:
            logger.error("Ollama chat_stream: connection refused at %s: %s", self.base_url, exc)
            breaker.on_failure()
            raise OllamaUnavailableError(
                f"Ollama is not reachable at {self.base_url}. Ensure the Ollama service is running."
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error("Ollama chat_stream: stream timed out after %.0fs", timeout)
            breaker.on_failure()
            raise OllamaUnavailableError(f"Ollama stream timed out after {timeout:.0f}s") from exc
        # 流正常收尾（done 帧或自然 EOF）：放行扣留尾巴 + 记计量
        breaker.on_success()
        if done_frame is not None:
            record_ollama_usage(
                done_frame,
                model=self.model,
                fallback_input=fallback_input,
                fallback_output="".join(acc),
            )
        tail = filt.flush()
        if tail:
            yield tail

    async def _generate(
        self,
        prompt: str,
        *,
        format: dict | None = None,
        timeout: float = 60.0,
    ) -> str:
        if not self.enabled:
            raise OllamaUnavailableError("端侧 Ollama 未配置，跳过探测")
        breaker = _ollama_breaker(self.base_url)
        if not breaker.allow():
            raise OllamaUnavailableError(
                f"Ollama at {self.base_url} 近期连续不可达，熔断中（稍后自动探测）"
            )
        payload = {"model": self.model, "prompt": prompt, "stream": False}
        if format:
            payload["format"] = format
        # 最大输出长度（gen_limits 全局上限）→ options.num_predict
        if self.max_output_tokens and self.max_output_tokens > 0:
            payload["options"] = {"num_predict": self.max_output_tokens}
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as c:
                r = await c.post(f"{self.base_url}/api/generate", json=payload)
                r.raise_for_status()
                body = r.json()
                breaker.on_success()
                record_ollama_usage(
                    body,
                    model=self.model,
                    fallback_input=prompt,
                    fallback_output=str(body.get("response", "") or ""),
                )
                return body["response"]
        except httpx.ConnectError as exc:
            logger.error("Ollama _generate: connection refused at %s: %s", self.base_url, exc)
            breaker.on_failure()
            raise OllamaUnavailableError(f"Ollama is not reachable at {self.base_url}") from exc
        except httpx.TimeoutException as exc:
            logger.error("Ollama _generate: request timed out after %.0fs", timeout)
            breaker.on_failure()
            raise OllamaUnavailableError(f"Ollama request timed out after {timeout:.0f}s") from exc

    # ---- Intent classification --------------------------------------------

    async def classify_intent(self, text: str) -> Intent:
        from agent.observability.cot_log import cot as cot_log

        async def _call(hint: str, last: str) -> str:
            user_content = text + (f"\n\n{hint}" if hint else "")
            msg = await self._chat(
                [
                    {"role": "system", "content": load_prompt("intent")},
                    {"role": "user", "content": user_content},
                ],
                format={
                    "type": "object",
                    "properties": {
                        "intent": {"enum": ["query", "mutate", "orchestrate", "chitchat"]}
                    },
                    "required": ["intent"],
                },
            )
            raw = str(msg["content"])
            cot_log("ollama.classify_intent.raw", text=text, hint=bool(hint), raw=raw)
            return raw

        payload = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
        if not isinstance(payload, dict):
            cot_log("ollama.classify_intent.fail_safe", text=text, note="解析失败 → query")
            return "query"  # fail safe — most common, lowest risk
        intent = payload.get("intent", "query")
        if intent not in ("query", "mutate", "orchestrate", "chitchat"):
            cot_log("ollama.classify_intent.invalid", text=text, payload=payload, result="query")
            return "query"
        return cast(Intent, intent)

    # ---- 结构化意图分析（Intent Router，2026-08-06）-------------------------

    async def analyze_intent(
        self, text: str, history: list | None = None, page_context: str = ""
    ) -> dict:
        """结构化意图分析：改写句 + 四分类 + 细分类型 + 实体 + 追问 + 风险。

        page_context（2026-08-14）：当前页签/场景一行描述，非空时拼进 user 消息。

        解析失败抛异常，由 LMRouter 降级链接管。
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
        for h in (history or [])[-4:]:  # 最近 2 轮上下文
            parsed = normalize_message(h)
            if parsed is None:
                continue
            role, content = parsed
            msgs.append({"role": role, "content": content})
        user_content = text
        if page_context.strip():
            user_content = f"[页面上下文：{page_context.strip()}]\n\n{text}"
        msgs.append({"role": "user", "content": user_content})

        async def _call(hint: str, last: str) -> str:
            last_content = msgs[-1]["content"] + (f"\n\n{hint}" if hint else "")
            final_msgs = [*msgs[:-1], {"role": "user", "content": last_content}]
            msg = await self._chat(
                final_msgs,
                format={
                    "type": "object",
                    "properties": {
                        "intent": {"enum": ["query", "mutate", "orchestrate", "chitchat"]},
                        "need_tool": {"type": "boolean"},
                        "need_clarification": {"type": "boolean"},
                    },
                    "required": ["intent"],
                },
            )
            raw = str(msg["content"])
            cot_log("ollama.analyze_intent.raw", text=text, hint=bool(hint), raw=raw)
            return raw

        payload = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
        if not isinstance(payload, dict) or not payload:
            cot_log("ollama.analyze_intent.parse_failed", text=text)
            raise ValueError("intent analysis output is not a JSON object")
        cot_log("ollama.analyze_intent.parsed", text=text, payload=payload)
        return IntentAnalysis.from_raw(payload, fallback_text=text, backend="ollama").to_dict()

    # ---- Plan generation ---------------------------------------------------

    async def plan(
        self,
        *,
        intent: Intent,
        user_prompt: str,
        history: list,
        tool_specs: list[dict],
    ) -> tuple[list[dict], str]:
        from agent.builtin.registry import get_default_registry

        sys_prompt = (
            load_prompt("planner").replace(
                "<<TOOL_SPECS>>",
                json.dumps(tool_specs, ensure_ascii=False, indent=2),
            )
            + "\n\n"
            + get_default_registry().generate_tool_descriptions()
        )
        msgs = [{"role": "system", "content": sys_prompt}]
        for h in history[-6:]:  # last 3 turns
            parsed = normalize_message(h)
            if parsed is None:
                continue
            h_role, h_content = parsed
            msgs.append({"role": h_role, "content": h_content})
        msgs.append({"role": "user", "content": user_prompt})

        async def _call(hint: str, last: str) -> str:
            user_content = user_prompt + (f"\n\n{hint}" if hint else "")
            msg = await self._chat(
                [*msgs[:-1], {"role": "user", "content": user_content}],
                format={
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
                                        "enum": ["read", "low", "medium", "high", "critical"]
                                    },
                                    "rationale": {"type": "string"},
                                },
                                "required": [
                                    "server",
                                    "name",
                                    "args",
                                    "risk_level",
                                    "rationale",
                                ],
                            },
                        },
                    },
                    "required": ["explanation", "steps"],
                },
                timeout=60.0,
            )
            return str(msg["content"])

        payload = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
        if not isinstance(payload, dict):
            return [], "planner failed to produce structured output"
        steps = payload.get("steps") or []
        return steps, payload.get("explanation", "")

    # ---- Repair -----------------------------------------------------------

    async def repair_call(
        self,
        *,
        original: dict,
        error: str,
        history: list,
    ) -> dict:
        sys_prompt = load_prompt("repair")

        async def _call(hint: str, last: str) -> str:
            user_content = (
                f"Original tool call:\n{json.dumps(original, indent=2, default=str)}\n\n"
                f"Error message:\n{error}\n\n"
                "Return a corrected tool call (same JSON shape)."
            )
            if hint:
                user_content += f"\n\n{hint}"
            msg = await self._chat(
                [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_content},
                ],
                format={
                    "type": "object",
                    "properties": {
                        "server": {"type": "string"},
                        "name": {"type": "string"},
                        "args": {"type": "object"},
                        "risk_level": {"enum": ["read", "low", "medium", "high", "critical"]},
                        "rationale": {"type": "string"},
                    },
                    "required": ["server", "name", "args", "risk_level"],
                },
            )
            return str(msg["content"])

        fixed = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
        if not isinstance(fixed, dict):
            return original  # give up — return as-is; the next attempt will fail again
        return fixed

    # ---- Summarise --------------------------------------------------------

    async def summarise(
        self,
        *,
        intent: Intent,
        user_prompt: str,
        plan: list[dict],
        results: list[dict],
        history: list | None = None,
    ) -> tuple[str, list[str]]:
        sys_prompt = _summarise_system_prompt()
        results_brief = json.dumps(results, ensure_ascii=False, indent=2, default=str)
        plan_brief = json.dumps(plan, ensure_ascii=False, indent=2, default=str)
        # 会话历史简报（BUGFIX #135）：终答此前只看见当轮 user_prompt，
        # 跨轮追问模型会反问「没有明确任务指令」；拼入最近几轮原文恢复连贯。
        history_brief = format_history_brief(history)

        async def _call(hint: str, last: str) -> str:
            user_content = (
                f"Intent: {intent}\n"
                f"User question: {user_prompt}\n\n"
                + (
                    f"Recent conversation (当前问题可能建立在这些对话之上，保持上下文连贯):\n{history_brief}\n\n"
                    if history_brief
                    else ""
                )
                + (
                    # 当前时间注入（BUGFIX #112）：本地模型对「今天」无可靠感知，
                    # 不注入会凭训练知识编造日期；summarise.md §5.1 以此为唯一基准。
                    f"Current time: {current_time_text()}\n\n"
                    f"Plan executed:\n{plan_brief}\n\n"
                    f"Tool results (may be truncated):\n{results_brief}\n\n"
                    "Produce the final answer."
                )
            )
            if hint:
                user_content += f"\n\n{hint}"
            msg = await self._chat(
                [
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_content},
                ],
                format={
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                        "sources": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["answer"],
                },
                timeout=60.0,
            )
            return str(msg["content"])

        payload = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
        if not isinstance(payload, dict):
            return "I could not synthesize a final answer.", []
        return payload.get("answer", ""), payload.get("sources", [])

    # ---- 原始对话（biznav 功能点提取等自由格式输出场景） --------------------

    async def extract_chat(
        self,
        messages: list[dict],
        *,
        timeout: float = 300.0,
    ) -> str:
        """原始对话：messages 透传，返回模型文本，不强制 JSON 结构化输出。

        与 summarise 的区别：不注入「汇总工具结果」的无关 system prompt，
        也不把输出包成 {"answer": ...} 对象——否则 biznav 提取提示词要求的
        JSON 数组会被包进 answer 字符串，extractor 解析不到功能点。
        """
        msg = await self._chat(messages, timeout=timeout)
        return str(msg.get("content", "") or "")
