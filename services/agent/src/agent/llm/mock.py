"""Mock LLM client —— 内置占位回复，无需任何外部服务。

用于：
    - 本地 demo / 离线开发
    - 端到端链路测试
    - Ollama 不可达时的兜底

行为：
    - classify_intent: 简单关键词启发式
    - plan: 返回空步骤（无工具）
    - repair: 返回原样
    - summarise: 根据 user_prompt 套友好模板
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("agent.llm.mock")


class MockLLMClient:
    """占位 LLM 客户端。所有方法都不发起网络请求。"""

    name = "mock"

    async def classify_intent(self, text: str) -> str:
        # 极简关键词启发式
        t = text.lower().strip()
        if not t:
            return "chitchat"
        # 写操作信号
        if re.search(
            r"\b(insert|update|delete|drop|create|alter|truncate|grant|revoke)\b", t
        ) or any(
            kw in t for kw in ("修改", "删除", "更新", "插入", "新增", "清空", "建表", "删除表")
        ):
            logger.info("mock.classify_intent: %r → mutate", text)
            return "mutate"
        # 跨系统编排
        if any(kw in t for kw in ("orchestrate", "跨系统", "编排", "工作流", "workflow")):
            logger.info("mock.classify_intent: %r → orchestrate", text)
            return "orchestrate"
        # 闲聊
        if any(
            kw in t
            for kw in ("你好", "你是", "hi", "hello", "嗨", "who are you", "what's your name")
        ):
            logger.info("mock.classify_intent: %r → chitchat", text)
            return "chitchat"
        logger.info("mock.classify_intent: %r → query (默认)", text)
        return "query"

    async def plan(
        self,
        *,
        intent: str,
        user_prompt: str,
        history: list,
        tool_specs: list[dict],
    ) -> tuple[list[dict], str]:
        # 闲聊意图 → 不出 plan
        if intent == "chitchat":
            return [], "（mock 模式：闲聊意图，不生成工具步骤）"
        return [], f"（mock 模式：无可用工具，已忽略 prompt '{user_prompt[:30]}'）"

    async def repair_call(
        self,
        *,
        original: dict,
        error: str,
        history: list,
    ) -> dict:
        return original

    async def summarise(
        self,
        *,
        intent: str,
        user_prompt: str,
        plan: list[dict],
        results: list[dict],
        history: list | None = None,
    ) -> tuple[str, list[str]]:
        # 模仿真实 LLM 一些延迟，方便看 UI
        await _sleep_async(0.3)

        if intent == "chitchat":
            return _chitchat_answer(user_prompt), []

        if not plan:
            return (
                "（mock）当前没有可调用的工具 / 系统资产。要在 Settings → LLM 接入 "
                "切换到真实 LLM，或者先在 Settings → Secrets 注册 MCP server。",
                [],
            )

        # 简化渲染
        return (
            f"（mock）执行计划：\n{_format_plan(plan)}\n\n"
            f"结果摘要：{len(results)} 条工具返回已收到。",
            [s.get("server") for s in plan if s.get("server")],
        )

    async def extract_chat(self, messages: list[dict]) -> str:
        """mock 原始对话：返回一个示例功能点 JSON 数组，让 biznav 提取链路
        在 mock 模式下能端到端跑通（不发起任何网络请求）。"""
        await _sleep_async(0.1)
        return (
            '[{"id": "mock-feature-1", "name": "示例业务功能点", '
            '"description": "（mock 模式）导入工程后由 AI 提炼的示例功能点", '
            '"category": "业务", "related_files": [], "related_apis": [], '
            '"related_tables": [], "business_rules": []}]'
        )


def _chitchat_answer(user_prompt: str) -> str:
    p = user_prompt.strip()
    if any(kw in p for kw in ("你是谁", "你叫什么", "who are you", "what's your name", "name")):
        return (
            "（mock）我是 **EAIDE** —— Enterprise AI IDE 助理，"
            "一个在企业内网本地运行的 AI IDE Agent，面向开发 / DBA / 业务系统操作员。\n\n"
            "我可以帮你：\n"
            "1. 跨数据库 / API / SSH / RPA 系统执行自然语言指令\n"
            "2. 在写操作前自动暂停（Human-in-the-Loop），等你 Approve\n"
            "3. 把每一次操作都落到本地审计表，全程可追溯"
        )
    if any(kw in p for kw in ("你好", "hi", "hello", "嗨", "哈喽")):
        return "（mock）你好！我是 EAIDE 助理。要试试什么？例如：`看看 orders 表前 10 行` 或 `查询 user 表里所有上海的用户`。"
    if any(kw in p for kw in ("help", "帮助", "能做什么", "怎么用")):
        return (
            "（mock）**EAIDE 快速上手**\n\n"
            "1. **左侧 Explorer** 选择要操作的系统资产（DB / API / SSH）\n"
            "2. **中央对话区** 用自然语言下达指令\n"
            "3. **写操作会弹审批卡** —— 我会展示计划，等你点击 Approve\n"
            "4. **右侧 Trace** 实时看 LangGraph 状态机每一步\n"
            "5. **底部终端** 接收 SSE 流式日志（LOG / agent think / mcp tool ...）\n\n"
            "快捷键：Ctrl+Shift+P 命令面板 · Ctrl+T 新会话 · F1 全部快捷键"
        )
    return f"（mock）收到：{p[:80]}"


def _format_plan(plan: list[dict]) -> str:
    if not plan:
        return "  （空）"
    lines = []
    for i, step in enumerate(plan, 1):
        lines.append(
            f"  {i}. `{step.get('server', '?')}.{step.get('name', '?')}` "
            f"risk={step.get('risk_level', '?')} — {step.get('rationale', '')}"
        )
    return "\n".join(lines)


async def _sleep_async(seconds: float) -> None:
    import asyncio

    await asyncio.sleep(seconds)
