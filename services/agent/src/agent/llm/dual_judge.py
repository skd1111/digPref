"""dual_judge —— Phase 2C V3-2 关键任务并行推理 + 裁判选择。

设计（来自 phase-2c-smart-router.md §13.5 + SCHEDULE §3.1 V3 拆解）：
    - 关键任务（如 plan / summarise）并发调用多个 backend
    - 裁判（可以是另一个 backend 或本地规则）选最佳答案
    - 返回 winner 文本 + judge_trace（每个 backend 的结果 + 谁赢了 + 评分）

CLAUDE.md §2 红线：
    - `_LOCAL_ONLY_TASKS`（intent / repair / biznav_extract）不走双模型并行
      —— 敏感上下文必须走 Ollama，永不可让多个后端接触。
    - 裁判也可以是本地规则（不调 LLM 的简单 scoring）—— 避免 LLM-as-judge 二次敏感数据暴露。

V3.2 收尾简化：
    - candidates 默认从 RouterEngine._backends 拉 2-3 个（plan / summarise 适合）
    - judge backend 可指定，否则用 router.pick('judge')（V3 简化：pick 的不是 _LOCAL_ONLY_TASKS）
    - judge prompt："请对比以下两个候选回答，选最准确的：..." + LLM 返回 "WINNER: 0|1|2"
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from agent.llm.router import _LOCAL_ONLY_TASKS

logger = logging.getLogger(__name__)


@dataclass
class CandidateResult:
    backend_name: str
    text: str
    error: str | None = None
    latency_ms: int = 0


@dataclass
class JudgeTrace:
    candidates: list[CandidateResult] = field(default_factory=list)
    winner_index: int = -1
    winner_text: str = ""
    judge_backend: str = ""
    judge_reason: str = ""


@dataclass
class DualJudgeResult:
    winner_text: str
    trace: JudgeTrace


async def run_dual_with_judge(
    *,
    user_prompt: str,
    backend_callers: dict[str, Callable[[str, str], Awaitable[str]]],
    candidates: list[str],
    judge_caller: Optional[Callable[[str, str], Awaitable[str]]] = None,
    judge_backend_name: str = "judge",
    judge_prompt_template: Optional[str] = None,
    task_kind: str = "summarise",
    timeout_sec: float = 30.0,
) -> DualJudgeResult:
    """并发调多个 backend，裁判选最优。

    Args:
        user_prompt: 用户问题
        backend_callers: backend_name → async (kind, prompt) → text（candidate 共用）
        candidates: 候选 backend 名字列表（如 ['ollama', 'private']）
        judge_caller: 裁判 async (kind, prompt) → text；None 时用本地规则（pick longest）
        judge_backend_name: 裁判 backend 显示名（仅审计用）；默认 'judge'
        judge_prompt_template: 裁判 prompt 模板；None 用默认
        task_kind: 任务类型（决定是否走 _LOCAL_ONLY_TASKS 红线）
        timeout_sec: 单 backend 调用超时

    Returns:
        DualJudgeResult：winner_text + trace（含每个 candidate 的结果）

    Raises:
        ValueError: _LOCAL_ONLY_TASKS 任务禁止并行
        RuntimeError: 所有 candidates 全失败

    V3.2 修订：judge_caller 独立于 candidate callers —— 防止 judge_backend='ollama'
    时 ollama candidate 和 ollama judge 共用一个 caller（导致 candidate 文本被当成
    judge 解析结果，winner 永远是 candidate[0]）。
    """
    # 红线：_LOCAL_ONLY_TASKS 任务必须单后端
    if task_kind in _LOCAL_ONLY_TASKS:
        raise ValueError(
            f"task_kind={task_kind} is in _LOCAL_ONLY_TASKS — dual inference forbidden by CLAUDE.md §2"
        )
    if len(candidates) < 1:
        raise ValueError("dual inference needs at least 1 candidate")
    if not all(c in backend_callers for c in candidates):
        missing = [c for c in candidates if c not in backend_callers]
        raise ValueError(f"missing backend_callers for: {missing}")

    # 1. 并发调所有 candidate
    async def _one(backend_name: str) -> CandidateResult:
        t0 = asyncio.get_running_loop().time()
        caller = backend_callers[backend_name]
        try:
            text = await asyncio.wait_for(
                caller(f"dual-{task_kind}-{backend_name}", user_prompt),
                timeout=timeout_sec,
            )
            return CandidateResult(
                backend_name=backend_name,
                text=text,
                latency_ms=int((asyncio.get_running_loop().time() - t0) * 1000),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("dual_candidate_failed backend=%s err=%s", backend_name, e)
            return CandidateResult(
                backend_name=backend_name,
                text="",
                error=str(e),
                latency_ms=int((asyncio.get_running_loop().time() - t0) * 1000),
            )

    results = await asyncio.gather(*[_one(c) for c in candidates])
    successful = [r for r in results if not r.error and r.text]
    if not successful:
        raise RuntimeError(
            f"all {len(candidates)} candidates failed: " +
            "; ".join(f"{r.backend_name}={r.error}" for r in results)
        )

    # 2. 仅 1 个 candidate 成功：直接返回（无需裁判）
    if len(successful) == 1:
        # 用 identity 找到 successful[0] 在 results 里的 index
        try:
            winner_idx_in_results = results.index(successful[0])
        except ValueError:
            winner_idx_in_results = 0
        return DualJudgeResult(
            winner_text=successful[0].text,
            trace=JudgeTrace(
                candidates=results,
                winner_index=winner_idx_in_results,
                winner_text=successful[0].text,
                judge_backend="none",
            ),
        )

    # 3. 裁判选最优（independent judge_caller）
    judge_reason = ""
    winner_idx = -1
    if judge_caller is None:
        # 兜底：本地简单规则（pick longest text）
        winner_idx = max(
            range(len(successful)),
            key=lambda i: len(successful[i].text),
        )
        judge_reason = f"no judge_caller provided; picked longest text (len={len(successful[winner_idx].text)})"
    else:
        try:
            judge_prompt = _build_judge_prompt(
                judge_prompt_template or _DEFAULT_JUDGE_PROMPT,
                user_prompt=user_prompt,
                candidate_texts=[f"{r.backend_name}:\n{r.text}" for r in successful],
            )
            judge_text = await asyncio.wait_for(
                judge_caller(f"judge-{task_kind}", judge_prompt),
                timeout=timeout_sec,
            )
            # 解析 "WINNER: <index>"
            winner_idx, judge_reason = _parse_judge_output(judge_text, len(successful))
        except Exception as e:  # noqa: BLE001
            logger.warning("judge_caller_failed err=%s", e)
            winner_idx = 0  # 兜底：选第一个
            judge_reason = f"judge_caller failed: {e}; picked first"

    winner_idx = max(0, min(winner_idx, len(successful) - 1))
    winner = successful[winner_idx]
    return DualJudgeResult(
        winner_text=winner.text,
        trace=JudgeTrace(
            candidates=results,
            winner_index=winner_idx,
            winner_text=winner.text,
            judge_backend=judge_backend_name,
            judge_reason=judge_reason,
        ),
    )


_DEFAULT_JUDGE_PROMPT = """你是裁判。请对比以下 {len_candidates} 个候选回答，选最准确的（事实正确 + 完整）。

