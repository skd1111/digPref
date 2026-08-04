"""Ollama HTTP client — used for data-sensitive tasks (intent, repair)."""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from agent.dual.prompt_loader import FINAL_ANSWER_STYLE
from agent.llm.prompts import load_prompt
from agent.llm.types import Intent

logger = logging.getLogger("agent.llm.ollama")


def _summarise_system_prompt() -> str:
    """终答 system prompt：总纲（system.md）+ 汇总模板 + 双模式回答风格。"""
    return (
        load_prompt("system")
        + "\n\n"
        + load_prompt("summarise")
        + "\n\n"
        + FINAL_ANSWER_STYLE
    )


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
    ) -> None:
        """Ollama 客户端初始化。

        Args:
            base_url: Ollama 服务地址（默认 http://127.0.0.1:11434）。
            model: 模型名（如 'qwen2.5:14b'）。
            max_context: 上下文窗口大小（tokens）。None=不设置（让 Ollama 走模型默认）。
                当配置时，每次 chat 请求会注入 `options.num_ctx`，让 Ollama 把 KV cache 限制
                在用户配置的窗口大小内——避免超长 history 触发显存爆掉。
        """
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_context = max_context

    # ---- HTTP primitives ---------------------------------------------------

    async def _chat(
        self,
        messages: list[dict],
        *,
        format: dict | None = None,
        options: dict | None = None,
        timeout: float = 30.0,
    ) -> dict:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
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
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(f"{self.base_url}/api/chat", json=payload)
                r.raise_for_status()
                return r.json()["message"]
        except httpx.ConnectError as exc:
            logger.error("Ollama _chat: connection refused at %s: %s", self.base_url, exc)
            raise OllamaUnavailableError(
                f"Ollama is not reachable at {self.base_url}. "
                f"Ensure the Ollama service is running."
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error("Ollama _chat: request timed out after %.0fs", timeout)
            raise OllamaUnavailableError(
                f"Ollama request timed out after {timeout:.0f}s"
            ) from exc

    async def _generate(
        self,
        prompt: str,
        *,
        format: dict | None = None,
        timeout: float = 60.0,
    ) -> str:
        payload = {"model": self.model, "prompt": prompt, "stream": False}
        if format:
            payload["format"] = format
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(f"{self.base_url}/api/generate", json=payload)
                r.raise_for_status()
                return r.json()["response"]
        except httpx.ConnectError as exc:
            logger.error("Ollama _generate: connection refused at %s: %s", self.base_url, exc)
            raise OllamaUnavailableError(
                f"Ollama is not reachable at {self.base_url}"
            ) from exc
        except httpx.TimeoutException as exc:
            logger.error("Ollama _generate: request timed out after %.0fs", timeout)
            raise OllamaUnavailableError(
                f"Ollama request timed out after {timeout:.0f}s"
            ) from exc

    # ---- Intent classification --------------------------------------------

    async def classify_intent(self, text: str) -> Intent:
        msg = await self._chat(
            [
                {"role": "system", "content": load_prompt("intent")},
                {"role": "user", "content": text},
            ],
            format={
                "type": "object",
                "properties": {
                    "intent": {
                        "enum": ["query", "mutate", "orchestrate", "chitchat"]
                    }
                },
                "required": ["intent"],
            },
        )
        try:
            payload = json.loads(msg["content"])
        except (json.JSONDecodeError, TypeError):
            return "query"  # fail safe — most common, lowest risk
        intent = payload.get("intent", "query")
        if intent not in ("query", "mutate", "orchestrate", "chitchat"):
            return "query"
        return intent  # type: ignore[return-value]

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

        msg = await self._chat(
            msgs,
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
                            "required": ["server", "name", "args", "risk_level", "rationale"],
                        },
                    },
                },
                "required": ["explanation", "steps"],
            },
            timeout=60.0,
        )
        try:
            payload = json.loads(msg["content"])
        except (json.JSONDecodeError, TypeError):
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
        msgs = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": (
                f"Original tool call:\n{json.dumps(original, indent=2, default=str)}\n\n"
                f"Error message:\n{error}\n\n"
                "Return a corrected tool call (same JSON shape)."
            )},
        ]
        msg = await self._chat(
            msgs,
            format={
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
                "required": ["server", "name", "args", "risk_level"],
            },
        )
        try:
            return json.loads(msg["content"])
        except (json.JSONDecodeError, TypeError):
            return original  # give up — return as-is; the next attempt will fail again

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
        msg = await self._chat(
            [
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": (
                    f"Intent: {intent}\n"
                    f"User question: {user_prompt}\n\n"
                    f"Plan executed:\n{plan_brief}\n\n"
                    f"Tool results (may be truncated):\n{results_brief}\n\n"
                    "Produce the final answer."
                )},
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
        try:
            payload = json.loads(msg["content"])
        except (json.JSONDecodeError, TypeError):
            return "I could not synthesize a final answer.", []
        return payload.get("answer", ""), payload.get("sources", [])