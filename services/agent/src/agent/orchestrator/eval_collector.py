"""orchestrator.eval_collector —— Phase 12 V1.5 评测指标 + LLM Judge 抽样。

设计文档 §3.2 评测维度：

| 指标 | 验收标准 |
|---|---|
| 结构校验通过率 | ≥ 95% |
| 子任务成功率（不含重试） | ≥ 85% |
| Worker 重试率 | ≤ 10% |
| DLQ 堆积率 | ≤ 1% |
| 上下文压缩率 | ≥ 60% |
| 必读字段保留率 | 100% |
| 端到端耗时 | P50 < 5s / P99 < 30s |

§3.3 LLM Judge：抽样 10%（`settings.orchestrator_judge_sample_rate`）由独立 LLM
对摘要质量打 1-5 分。**Judge 只是质量趋势信号，不作为 CI 闸门**（设计文档明文）。

抽样是**确定性**的（计数器取模），不用随机数 —— 测试可复现，也便于统计对齐。
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from agent.config import settings

logger = logging.getLogger(__name__)

# 验收阈值（设计文档 §3.2）
THRESHOLDS: dict[str, float] = {
    "validation_pass_rate": 0.95,
    "success_rate": 0.85,
    "retry_rate": 0.10,          # 上限
    "dlq_rate": 0.01,            # 上限
    "compression_ratio": 0.60,
    "required_fields_kept_rate": 1.0,
    "p50_ms": 5_000.0,           # 上限
    "p99_ms": 30_000.0,          # 上限
}

JUDGE_PROMPT_TEMPLATE = """你是子 Agent 输出质量评审员。请对下面这份子 Agent 摘要打分。

评分标准（1-5 分整数）：
5 = 摘要准确、结构清晰、对主 Agent 决策直接有用
4 = 基本可用，个别细节缺失
3 = 勉强可用，信息密度低
2 = 明显跑题或缺关键结论
1 = 无效输出

任务类型: __TASK_TYPE__
任务描述: __TASK_DESC__
子 Agent 摘要:
__SUMMARY__

