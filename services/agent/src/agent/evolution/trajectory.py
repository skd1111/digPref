"""evolution.trajectory —— 任务收尾轨迹抽取 + 环境信号归一（设计文档 §3.1 / §2.4）。

挂载点：`graph/stream.py` 在 done 之前调 `record_run_outcome`（后台
best-effort 任务，不阻塞流收尾）。

职责：
    1. 从图终态快照抽取轨迹摘要（意图 / skill / 工具指纹 / 结果），落
       `trajectories` 表——只存摘要，不存参数明文 / 凭证 / 大体积输出。
    2. 环境客观信号（成功 / 失败）归一落 `evaluation_signals`（source=env）。
    3. 失败轨迹后台触发 `reflection` 反思（经验学习闭环起点）。

失败判定（启发式，与环境信号互补）：
    - 无终答（工具编排失败 / 预算耗尽）
    - 终答命中已知失败文案特征（重试耗尽 / 修复上限 / 重复调用熔断 / 无法完成）
    - trace 里存在 tool_orchestrator / repair 的 fail 记录
"""

from __future__ import annotations

import logging
from typing import Any

from agent.config import settings
from agent.evolution import reflection, storage
from agent.evolution.signature import compute_task_signature, tool_fingerprint

logger = logging.getLogger(__name__)

# 终答失败文案特征（与 tools/loop.py / responder.py 的硬失败文案同源；
# 只用于启发式判定，新增失败文案时同步维护）
_FAIL_MARKERS = (
    "重试",  # 工具重试耗尽
    "修复上限",  # Auto-Repair 耗尽
    "自动重试",  # repair 达上限
    "无法完成",  # 工具编排放弃
    "暂时无法",  # 编排不可用
    "没有可用的候选工具",
    "已被用户停止",
    "人工检查错误详情",
)

_ANSWER_DIGEST_CHARS = 300


def _extract_tool_names(state: dict[str, Any]) -> list[str]:
    """从终态工具结果列表提取有序工具名（只取名字，不碰参数）。"""
    names: list[str] = []
    for entry in state.get("tool_results") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("tool") or "").strip()
        if name:
            names.append(name)
    return names


def _trace_has_failure(state: dict[str, Any]) -> bool:
    for entry in state.get("trace") or []:
        if not isinstance(entry, dict):
            continue
        if str(entry.get("status") or "") == "fail" and str(entry.get("node") or "") in (
            "tool_orchestrator",
            "repair",
        ):
            return True
    return False


def _judge_outcome(state: dict[str, Any]) -> str:
    """启发式判定任务结果：success / fail。"""
    final = str(state.get("final_answer") or "").strip()
    if not final:
        return "fail"
    if _trace_has_failure(state):
        return "fail"
    if state.get("needs_human_intervention"):
        return "fail"
    head = final[:120]
    if any(marker in head for marker in _FAIL_MARKERS):
        return "fail"
    return "success"


async def record_run_outcome(
    *,
    run_id: str,
    user_prompt: str,
    state: dict[str, Any],
) -> None:
    """任务收尾钩子：抽轨迹 + 环境信号归一 + 失败触发反思。

    全函数 best-effort：任何异常吞掉记日志，绝不影响主链路。
    """
    if not settings.evolution_enabled:
        return
    try:
        analysis = state.get("intent_analysis")
        intent: dict[str, Any] = {}
        if isinstance(analysis, dict):
            intent = {
                "intent": analysis.get("intent"),
                "intent_category": analysis.get("intent_category"),
                "rewritten_query": str(analysis.get("rewritten_query") or "")[:200],
            }
        category = str(intent.get("intent_category") or "")
        skill_id = str(state.get("active_skill_id") or "")
        tool_names = _extract_tool_names(state)
        signature = compute_task_signature(category, skill_id, tool_names)
        outcome = _judge_outcome(state)
        digest = str(state.get("final_answer") or "")[:_ANSWER_DIGEST_CHARS]

        await storage.record_trajectory(
            session_id=run_id,
            task_signature=signature,
            intent=intent,
            active_skill_id=skill_id,
            tool_fp=tool_fingerprint(tool_names),
            outcome=outcome,
            answer_digest=digest,
        )
        await storage.record_signal(
            session_id=run_id,
            task_signature=signature,
            source="env",
            score=1.0 if outcome == "success" else 0.0,
            reason=str(user_prompt or "")[:120],
        )
        if outcome == "fail":
            # 失败即反思（Reflexion 式）：后台提炼教训入经验库
            await reflection.run_reflection(
                {
                    "session_id": run_id,
                    "task_signature": signature,
                    "intent": intent,
                    "active_skill_id": skill_id,
                    "tool_fp": tool_fingerprint(tool_names),
                    "outcome": outcome,
                    "answer_digest": digest,
                }
            )
    except Exception as exc:
        logger.warning("[evolution] trajectory record failed run=%s: %s", run_id, exc)
