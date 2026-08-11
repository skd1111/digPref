"""bench_dspark.py —— Phase 13 V1.5 DSpark 基准测试脚本。

设计（来自 phase-13-dspark.md §9 验收）：
- 5 类任务场景：sql_generation / code_completion / log_analysis / chat_qa / complex_reasoning
- 测两个指标：加速比（speedup_ratio）+ Token 接受率（acceptance rate）
- 输出：JSON 报告 → stdout + 写入 `bench_dspark_report.json`

用法：
    cd services/agent && uv run pytest tests/bench_dspark.py
    # 或：
    uv run python -m agent.tests.bench_dspark  # 无依赖（脚本）
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from agent.llm.dspark.llamacpp_backend import MockDSparkBackend


@dataclass
class BenchScenario:
    """单个测试场景。"""

    name: str
    task_category: str
    prompt: str
    max_tokens: int
    expected_mode: str  # 'aggressive' / 'standard' / 'conservative' / 'off'


SCENARIOS: list[BenchScenario] = [
    BenchScenario(
        name="sql_generation",
        task_category="sql_generation",
        prompt="生成查订单的 SELECT 语句",
        max_tokens=300,
        expected_mode="aggressive",
    ),
    BenchScenario(
        name="code_completion",
        task_category="code_completion",
        prompt="写 Python 函数",
        max_tokens=300,
        expected_mode="aggressive",
    ),
    BenchScenario(
        name="log_analysis",
        task_category="log_analysis",
        prompt="分析日志异常",
        max_tokens=500,
        expected_mode="standard",
    ),
    BenchScenario(
        name="chat_qa",
        task_category="chat_qa",
        prompt="你好",
        max_tokens=100,
        expected_mode="conservative",
    ),
    BenchScenario(
        name="complex_reasoning",
        task_category="complex_reasoning",
        prompt="复杂推理",
        max_tokens=800,
        expected_mode="off",
    ),
]


@dataclass
class BenchResult:
    """单个场景的基准结果。"""

    scenario: str
    task_category: str
    expected_mode: str
    actual_mode: str
    speedup_ratio: float
    drafted_tokens: int
    accepted_tokens: int
    acceptance_rate: float  # accepted / drafted
    duration_ms: int
    meets_threshold: bool  # 是否达到验收阈值（≥ 50% CPU / ≥ 40% GPU）


_THRESHOLD_CPU = 1.5  # MockDSparkBackend 用 2.0x；真实 GPU/CPU 期望 ≥ 1.5x 加速
_THRESHOLD_ACCEPTANCE = 0.6  # 接受率 ≥ 60% 达标


async def run_scenario(scenario: BenchScenario, backend: MockDSparkBackend) -> BenchResult:
    """跑一个场景。"""
    # DSpark 启用（带 draft model）
    r_dspark = await backend.generate(
        prompt=scenario.prompt,
        max_tokens=scenario.max_tokens,
        temperature=0.0,
        task_category=scenario.task_category,
        n_draft=8
        if scenario.expected_mode == "aggressive"
        else 4
        if scenario.expected_mode == "standard"
        else 2
        if scenario.expected_mode == "conservative"
        else 1,
        draft_p_min=0.75
        if scenario.expected_mode == "aggressive"
        else 0.85
        if scenario.expected_mode == "standard"
        else 0.90
        if scenario.expected_mode == "conservative"
        else 1.0,
        draft_model_path="models/draft/qwen2.5-0.1b-instruct-q4_k_m.gguf",
    )
    # 基线：禁用 DSpark（n_draft=1, draft_p_min=1.0）
    r_baseline = await backend.generate(
        prompt=scenario.prompt,
        max_tokens=scenario.max_tokens,
        temperature=0.0,
        task_category=scenario.task_category,
        n_draft=1,
        draft_p_min=1.0,
        draft_model_path=None,
    )
    # 加速比 = baseline_time / dspark_time（越大越好）
    if r_dspark.duration_ms > 0:
        speedup = r_baseline.duration_ms / r_dspark.duration_ms
    else:
        speedup = 1.0
    accepted = r_dspark.accepted_tokens
    drafted = r_dspark.drafted_tokens or scenario.max_tokens
    accept_rate = accepted / max(1, drafted)
    meets = speedup >= _THRESHOLD_CPU and accept_rate >= _THRESHOLD_ACCEPTANCE
    return BenchResult(
        scenario=scenario.name,
        task_category=scenario.task_category,
        expected_mode=scenario.expected_mode,
        actual_mode=r_dspark.backend,
        speedup_ratio=round(speedup, 2),
        drafted_tokens=drafted,
        accepted_tokens=accepted,
        acceptance_rate=round(accept_rate, 2),
        duration_ms=r_dspark.duration_ms,
        meets_threshold=meets,
    )


async def main() -> int:
    """跑全部 5 场景 + 输出 JSON 报告。"""
    backend = MockDSparkBackend(fixed_output="bench response", mock_speedup=2.0)
    print(f"[bench] backend = {type(backend).__name__} (DSpark enabled)")
    print(f"[bench] running {len(SCENARIOS)} scenarios...\n")

    results: list[BenchResult] = []
    for s in SCENARIOS:
        r = await run_scenario(s, backend)
        results.append(r)
        marker = "[PASS]" if r.meets_threshold else "[FAIL]"
        print(
            f"  {r.scenario:20s} mode={r.expected_mode:12s} "
            f"speedup={r.speedup_ratio:.2f}x  "
            f"accept={r.acceptance_rate:.0%}  "
            f"{marker}"
        )

    # 汇总
    passed = sum(1 for r in results if r.meets_threshold)
    avg_speedup = sum(r.speedup_ratio for r in results) / len(results)
    avg_accept = sum(r.acceptance_rate for r in results) / len(results)
    print()
    print(f"[bench] {passed}/{len(results)} scenarios meet threshold")
    print(f"[bench] avg speedup = {avg_speedup:.2f}x")
    print(f"[bench] avg acceptance = {avg_accept:.0%}")

    # 写入报告
    report_path = Path(__file__).parent / "bench_dspark_report.json"
    report_path.write_text(
        json.dumps(
            {
                "summary": {
                    "passed": passed,
                    "total": len(results),
                    "avg_speedup_ratio": round(avg_speedup, 2),
                    "avg_acceptance_rate": round(avg_accept, 2),
                    "thresholds": {
                        "speedup_cpu_min": _THRESHOLD_CPU,
                        "acceptance_rate_min": _THRESHOLD_ACCEPTANCE,
                    },
                },
                "scenarios": [asdict(r) for r in results],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"[bench] report written to {report_path}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    import sys

    sys.exit(asyncio.run(main()))
