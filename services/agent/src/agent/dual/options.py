"""Phase 18 推荐选项生成 —— Work 框架审批决策点的候选方案。

work 子任务触发 medium+ 审批前，LLM 生成 2~3 个候选（必含"不执行"保底项）
与推荐项；前端 ApprovalCard 渲染选项列表；自动模式按推荐项执行。
解析失败一律返回空（hitl_gate 回退二元审批，绝不因选项机制阻塞审批）。
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from protocol.approval import ApprovalOption

from agent.llm.json_discipline import extract_json, parse_with_retry
from agent.llm.prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)

_ABORT_LABEL = "不执行"


async def generate_approval_options(
    llm: Any | None, call: dict
) -> tuple[list[ApprovalOption], str | None, str | None]:
    """返回 (options, recommended_option_id, recommendation_reason)。

    任何失败（LLM 不可用/输出非法）都返回 ([], None, None)。
    """
    if llm is None:
        return [], None, None
    operation = (
        str(call.get("name") or "")
        + " "
        + json.dumps(
            call.get("args") or call.get("arguments") or {}, ensure_ascii=False, default=str
        )[:500]
    )
    target = str(call.get("target_system") or call.get("server") or "")
    try:

        async def _call(hint: str, last: str) -> str:
            prompt = render_prompt(
                load_prompt("approval/options"),
                OPERATION=operation,
                TARGET=target + (f"\n\n{hint}" if hint else ""),
            )
            return str(await llm.route(task="query", prompt=prompt))

        raw = await parse_with_retry(_call, lambda t: extract_json(t, want="object"))
    except Exception as exc:
        logger.warning("approval options generation failed: %s", exc)
        return [], None, None
    if not isinstance(raw, dict):
        return [], None, None
    return _parse_options(json.dumps(raw, ensure_ascii=False))


def _parse_options(
    raw: str | None,
) -> tuple[list[ApprovalOption], str | None, str | None]:
    data = _extract_json(raw or "")
    if not isinstance(data, dict):
        return [], None, None
    raw_opts = data.get("options")
    if not isinstance(raw_opts, list) or not raw_opts:
        return [], None, None

    options: list[ApprovalOption] = []
    for o in raw_opts[:4]:
        if not isinstance(o, dict) or not o.get("label"):
            continue
        options.append(
            ApprovalOption(
                id=str(o.get("id") or f"o{len(options) + 1}"),
                label=str(o["label"]),
                adjusted_plan=str(o.get("adjusted_plan") or ""),
                risk_note=o.get("risk_note"),
            )
        )
    if not options:
        return [], None, None

    # 保底项：必须存在"不执行"
    if not any(o.label == _ABORT_LABEL for o in options):
        options.append(
            ApprovalOption(
                id=f"abort-{uuid.uuid4().hex[:8]}",
                label=_ABORT_LABEL,
                adjusted_plan="",
                risk_note="取消本次操作",
            )
        )

    rec = data.get("recommended_option_id")
    reason = data.get("recommendation_reason")
    rec_valid = rec if any(o.id == rec for o in options) else None
    if rec_valid is None:
        reason = None  # 无有效推荐项时理由一并不返回
    return options, rec_valid, (str(reason) if reason else None)


def _extract_json(text: str) -> Any:
    """从模型输出里抽取 JSON（共享容错解析，spec §4.5）。"""
    return extract_json(text, want="object")
