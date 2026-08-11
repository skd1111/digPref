"""LocalSmallLLMClient —— 本地端侧小模型（OpenAI 兼容协议）。

Phase 4 V0：端侧模型只做"思考"（分类 + 列计划），不执行。
- 正常模式：简单任务先走端侧 → 失败回退 Ollama/云端
- 性能模式：跳过端侧，直走云端
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

import httpx

from agent.llm.json_discipline import extract_json, parse_with_retry
from agent.llm.token_usage import record_openai_usage
from agent.llm.types import Intent

logger = logging.getLogger("agent.llm.local_small")


class LocalSmallUnavailableError(Exception):
    """本地端侧模型不可达时抛出，让 fallback 链切下一级。"""


class LocalSmallLLMClient:
    """本地小模型文本客户端 — 兼容 OpenAI Chat Completions API。

    设计约束：
    - 只做简单任务（分类、列计划），不做复杂推理
    - 超时短（5s），失败快速回退
    - 所有公共方法失败返回安全默认值（不抛异常）
    """

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8081/v1",
        model: str = "qwen2.5-0.5b",
        max_context: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.max_context = max_context

    # ---- HTTP primitive ---------------------------------------------------

    async def _chat(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 256,
        temperature: float = 0.1,
        timeout: float = 5.0,
        response_format: dict | None = None,
    ) -> dict:
        """OpenAI 兼容 /v1/chat/completions 调用。"""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(f"{self.base_url}/chat/completions", json=payload)
                r.raise_for_status()
                body = r.json()
                msg = body["choices"][0]["message"]
                # Token 计量（端侧小模型）；usage 缺失时按字符数估算
                record_openai_usage(
                    body,
                    backend="local_small",
                    model=self.model,
                    fallback_messages=messages,
                    fallback_output=str(msg.get("content", "") or ""),
                )
                return msg
        except httpx.ConnectError as exc:
            logger.debug("local_small: connection refused at %s", self.base_url)
            raise LocalSmallUnavailableError(
                f"Local small model not reachable at {self.base_url}"
            ) from exc
        except httpx.TimeoutException as exc:
            logger.debug("local_small: request timed out")
            raise LocalSmallUnavailableError("Local small model request timed out") from exc
        except (KeyError, IndexError) as exc:
            logger.debug("local_small: unexpected response shape: %s", exc)
            raise LocalSmallUnavailableError(
                "Local small model returned unexpected response"
            ) from exc

    # ---- Intent classification (端侧核心能力 #1) --------------------------

    async def classify_intent(self, text: str) -> Intent:
        """快速意图分类 —— 端侧模型擅长的简单任务。"""
        if not text or not text.strip():
            return "query"
        try:

            async def _call(hint: str, last: str) -> str:
                user_content = text + (f"\n\n{hint}" if hint else "")
                msg = await self._chat(
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是一个意图分类器。分析用户输入，返回以下意图之一：\n"
                                "- query: 查询/读取数据\n"
                                "- mutate: 修改/写入数据\n"
                                "- orchestrate: 跨系统编排/部署\n"
                                "- chitchat: 闲聊/问候\n\n"
                                '只返回 JSON：{"intent": "<意图>"}'
                            ),
                        },
                        {"role": "user", "content": user_content},
                    ],
                    max_tokens=32,
                    temperature=0.0,
                    timeout=3.0,
                    response_format={"type": "json_object"},
                )
                return str(msg["content"])

            payload = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
            if not isinstance(payload, dict):
                return "query"
            intent = payload.get("intent", "query")
            if intent not in ("query", "mutate", "orchestrate", "chitchat"):
                return "query"
            return cast(Intent, intent)
        except (LocalSmallUnavailableError, TypeError, KeyError):
            return "query"  # 安全兜底

    # ---- Plan generation (端侧核心能力 #2) ---------------------------------

    async def plan(
        self,
        *,
        intent: Intent,
        user_prompt: str,
        history: list,
        tool_specs: list[dict],
    ) -> tuple[list[dict], str]:
        """列计划 —— 端侧模型生成执行步骤大纲。"""
        try:
            sys_prompt = (
                "你是一个任务规划器。根据用户请求和可用工具，生成执行计划。\n"
                '返回 JSON：{"explanation": "<简短说明>", "steps": [...]}\n'
                '每个 step：{"server": "<MCP server>", "name": "<工具名>", '
                '"args": {}, "risk_level": "read|low|medium|high|critical", '
                '"rationale": "<原因>"}'
            )
            tools_text = json.dumps(tool_specs, ensure_ascii=False, indent=2)

            async def _call(hint: str, last: str) -> str:
                user_content = user_prompt + (f"\n\n{hint}" if hint else "")
                msg = await self._chat(
                    [
                        {
                            "role": "system",
                            "content": f"{sys_prompt}\n\n可用工具：\n{tools_text}",
                        },
                        {"role": "user", "content": user_content},
                    ],
                    max_tokens=512,
                    temperature=0.1,
                    timeout=5.0,
                    response_format={"type": "json_object"},
                )
                return str(msg["content"])

            payload = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
            if not isinstance(payload, dict):
                return [], ""
            steps = payload.get("steps") or []
            return steps, payload.get("explanation", "")
        except (LocalSmallUnavailableError, TypeError, KeyError):
            return [], ""  # 安全兜底：空计划

    # ---- Repair (端侧不擅长，走云端；保留接口兼容) --------------------------

    async def repair_call(
        self,
        *,
        original: dict,
        error: str,
        history: list,
    ) -> dict:
        """端侧不做 repair —— 直接返回原样，让 fallback 链切到 Ollama。"""
        raise LocalSmallUnavailableError("repair not supported on local_small")

    # ---- Summarise (端侧不擅长，走云端；保留接口兼容) -----------------------

    async def summarise(
        self,
        *,
        intent: Intent,
        user_prompt: str,
        plan: list[dict],
        results: list[dict],
    ) -> tuple[str, list[str]]:
        """端侧不做 summarise —— 直接返回占位，让 fallback 链切到 Ollama。"""
        raise LocalSmallUnavailableError("summarise not supported on local_small")


# 模块级单例（可选，测试可注入）
_client: LocalSmallLLMClient | None = None


def get_local_small_client(
    base_url: str | None = None,
    model: str | None = None,
) -> LocalSmallLLMClient:
    """获取本地小模型客户端单例。"""
    global _client
    if _client is None or base_url or model:
        from agent.config import settings

        _client = LocalSmallLLMClient(
            base_url=base_url or settings.local_small_base_url,
            model=model or settings.local_small_model,
        )
    return _client
