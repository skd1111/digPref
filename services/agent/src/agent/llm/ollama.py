"""Ollama HTTP client — used for data-sensitive tasks (intent, repair)."""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import httpx

from agent.dual.prompt_loader import FINAL_ANSWER_STYLE
from agent.llm.circuit_breaker import CircuitBreakerRegistry
from agent.llm.json_discipline import extract_json, parse_with_retry
from agent.llm.prompts import load_prompt
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

    # ---- HTTP primitives ---------------------------------------------------

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
        if merged_options:
            payload["options"] = merged_options
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
            return str(msg["content"])

        payload = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
        if not isinstance(payload, dict):
            return "query"  # fail safe — most common, lowest risk
        intent = payload.get("intent", "query")
        if intent not in ("query", "mutate", "orchestrate", "chitchat"):
            return "query"
        return cast(Intent, intent)

    # ---- 结构化意图分析（Intent Router，2026-08-06）-------------------------

    async def analyze_intent(self, text: str, history: list | None = None) -> dict:
        """结构化意图分析：改写句 + 四分类 + 细分类型 + 实体 + 追问 + 风险。

        解析失败抛异常，由 LMRouter 降级链接管。
        """
        from agent.llm.types import IntentAnalysis  # 避免循环导入

        msgs: list[dict] = [{"role": "system", "content": load_prompt("intent_router")}]
        for h in (history or [])[-4:]:  # 最近 2 轮上下文
            role = getattr(h, "role", None) or (h.get("role") if isinstance(h, dict) else None)
            content = getattr(h, "content", None) or (
                h.get("content") if isinstance(h, dict) else None
            )
            if role in ("user", "assistant") and content:
                msgs.append({"role": role, "content": str(content)})
        msgs.append({"role": "user", "content": text})

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
            return str(msg["content"])

        payload = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
        if not isinstance(payload, dict) or not payload:
            raise ValueError("intent analysis output is not a JSON object")
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
            if hasattr(h, "role") and hasattr(h, "content"):
                msgs.append({"role": h.role, "content": h.content})
            elif isinstance(h, dict):
                msgs.append({"role": h.get("role", "user"), "content": h.get("content", "")})
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
    ) -> tuple[str, list[str]]:
        sys_prompt = _summarise_system_prompt()
        results_brief = json.dumps(results, ensure_ascii=False, indent=2, default=str)
        plan_brief = json.dumps(plan, ensure_ascii=False, indent=2, default=str)

        async def _call(hint: str, last: str) -> str:
            user_content = (
                f"Intent: {intent}\n"
                f"User question: {user_prompt}\n\n"
                f"Plan executed:\n{plan_brief}\n\n"
                f"Tool results (may be truncated):\n{results_brief}\n\n"
                "Produce the final answer."
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
