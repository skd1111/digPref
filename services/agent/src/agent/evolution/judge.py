"""evolution.judge —— 主对话终答 LLM-as-a-Judge 抽样评测（V1，设计文档 §2.3）。

把 `orchestrator/eval_collector` 的确定性抽样 Judge 泛化到主对话
`final_answer`：
    - 抽样是**确定性**的（计数器取模，可复现，与子任务 Judge 同范式）
    - 抽样率 `settings.evolution_judge_sample_rate`（默认 0 = 关闭）
    - Judge 分仅作质量趋势与进化信号（落 `evaluation_signals` source=judge），
      **不作为 CI 闸门**（延续既有约定）
    - 任务 `answer_judge` 接触用户请求与回答原文 → `_LOCAL_ONLY_TASKS` 本地优先
"""

from __future__ import annotations

import itertools
import logging
from typing import Any

from agent.config import settings
from agent.evolution import storage
from agent.llm.prompts import load_prompt, render_prompt

logger = logging.getLogger(__name__)

# 确定性抽样计数器（与 eval_collector 同范式：计数器取模，测试可复现）
_counter = itertools.count(1)


def reset_judge_counter() -> None:
    """测试 hook：重置抽样计数器。"""
    global _counter
    _counter = itertools.count(1)


def _sampled(sample_rate: float | None = None) -> bool:
    rate = settings.evolution_judge_sample_rate if sample_rate is None else sample_rate
    if rate <= 0.0:
        return False
    if rate >= 1.0:
        next(_counter)
        return True
    n = next(_counter)
    return n % max(1, round(1.0 / rate)) == 0


async def maybe_judge_answer(
    *,
    run_id: str,
    task_signature: str,
    user_prompt: str,
    final_answer: str,
    sample_rate: float | None = None,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    """抽样对终答打分并落 `evaluation_signals`（source=judge）。

    返回 {"score": 1-5, "reason": ...}（测试用）；未抽中 / 失败返 None。
    全函数 best-effort，绝不阻塞主链路。
    """
    if not settings.evolution_enabled:
        return None
    if not _sampled(sample_rate):
        return None
    answer = str(final_answer or "").strip()
    if not answer:
        return None
    try:
        prompt = render_prompt(
            load_prompt("evolution/answer_judge"),
            USER_PROMPT=str(user_prompt or "")[:500],
            ANSWER=answer[:1500],
        )
    except Exception as exc:
        logger.warning("[evolution] answer_judge prompt load failed: %s", exc)
        return None

    try:
        from agent.llm.router import LMRouter

        text = await LMRouter().route(task="answer_judge", prompt=prompt)
    except Exception as exc:
        logger.info("[evolution] answer_judge LLM unavailable, skipped: %s", exc)
        return None

    # 复用 orchestrator Judge 的解析（1-5 分 + 理由，兜底 0 分）
    from agent.orchestrator.eval_collector import _parse_judge_output

    score, reason = _parse_judge_output(text or "")
    if score <= 0:
        return None
    try:
        await storage.record_signal(
            session_id=run_id,
            task_signature=task_signature,
            source="judge",
            score=round(score / 5.0, 2),
            rating=score,
            reason=reason,
            db_path=db_path,
        )
    except Exception as exc:
        logger.warning("[evolution] judge signal insert failed: %s", exc)
        return None
    return {"score": score, "reason": reason}
