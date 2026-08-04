"""Phase 18 ExecutionPolicy —— 子任务级执行策略。

每个子任务/计划步骤携带一个策略，决定：
- framework：coding（Auto-Repair + 确定性验证）/ work（HITL 优先、失败即停）
- max_repair_attempts：随验证能力降级（full=3 / syntax_only=2 / unverified=1；work=0）
- validator_level：可执行的验证层级
- autonomy：继承会话级自主性

红线：策略只影响"失败后怎么办"，不改变风险闸门——medium+ 风险照样过 hitl_gate。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agent.dual.router import ModeRouter

Framework = Literal["coding", "work"]
ValidatorLevel = Literal["full", "syntax_only", "unverified"]
# HYBRID 拆解阶段（对齐 Code/Work 双模式提示词：Code→验证→Work→确认）
Stage = Literal["code", "work", "verify", "confirm"]

# coding 子任务 repair 上限随验证能力降级
_REPAIR_BUDGET_BY_LEVEL: dict[ValidatorLevel, int] = {
    "full": 3,
    "syntax_only": 2,
    "unverified": 1,
}

# 本地文件类 builtin 工具 → mixed 拆解歧义时的 coding 判据
_FILE_TOOL_NAMES = frozenset({
    "write_file", "edit_file", "create_file", "apply_patch", "run_tests",
})


@dataclass
class ExecutionPolicy:
    framework: Framework
    max_repair_attempts: int
    validator_level: ValidatorLevel
    autonomy: str = "interactive"
    stage: Stage = "code"

    def to_dict(self) -> dict:
        return {
            "framework": self.framework,
            "max_repair_attempts": self.max_repair_attempts,
            "validator_level": self.validator_level,
            "autonomy": self.autonomy,
            "stage": self.stage,
        }


def build_policy(
    framework: Framework,
    validator_level: ValidatorLevel,
    autonomy: str = "interactive",
) -> ExecutionPolicy:
    if framework == "work":
        budget = 0  # work 子任务失败即停等人工，不自动重试
    else:
        budget = _REPAIR_BUDGET_BY_LEVEL[validator_level]
    return ExecutionPolicy(
        framework=framework,
        max_repair_attempts=budget,
        validator_level=validator_level,
        autonomy=autonomy,
        stage="code" if framework == "coding" else "work",
    )


def _subtask_framework(call: dict, router: ModeRouter, prior: Framework) -> Framework:
    """mixed 路由下单个子任务的框架判定（V1：关键词 + 文件工具启发式）。"""
    text = call.get("description") or call.get("name") or ""
    hit = router.keyword_route(text)
    if hit in ("coding", "work"):
        return hit  # type: ignore[return-value]
    # 歧义/无命中：本地文件类工具归 coding，外部系统调用归 work
    if call.get("server") == "builtin" and call.get("name") in _FILE_TOOL_NAMES:
        return "coding"
    return prior if prior in ("coding", "work") else "work"


def tag_plan_with_policy(
    plan: list[dict],
    routing: str,
    validator_level: ValidatorLevel = "full",
    autonomy: str = "interactive",
) -> list[dict]:
    """为计划中每个步骤生成 ExecutionPolicy（与 plan 同序）。

    routing 为 coding/work 时整体打标；mixed 时逐子任务判定。
    """
    if not plan:
        return []
    router = ModeRouter(llm=None)
    policies: list[dict] = []
    for call in plan:
        if routing in ("coding", "work"):
            fw: Framework = routing  # type: ignore[assignment]
        else:
            fw = _subtask_framework(call, router, prior="work")
        policies.append(build_policy(fw, validator_level, autonomy).to_dict())
    return policies


def decomposition_stages(plan: list[dict], routing: str) -> list[dict]:
    """HYBRID 拆解阶段序列（展示/审计用，不改变实际执行顺序）。

    对齐双模式提示词：Code 子任务 → verify → Work 子任务 → confirm。
    返回 [{"index": plan 下标(-1=合成阶段), "stage": code|work|verify|confirm}, ...]。
    """
    if not plan:
        return []
    policies = tag_plan_with_policy(plan, routing)
    stages: list[dict] = []
    prev_fw: str | None = None
    has_work = False
    has_code = False
    for i, p in enumerate(policies):
        fw = p["framework"]
        if fw == "work":
            has_work = True
            if prev_fw == "coding":
                stages.append({"index": i, "stage": "verify"})  # code→work 边界插验证
        else:
            has_code = True
        stages.append({"index": i, "stage": "code" if fw == "coding" else "work"})
        prev_fw = fw
    if has_code and not has_work:
        stages.append({"index": -1, "stage": "verify"})
    if has_work:
        stages.append({"index": -1, "stage": "confirm"})
    return stages