只输出一行 JSON：{"score": <1-5>, "reason": "<不超过 40 字>"}
"""


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = (len(ordered) - 1) * pct
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return ordered[int(idx)]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)


@dataclass
class JudgeVerdict:
    """Judge 评分结果。"""
    task_id: str
    score: int = 0
    reason: str = ""
    sampled: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "score": self.score,
            "reason": self.reason,
            "sampled": self.sampled,
            "error": self.error,
        }


@dataclass
class EvalCollector:
    """进程内评测指标累加器（快照可写 orchestrator.db::sub_agent_metrics）。"""

    dispatched: int = 0
    succeeded: int = 0
    failed: int = 0
    dlq: int = 0
    cancelled: int = 0
    validation_checked: int = 0
    validation_failed: int = 0
    retries: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    compression_ratios: list[float] = field(default_factory=list)
    required_fields_checked: int = 0
    required_fields_kept: int = 0
    judge_scores: list[int] = field(default_factory=list)
    local_only_forced: int = 0
    hitl_requested: int = 0
    hitl_approved: int = 0
    hitl_rejected: int = 0
    _judge_counter: int = 0

    # ---- 记录 -----------------------------------------------------------

    def record_dispatch(self, *, local_only: bool = False) -> None:
        self.dispatched += 1
        if local_only:
            self.local_only_forced += 1

    def record_context(self, *, compression_ratio: float, required_kept: bool) -> None:
        self.compression_ratios.append(float(compression_ratio))
        self.required_fields_checked += 1
        if required_kept:
            self.required_fields_kept += 1

    def record_validation(self, *, ok: bool) -> None:
        self.validation_checked += 1
        if not ok:
            self.validation_failed += 1

    def record_retry(self, n: int = 1) -> None:
        self.retries += max(0, n)

    def record_result(self, *, status: str, latency_ms: int, attempts: int = 1) -> None:
        """status ∈ ok / err / dlq / cancelled。"""
        self.latencies_ms.append(float(max(0, latency_ms)))
        if attempts > 1:
            self.record_retry(attempts - 1)
        if status == "ok":
            self.succeeded += 1
        elif status == "dlq":
            self.dlq += 1
            self.failed += 1
        elif status == "cancelled":
            self.cancelled += 1
        else:
            self.failed += 1

    def record_hitl(self, *, decision: str) -> None:
        self.hitl_requested += 1
        if decision == "approve":
            self.hitl_approved += 1
        else:
            self.hitl_rejected += 1

    # ---- 抽样 -----------------------------------------------------------

    def should_judge(self, *, sample_rate: float | None = None) -> bool:
        """确定性抽样：rate=0.1 → 每 10 个抽 1 个（第 1、11、21…）。"""
        rate = settings.orchestrator_judge_sample_rate if sample_rate is None else sample_rate
        if rate <= 0:
            return False
        if rate >= 1:
            return True
        interval = max(1, int(round(1 / rate)))
        hit = (self._judge_counter % interval) == 0
        self._judge_counter += 1
        return hit

    # ---- 快照 -----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        total = max(1, self.dispatched)
        checked = max(1, self.validation_checked)
        req_checked = max(1, self.required_fields_checked)
        metrics = {
            "dispatched": self.dispatched,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "dlq": self.dlq,
            "cancelled": self.cancelled,
            "retries": self.retries,
            "success_rate": self.succeeded / total,
            "retry_rate": self.retries / total,
            "dlq_rate": self.dlq / total,
            "validation_pass_rate": (checked - self.validation_failed) / checked
            if self.validation_checked else 1.0,
            "compression_ratio": (
                sum(self.compression_ratios) / len(self.compression_ratios)
                if self.compression_ratios else 0.0
            ),
            "required_fields_kept_rate": self.required_fields_kept / req_checked
            if self.required_fields_checked else 1.0,
            "p50_ms": _percentile(self.latencies_ms, 0.50),
            "p99_ms": _percentile(self.latencies_ms, 0.99),
            "local_only_forced": self.local_only_forced,
            "hitl": {
                "requested": self.hitl_requested,
                "approved": self.hitl_approved,
                "rejected": self.hitl_rejected,
            },
            "judge": {
                "samples": len(self.judge_scores),
                "avg_score": (
                    sum(self.judge_scores) / len(self.judge_scores)
                    if self.judge_scores else 0.0
                ),
                "sample_rate": settings.orchestrator_judge_sample_rate,
                "is_ci_gate": False,   # 设计文档 §3.3：Judge 不作 CI 闸门
            },
        }
        metrics["thresholds"] = dict(THRESHOLDS)
        metrics["violations"] = self._violations(metrics)
        return metrics

    @staticmethod
    def _violations(metrics: dict[str, Any]) -> list[str]:
        """列出未达标项（仅提示，不阻塞）。"""
        out: list[str] = []
        lower_bound = ("validation_pass_rate", "success_rate",
                       "compression_ratio", "required_fields_kept_rate")
        upper_bound = ("retry_rate", "dlq_rate", "p50_ms", "p99_ms")
        for key in lower_bound:
            if metrics.get(key, 0) < THRESHOLDS[key]:
                out.append(f"{key}<{THRESHOLDS[key]}")
        for key in upper_bound:
            if metrics.get(key, 0) > THRESHOLDS[key]:
                out.append(f"{key}>{THRESHOLDS[key]}")
        return out

    def reset(self) -> None:
        self.__init__()  # type: ignore[misc]


# ---- Judge ----------------------------------------------------------------


def _parse_judge_output(text: str) -> tuple[int, str]:
    """解析 Judge 输出（JSON 优先，退化正则；兜底 0 分）。"""
    if not text:
        return 0, "empty judge output"
    match = re.search(r"\{[^{}]*\}", text, re.S)
    if match:
        import json
        try:
            data = json.loads(match.group(0))
            score = int(data.get("score", 0))
            reason = str(data.get("reason", ""))[:120]
            return max(0, min(5, score)), reason
        except Exception:  # noqa: BLE001
            pass
    digits = re.search(r"\b([1-5])\b", text)
    if digits:
        return int(digits.group(1)), text.strip()[:120]
    return 0, text.strip()[:120]


async def judge_report(
    *,
    task_id: str,
    task_type: str,
    task_description: str,
    summary: str,
    judge_caller: Optional[Callable[[str], Awaitable[str]]] = None,
    collector: Optional[EvalCollector] = None,
) -> JudgeVerdict:
    """对一份子 Agent 摘要打分（无 caller → 直接返 sampled=False 空结果）。

    Judge 调用失败**绝不**影响主流程（返回 error 字段）。
    """
    if judge_caller is None:
        return JudgeVerdict(task_id=task_id, sampled=False, error="no judge_caller")
    prompt = (
        JUDGE_PROMPT_TEMPLATE
        .replace("__TASK_TYPE__", task_type or "")
        .replace("__TASK_DESC__", (task_description or "")[:200])
        .replace("__SUMMARY__", (summary or "")[:1500])
    )
    try:
        raw = await judge_caller(prompt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[eval] judge 调用失败 task=%s err=%s", task_id, exc)
        return JudgeVerdict(task_id=task_id, sampled=True, error=f"{type(exc).__name__}: {exc}")
    score, reason = _parse_judge_output(raw or "")
    if collector is not None and score > 0:
        collector.judge_scores.append(score)
    return JudgeVerdict(task_id=task_id, score=score, reason=reason, sampled=True)


# ---- 全局单例 -------------------------------------------------------------

_default_collector: EvalCollector | None = None


def get_default_collector() -> EvalCollector:
    global _default_collector
    if _default_collector is None:
        _default_collector = EvalCollector()
    return _default_collector


def reset_default_collector() -> EvalCollector:
    """测试 hook。"""
    global _default_collector
    _default_collector = EvalCollector()
    return _default_collector


__all__ = [
    "EvalCollector",
    "JudgeVerdict",
    "THRESHOLDS",
    "judge_report",
    "get_default_collector",
    "reset_default_collector",
]
