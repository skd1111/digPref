"""reqflow.generator —— AI 需求卡片生成（对话摘要 + 功能点上下文 → 结构化草稿）。

调用方（api.py）负责构造 llm_call（三级降级链：本地 Ollama → DB 内网 → DB 云端，
均走 extract_chat 原始对话）；本模块只做 prompt 组装 + JSON 容错解析 + 字段规范化。
LLM 异常（含降级链全失败 RuntimeError）向上冒泡，由 API 层转 502。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from agent.llm.json_discipline import extract_json, parse_with_retry
from agent.llm.prompts import load_prompt, render_prompt

_FEASIBILITY_VALUES = ("feasible", "risky", "infeasible")
_PRIORITY_VALUES = ("P0", "P1", "P2")


def _format_features_context(features: list[dict[str, Any]]) -> str:
    """把功能点 dict 列表渲染成提示词上下文文本。"""
    if not features:
        return "（无关联功能点上下文）"
    blocks: list[str] = []
    for f in features:
        lines = [f"功能点：{f.get('name', '')}（id={f.get('id', '')}）"]
        if f.get("description"):
            lines.append(f"  描述：{f['description']}")
        apis = f.get("related_apis") or []
        if apis:
            api_txt = "；".join(f"{a.get('method', '')} {a.get('path', '')}" for a in apis[:10])
            lines.append(f"  关联API：{api_txt}")
        tables = f.get("related_tables") or []
        if tables:
            lines.append("  关联表：" + "；".join(str(t.get("name", "")) for t in tables[:10]))
        rules = f.get("business_rules") or []
        if rules:
            rule_txt = "；".join(
                str(r.get("text", r) if isinstance(r, dict) else r) for r in rules[:5]
            )
            lines.append(f"  业务规则：{rule_txt}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def build_cardify_messages(
    *,
    features: list[dict[str, Any]],
    conversation_summary: str,
    system_name: str = "",
    done_requirements: str = "",
) -> list[dict[str, str]]:
    """组装 cardify 提示词 messages。"""
    user = render_prompt(
        load_prompt("reqflow/cardify"),
        SYSTEM_NAME=system_name or "（未指定）",
        FEATURES_CONTEXT=_format_features_context(features),
        CONVERSATION_SUMMARY=conversation_summary or "（无对话摘要）",
        DONE_REQUIREMENTS=done_requirements or "（本系统暂无已完成的需求卡片）",
    )
    return [
        {
            "role": "system",
            "content": "你是 EAIDE 需求分析助手，负责把业务人员与 AI 的对齐结论"
            "结构化为需求卡片。只输出 JSON 对象。",
        },
        {"role": "user", "content": user},
    ]


def _normalize(data: dict[str, Any]) -> dict[str, Any]:
    """字段规范化：枚举兜底 + external_systems 统一成字符串数组。"""
    feasibility = str(data.get("feasibility") or "").strip().lower()
    if feasibility not in _FEASIBILITY_VALUES:
        feasibility = "risky"
    priority = str(data.get("priority") or "").strip().upper()
    if priority not in _PRIORITY_VALUES:
        priority = "P2"
    ext = data.get("external_systems")
    if isinstance(ext, str):
        ext_list = [ext] if ext.strip() else []
    elif isinstance(ext, list):
        ext_list = [str(x) for x in ext if str(x).strip()]
    else:
        ext_list = []
    return {
        "title": str(data.get("title") or "未命名需求"),
        "business_value": str(data.get("business_value") or ""),
        "change_points": str(data.get("change_points") or ""),
        "feasibility": feasibility,
        "feasibility_notes": str(data.get("feasibility_notes") or ""),
        "impact": str(data.get("impact") or ""),
        "external_systems": ext_list,
        "priority": priority,
    }


async def generate_card_draft(
    *,
    llm_call: Callable[[list[dict[str, str]]], Awaitable[str]],
    features: list[dict[str, Any]],
    conversation_summary: str,
    system_name: str = "",
    done_requirements: str = "",
) -> dict[str, Any]:
    """生成需求卡片草稿。

    Args:
        llm_call: async (messages) -> str；由 api 层用三级降级链构造，
            全失败时应抛 RuntimeError（本函数直接冒泡）。
        features: biznav 功能点 dict 列表（name/description/related_* 等）。
        conversation_summary: 对齐对话摘要。
        system_name: 系统名称（注入提示词）。
        done_requirements: 本工程已完成需求卡片的摘要文本（影响分析参照）。

    Returns:
        规范化后的卡片草稿 dict（title/business_value/.../priority）。

    Raises:
        RuntimeError: LLM 返回为空或无法解析出 JSON 对象。
    """
    messages = build_cardify_messages(
        features=features,
        conversation_summary=conversation_summary,
        system_name=system_name,
        done_requirements=done_requirements,
    )

    async def _call(hint: str, last: Any) -> str:
        msgs = list(messages)
        if hint:
            msgs = [*msgs, {"role": "user", "content": str(hint)}]
        return str(await llm_call(msgs) or "")

    data = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
    if not isinstance(data, dict) or not data:
        raise RuntimeError("AI 未返回有效的需求卡片结构（LLM 返回为空或格式错误）")
    return _normalize(data)


__all__ = ["build_cardify_messages", "generate_card_draft"]
