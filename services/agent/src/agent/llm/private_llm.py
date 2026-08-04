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

import json
import re
from typing import Any

import httpx

from agent.dual.prompt_loader import FINAL_ANSWER_STYLE
from agent.llm.prompts import load_prompt
from agent.llm.types import Intent

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
    ) -> None:
        """内网 LLM 客户端初始化。

        Args:
            base_url: 内网 LLM 网关（OpenAI 兼容协议）。
            api_key: Bearer Token（来自 keyring 占位符解析）。
            model: 模型名（如 'DeepSeek-RD-Llama-70B-Int8'）。
            max_context: 上下文窗口大小（tokens）。None=不截断 history。
                配置时按 `chars_per_token=4` 估算 message 长度，超长 history 从中部丢消息。
        """
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.max_context = max_context
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
                {**m, "content": str(m.get("content", ""))[-keep_chars:]}
                for m in system_msgs
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
                    kept.append(
                        {**m, "content": str(m.get("content", ""))[:remaining]}
                    )
                break
            kept.append(m)
            remaining -= content_len
        kept.reverse()
        return system_msgs + kept

    @property
    def client(self) -> httpx.AsyncClient:
        """获取或创建共享的 httpx 客户端（复用连接池）。"""
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(90.0, connect=10.0),
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    # ---- HTTP 底层 -----------------------------------------------------------

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
        if response_format:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": response_format,
                },
            }

        r = await self.client.post(
            f"{self.base_url}/chat/completions",
            json=payload,
        )
        r.raise_for_status()
        body = r.json()
        # 安全取嵌套字段：防御 LLM 返回格式异常
        choices = body.get("choices", [])
        if not choices:
            raise ValueError("LLM 返回了空的 choices 列表")
        content = choices[0].get("message", {}).get("content", "")
        return json.loads(_strip_think(content))

    # ---- 意图分类 ------------------------------------------------------------

    async def classify_intent(self, text: str) -> Intent:
        """分类用户意图。

        注意：Router 通常将 intent 任务路由到 Ollama（数据敏感性），
        此实现保留作为 fallback。
        """
        try:
            result = await self._chat_completion(
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
        except Exception:
            return "query"
        intent = result.get("intent", "query")
        if intent not in {"query", "mutate", "orchestrate", "chitchat"}:
            return "query"
        return intent  # type: ignore[return-value]

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
            if hasattr(h, "role") and hasattr(h, "content"):
                msgs.append({"role": h.role, "content": h.content})
            elif isinstance(h, dict):
                msgs.append({"role": h.get("role", "user"), "content": h.get("content", "")})
        msgs.append({"role": "user", "content": user_prompt})

        try:
            result = await self._chat_completion(
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
            return await self._chat_completion(
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
    ) -> tuple[str, list[str]]:
        """汇总工具执行结果，生成自然语言终答。

        失败时返回原始结果的文字描述。
        """
        sys_prompt = (
            load_prompt("system")
            + "\n\n"
            + load_prompt("summarise")
            + "\n\n"
            + FINAL_ANSWER_STYLE
        )
        results_brief = json.dumps(results, ensure_ascii=False, indent=2, default=str)
        plan_brief = json.dumps(plan, ensure_ascii=False, indent=2, default=str)
        try:
            result = await self._chat_completion(
                [
                    {"role": "system", "content": sys_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Intent: {intent}\n"
                            f"User question: {user_prompt}\n\n"
                            f"Plan executed:\n{plan_brief}\n\n"
                            f"Tool results (may be truncated):\n{results_brief}\n\n"
                            "Produce the final answer."
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
        except Exception:
            return "我无法综合最终答案。下面是工具返回的原始结果。", []
        return result.get("answer", ""), result.get("sources", [])
