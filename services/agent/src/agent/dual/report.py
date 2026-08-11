"""Phase 18 结构化执行报告 —— responder 尾注（对齐双模式提示词输出格式）。

红线（不伪造结果）：
- 只在真实存在信号时生成（repair 发生 / 审批决策产生）；
- 无验证信号绝不声称"已验证"；无执行信号绝不声称"已完成"。
"""

from __future__ import annotations


def build_dual_report(state: dict) -> str | None:
    """按 routing 生成结构化报告；无信号返回 None（不追加任何内容）。"""
    routing = state.get("routing")
    if routing not in ("coding", "work", "mixed"):
        return None

    sections: list[str] = []
    if routing in ("coding", "mixed"):
        code_sec = _coding_section(state)
        if code_sec:
            sections.append(code_sec)
    if routing in ("work", "mixed"):
        work_sec = _work_section(state)
        if work_sec:
            sections.append(work_sec)

    if not sections:
        return None
    title = {"coding": "CODE", "work": "WORK", "mixed": "HYBRID"}[routing]
    return "\n---\n## 执行报告（" + title + "）\n" + "\n".join(sections)


def _coding_section(state: dict) -> str | None:
    attempt = int(state.get("repair_attempt") or 0)
    exhausted = bool(state.get("needs_human_intervention"))
    if attempt <= 0 and not exhausted:
        return None  # 无修复信号 → 不输出，避免伪造"已验证"

    lines = ["### CODE 验证状态"]
    if exhausted:
        lines.append(f"- 结果：未通过验证（Auto-Repair {attempt} 轮后仍未通过，已转人工介入）")
        feedback = state.get("error_feedback") or []
        if feedback:
            last = feedback[-1]
            lines.append(f"- 最后错误：{str(last.get('error') or '')[:200]}")
    else:
        lines.append(f"- 结果：经 {attempt} 轮自动修复后通过验证")
    lines.append("- 说明：验证基于语法检查/项目 validate_command；未覆盖的部分请勿视为已验证。")
    return "\n".join(lines)


def _work_section(state: dict) -> str | None:
    decision = state.get("approval_decision")
    autonomy = state.get("autonomy") or "interactive"
    if not decision and autonomy != "auto":
        return None  # 无审批/自动信号 → 不输出

    lines = ["### WORK 确认状态"]
    if decision == "approve":
        lines.append("- 审批：已批准")
    elif decision == "reject":
        lines.append("- 审批：已拒绝（操作未执行）")
    elif autonomy == "auto":
        lines.append("- 审批：自动模式执行（详见审计日志 decided_by=auto_mode）")
    else:
        lines.append("- 审批：未触发")
    if autonomy == "auto":
        lines.append("- 决策方式：自动模式（按推荐选项执行，全程留痕）")
    else:
        lines.append("- 决策方式：人工交互")
    return "\n".join(lines)
