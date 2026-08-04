"""Phase 18 Auto-Repair 循环纯逻辑 —— Coding 框架的确定性验证钩子。

语义（spec §3.1）：
- coding 子任务的文件写操作成功后运行 CodingValidator；
- 失败 → 错误写入 error_feedback + 合成一条 coding_validation 失败结果喂回模型，
  由动态工具循环自然重试（attempt 递增）；
- 达到预算 → needs_human_intervention，交由 responder 汇总失败原因。

红线：本模块只处理"确定性失败"（语法/编译/测试），不触碰 HITL 审批；
写工具本身的风险闸门（medium）照旧由 hitl_gate/dispatcher 负责。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from agent.coding.toolchain import load_toolchain_config
from agent.coding.validator import CodingValidator

WRITE_TOOL_NAMES = frozenset({"write_file", "edit_file", "create_file", "apply_patch"})

_FEEDBACK_MAX = 6  # error_feedback 上限（防 token 爆炸，保留最近条目）


def coding_budget(state: dict) -> int:
    """coding 子任务的 repair 预算（取 execution_policies 中 coding 策略上限）。"""
    policies = state.get("execution_policies") or []
    budgets = [
        int(p.get("max_repair_attempts", 3))
        for p in policies
        if p.get("framework") == "coding"
    ]
    return budgets[0] if budgets else 3


def should_retry(state: dict) -> bool:
    return int(state.get("repair_attempt") or 0) < coding_budget(state)


def validate_written_files(state: dict, pairs: list[tuple[dict, dict]]) -> dict | None:
    """对成功执行的文件写操作做确定性验证。

    Args:
        state: 当前 AgentState（读 routing / repair_attempt / error_feedback）
        pairs: [(tool_call, tool_result), ...]

    Returns:
        None = 无需 repair；否则返回 AgentState 增量：
        extra_results / error_feedback / repair_attempt /
        needs_human_intervention / trace
    """
    routing = state.get("routing")
    if routing not in ("coding", "mixed"):
        return None

    # 收集成功写入的文件路径
    files: list[str] = []
    for call, result in pairs:
        if str(call.get("name") or "") not in WRITE_TOOL_NAMES:
            continue
        if not result.get("ok"):
            continue
        args = call.get("arguments") or call.get("args") or {}
        path = args.get("path") or args.get("file_path")
        if path:
            files.append(str(path))
    if not files:
        return None

    # 项目根 V1：取第一个文件的目录（同批文件通常同项目）
    project_root = os.path.dirname(os.path.abspath(files[0]))
    validator = CodingValidator(
        project_root=project_root,
        toolchain_config=load_toolchain_config(),
    )
    vr = validator.validate(files)
    if vr.ok:
        return None

    attempt = int(state.get("repair_attempt") or 0) + 1
    budget = coding_budget(state)
    exhausted = attempt >= budget

    feedback = list(state.get("error_feedback") or [])
    feedback.append({
        "attempt": attempt,
        "error": (vr.error or "")[:2000],
        "files": files,
        "validator_level": vr.level,
        "ts": datetime.now(timezone.utc).isoformat(),
    })
    feedback = feedback[-_FEEDBACK_MAX:]

    extra_results = [{
        "id": str(uuid.uuid4()),
        "name": "coding_validation",
        "ok": False,
        "error": (
            f"代码验证失败（第 {attempt}/{budget} 次修复预算）：\n{vr.error}\n"
            "请修复上述问题后重新写入。"
        ),
    }]

    return {
        "extra_results": extra_results,
        "error_feedback": feedback,
        "repair_attempt": attempt,
        "needs_human_intervention": exhausted,
        "trace": [{
            "node": "tool_orchestrator",
            "status": "fail",
            "kind": "repair_attempt",
            "attempt": attempt,
            "max_attempts": budget,
            "validator_level": vr.level,
            "error_summary": (vr.error or "")[:200],
            "ts": datetime.now(timezone.utc).isoformat(),
        }],
    }