用户问题：
{user_prompt}

候选回答：
{candidates}

只输出 "WINNER: <index>" 一行（index 从 0 开始），可加 1-2 句简短理由。
"""


def _build_judge_prompt(template: str, user_prompt: str, candidate_texts: list[str]) -> str:
    """安全替换占位符：避免 .format 把 prompt 里的 {xxx} 误吞（Python str.format）。"""
    candidates_str = "\n\n".join(
        f"[{i}] {t}" for i, t in enumerate(candidate_texts)
    )
    return (
        template
        .replace("{len_candidates}", str(len(candidate_texts)))
        .replace("{user_prompt}", user_prompt)
        .replace("{candidates}", candidates_str)
    )


def _parse_judge_output(text: str, n_candidates: int) -> tuple[int, str]:
    """解析裁判输出 'WINNER: <idx>'。

    返回 (winner_idx, reason)。越界 idx → clamp 到 [0, n-1]；clamp 后清空 reason
    （防止审计噪声；越界是异常态，不应记 reason）。
    """
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    raw_idx: int | None = None
    reason_lines: list[str] = []
    parsed_winner = False
    for ln in lines:
        up = ln.upper()
        # Match "WINNER:...", "WINNER =...", "WINNER=..." but not "WINNERS:..."
        if re.match(r'^WINNER\s*[:=]', up):
            # 解析 ": <int>" 或 "= <int>"；拒绝负数（避免 "-1" 被误解析为 "1"）
            after = ln.split(":", 1)[-1] if ":" in ln else (ln.split("=", 1)[-1] if "=" in ln else ln)
            after = after.strip()
            # 第一个字符必须是数字；否则不是合法 WINNER（避免 "-1" → "1"）
            if not after or not after[0].isdigit():
                continue
            # 取连续数字
            num_str = ""
            for ch in after:
                if ch.isdigit():
                    num_str += ch
                else:
                    break
            if num_str:
                raw_idx = int(num_str)
                parsed_winner = True
        else:
            # 仅当 idx 有效时收集 reason 行
            if parsed_winner and raw_idx is not None and 0 <= raw_idx < n_candidates:
                reason_lines.append(ln)
    if raw_idx is None:
        logger.warning(
            "judge_output_no_winner_line raw_text=%s — falling back to index 0",
            text[:120],
        )
        return 0, ""
    winner_idx = max(0, min(raw_idx, n_candidates - 1))
    # 越界 clamp 后清空 reason
    if winner_idx != raw_idx:
        reason = ""
    else:
        # 按行累加，在 500 字符边界处截断（不切中间）
        reason_parts: list[str] = []
        total = 0
        for line_text in reason_lines:
            head = f"{line_text}; "
            if total + len(head) > 500:
                reason_parts.append(line_text[:500 - total])
                break
            reason_parts.append(line_text)
            total += len(head)
        reason = "; ".join(reason_parts)
    return winner_idx, reason