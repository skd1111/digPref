"""意图识别准确率评测脚本（2026-08-31）。

用法（仓库根目录）：
    # 只评语义路由层（进程内向量模型，无需 LLM，秒级）
    uv run --package agent python test-fixtures/intent_eval/run_eval.py

    # 加评 LLM 结构化分析层（需要本地/内网模型在线）
    uv run --package agent python test-fixtures/intent_eval/run_eval.py --with-llm

    # 结果落盘
    uv run --package agent python test-fixtures/intent_eval/run_eval.py --json results.json

口径：
    route 层（tier=route）：
        正样本：命中期望路由 = 对；落到 None = 「快速路径未覆盖」（回退 LLM，
              不算错，单独统计覆盖率）；命中其它路由 = 错（误触）。
        负样本（hard_negative）：被拦截返 None = 对；命中任何路由 = 错（误触率核心指标）。
    llm 层（tier=llm，--with-llm）：
        intent / intent_category / need_clarification 三项分别比对；
        带 context_dependent 标签的用例（追问短句/依赖上文）单列参考，不计入准确率。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

# Windows GBK 控制台兼容：强制 UTF-8 输出（失败时替换，不中断评测）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent


def load_dataset() -> list[dict]:
    cases = []
    with open(HERE / "dataset.jsonl", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


# ---- Tier A：语义路由层 --------------------------------------------------------


async def eval_route_tier(cases: list[dict]) -> dict:
    from agent.config import settings
    from agent.graph.semantic_route import SemanticIntentRouter

    settings.semantic_route_enabled = True
    router = SemanticIntentRouter()

    rows = []
    for c in cases:
        if c["tier"] != "route":
            continue
        out = await router.route(c["query"])
        got = out.get("_route") if isinstance(out, dict) else None
        score = out.get("_route_score") if isinstance(out, dict) else None
        expect = c.get("expect_route")

        if expect is None:
            verdict = "blocked_ok" if got is None else "FALSE_HIT"
        elif got == expect:
            verdict = "hit_ok"
        elif got is None:
            verdict = "fallback"  # 未覆盖，回退 LLM，不算错
        else:
            verdict = "WRONG_ROUTE"
        rows.append({**c, "got_route": got, "score": score, "verdict": verdict})

    counters = Counter(r["verdict"] for r in rows)
    positives = [r for r in rows if r.get("expect_route")]
    negatives = [r for r in rows if not r.get("expect_route")]
    pos_hit = sum(1 for r in positives if r["verdict"] == "hit_ok")
    neg_block = sum(1 for r in negatives if r["verdict"] == "blocked_ok")
    errors = [r for r in rows if r["verdict"] in ("FALSE_HIT", "WRONG_ROUTE")]

    print("=" * 64)
    print("Tier A · 语义路由层（进程内向量模型）")
    print("=" * 64)
    print(
        f"正样本快速路径命中率 : {pos_hit}/{len(positives)}"
        f"  ({pos_hit / max(len(positives), 1):.1%})"
    )
    print(
        f"负样本拦截率（核心） : {neg_block}/{len(negatives)}"
        f"  ({neg_block / max(len(negatives), 1):.1%})"
    )
    print(f"误触（错误）         : {len(errors)}  |  未覆盖回退: {counters['fallback']}")
    for r in errors:
        print(
            f"  ✗ [{r['id']}] {r['query']!r} → {r['got_route']}"
            f"（score={r['score']}，期望 {r.get('expect_route')}）"
        )
    for r in rows:
        if r["verdict"] == "fallback":
            print(f"  · [{r['id']}] 未覆盖（回退 LLM）: {r['query']!r}")
    return {"rows": rows, "counters": dict(counters)}


# ---- Tier B：LLM 结构化分析层 ----------------------------------------------------


async def eval_llm_tier(cases: list[dict]) -> dict:
    from agent.llm.intent_slots import validate_slots
    from agent.llm.router import LMRouter

    router = LMRouter()
    rows = []
    for c in cases:
        if c["tier"] != "llm":
            continue
        analysis = await router.analyze_intent(c["query"], None, page_context="")
        if isinstance(analysis, dict) and "_route" not in analysis:
            analysis = validate_slots(analysis)

        got_intent = analysis.get("intent") if isinstance(analysis, dict) else None
        got_cat = analysis.get("intent_category") if isinstance(analysis, dict) else None
        got_clarify = (
            bool(analysis.get("need_clarification")) if isinstance(analysis, dict) else False
        )
        backend = analysis.get("backend") if isinstance(analysis, dict) else "?"

        problems = []
        if c.get("expect_intent") and got_intent != c["expect_intent"]:
            problems.append(f"intent={got_intent}≠{c['expect_intent']}")
        if c.get("expect_category") and got_cat != c["expect_category"]:
            problems.append(f"category={got_cat}≠{c['expect_category']}")
        if "expect_clarify" in c and got_clarify != c["expect_clarify"]:
            problems.append(f"clarify={got_clarify}≠{c['expect_clarify']}")

        contextual = "context_dependent" in c.get("tags", [])
        verdict = "ref_only" if contextual else ("ok" if not problems else "MISS")
        rows.append(
            {
                **c,
                "got_intent": got_intent,
                "got_category": got_cat,
                "got_clarify": got_clarify,
                "backend": backend,
                "problems": problems,
                "verdict": verdict,
            }
        )

    scored = [r for r in rows if r["verdict"] != "ref_only"]
    ok = sum(1 for r in scored if r["verdict"] == "ok")
    backends = Counter(r["backend"] for r in rows)
    if backends.get("plain") or backends.get("mock"):
        print("!! 警告：部分用例走了 plain/mock 降级，本地模型可能不在线，结果仅供参考")

    print("=" * 64)
    print("Tier B · LLM 结构化分析层")
    print("=" * 64)
    print(f"计分用例准确率 : {ok}/{len(scored)}  ({ok / max(len(scored), 1):.1%})")
    print(f"后端分布       : {dict(backends)}")
    for r in rows:
        if r["verdict"] == "MISS":
            print(f"  ✗ [{r['id']}] {r['query']!r} → {'; '.join(r['problems'])}")
    ref = [r for r in rows if r["verdict"] == "ref_only"]
    if ref:
        print(f"-- 参考（上下文依赖，不计分）{len(ref)} 条：")
        for r in ref:
            mark = "✓" if not r["problems"] else "△ " + "; ".join(r["problems"])
            print(f"  {mark} [{r['id']}] {r['query']!r} → {r['got_intent']}/{r['got_category']}")
    return {"rows": rows}


# ---- main -----------------------------------------------------------------------


async def amain() -> int:
    parser = argparse.ArgumentParser(description="意图识别准确率评测")
    parser.add_argument("--with-llm", action="store_true", help="加评 LLM 结构化分析层")
    parser.add_argument("--json", metavar="PATH", help="结果落盘（JSON）")
    args = parser.parse_args()

    cases = load_dataset()
    print(f"数据集：{len(cases)} 条（{HERE / 'dataset.jsonl'}）\n")

    result = {"route": await eval_route_tier(cases)}
    if args.with_llm:
        print()
        result["llm"] = await eval_llm_tier(cases)

    if args.json:
        out = Path(args.json)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n结果已写入 {out}")

    route_errors = sum(
        1 for r in result["route"]["rows"] if r["verdict"] in ("FALSE_HIT", "WRONG_ROUTE")
    )
    llm_errors = sum(1 for r in result.get("llm", {}).get("rows", []) if r["verdict"] == "MISS")
    return 1 if (route_errors or llm_errors) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
