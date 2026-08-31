"""intent_slots —— 意图实体槽位规则表与代码层拦截（2026-08-31）。

意图识别四层增强 · 结构化输出：LLM 提取实体可能遗漏/格式漂移，
本模块在 analyze_intent 产出后做代码层硬校验——必填槽位缺失且风险
等级为 high/critical 时，强制 need_clarification=True 触发追问信号，
绝不放行 Agent 猜测执行（与 HITL「要动的先审批」同精神：不确定就问）。

规则来源与 intent_router.md 的槽位纪律保持一致：
    - model_onboard 必填 model_name + endpoint（齐全时禁止追问）
    - conn_test 必填 endpoint
    - task_execution 高风险时至少命中一个目标槽位（表/数据源/主机/端点）

低风险缺失只登记 missing_fields 不拦截——读取类操作缺参可由工具层
再追问，无需在意图层阻断。
"""

from __future__ import annotations

from typing import Any

# 细分类型 → 必填槽位（全部齐备才算可执行）
_CATEGORY_REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    "model_onboard": ("model_name", "endpoint"),
    "conn_test": ("endpoint",),
}

# 细分类型 → 任选其一槽位（高风险写操作至少命中一个目标实体）
_HIGH_RISK_ANY_OF_SLOTS: dict[str, tuple[str, ...]] = {
    "task_execution": ("target_table", "data_source", "target_host", "endpoint"),
}

_BLOCKING_RISKS = ("high", "critical")


def _slot_missing(entities: dict[str, Any], slot: str) -> bool:
    value = entities.get(slot)
    return not str(value or "").strip()


def validate_slots(analysis: dict[str, Any]) -> dict[str, Any]:
    """校验意图分析结果的实体槽位；返回（可能改写过的）新 dict。

    拦截语义：必填槽位缺失 + risk_level ∈ {high, critical}
        → need_clarification=True，missing_fields 补齐，
          clarification_message 为空时生成一句面向用户的追问。
    非拦截语义：低风险缺失只合并进 missing_fields（供下游参考）。
    已带 need_clarification=True 的结果不覆盖其追问文案。
    语义路由直出结果（含 _route 标记）由调用方跳过本校验——
    预置 analysis 为代码精心构造，无需二次校验。
    """
    if not isinstance(analysis, dict):
        return analysis
    out = dict(analysis)
    category = str(out.get("intent_category") or "")
    entities = out.get("entities")
    if not isinstance(entities, dict):
        entities = {}
    risk = str(out.get("risk_level") or "low")

    missing: list[str] = [
        slot for slot in _CATEGORY_REQUIRED_SLOTS.get(category, ()) if _slot_missing(entities, slot)
    ]
    if not missing:
        any_of = _HIGH_RISK_ANY_OF_SLOTS.get(category)
        if any_of and risk in _BLOCKING_RISKS:
            if all(_slot_missing(entities, slot) for slot in any_of):
                # 代表性槽位进 missing_fields（追问话术引用）
                missing.append(any_of[0])

    if not missing:
        return out

    existing = [str(f).strip() for f in (out.get("missing_fields") or []) if str(f).strip()]
    out["missing_fields"] = existing + [f for f in missing if f not in existing]

    if risk in _BLOCKING_RISKS:
        out["need_clarification"] = True
        if not str(out.get("clarification_message") or "").strip():
            out["clarification_message"] = (
                f"执行前需要确认关键信息：{'、'.join(missing)}。请补充后再试。"
            )
        try:
            from agent.observability.cot_log import cot as cot_log

            cot_log(
                "intent.slot_guard",
                intent_category=category,
                risk_level=risk,
                missing=out["missing_fields"],
            )
        except Exception:  # 日志失败不影响拦截结果
            pass
    return out
