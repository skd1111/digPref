"""bench-retrieval —— 本地知识库混合检索延迟 bench（只读，不写 kb.db）。

用途：面试数字口径 / 性能回归基线。在仓库根目录执行（model/ 相对路径需命中）：
    uv run python infra/scripts/bench-retrieval.py --db <kb.db 路径> [--n 30] [--top-k 5]

输出 JSON：库规模（docs/chunks/parents）+ 每查询 total_ms / rerank_ms + p50/p95。
首查作冷启动预热不计入统计（与生产 lifespan 预加载语义对齐）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

QUERIES = [
    "对公转账汇兑的规章制度",
    "差旅费报销标准是多少",
    "招待费审批流程需要什么材料",
    "数据安全管理规范中对密码的要求",
    "员工手册里年假怎么计算",
    "合同用印审批需要经过哪些节点",
    "跨境支付的外汇审查要求",
    "报销凭证的留存年限规定",
]


def _pct(xs: list[float], k: int) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    i = max(0, min(len(xs) - 1, round((k / 100) * (len(xs) - 1))))
    return round(xs[i], 1)


async def _run(db: str, n: int, top_k: int) -> dict:
    from agent.knowledge.hybrid_rag import HybridRetriever
    from agent.knowledge.reranker import get_reranker_client
    from agent.knowledge.retriever import build_default_embedding_client
    from agent.knowledge.storage import KnowledgeStorage

    storage = KnowledgeStorage(db)
    stats = storage.get_stats()
    embedding = build_default_embedding_client()
    reranker = get_reranker_client()
    retriever = HybridRetriever(storage=storage, embedding=embedding, reranker=reranker)

    # 冷启动预热（不计入统计）
    await retriever.search(QUERIES[0], top_k=top_k)

    qs = (QUERIES * (n // len(QUERIES) + 1))[:n]
    totals: list[float] = []
    reranks: list[float] = []
    for q in qs:
        t0 = time.perf_counter()
        await retriever.search(q, top_k=top_k)
        totals.append((time.perf_counter() - t0) * 1000)
        reranks.append(float(getattr(retriever, "last_rerank_ms", 0) or 0))

    return {
        "db": db,
        "docs": getattr(stats, "total_docs", None),
        "chunks": getattr(stats, "total_chunks", None),
        "parents": getattr(stats, "total_parents", None),
        "n": n,
        "top_k": top_k,
        "embedding": "in-process ONNX bge-small-zh" if embedding is not None else None,
        "reranker": "in-process ONNX bge-reranker(quantized)" if reranker is not None else None,
        "total_ms_p50": _pct(totals, 50),
        "total_ms_p95": _pct(totals, 95),
        "rerank_ms_p50": _pct(reranks, 50),
        "rerank_ms_p95": _pct(reranks, 95),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="kb.db 路径（只读打开）")
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--top-k", type=int, default=5)
    args = ap.parse_args()
    print(
        json.dumps(
            asyncio.run(_run(str(Path(args.db)), args.n, args.top_k)), ensure_ascii=False, indent=2
        )
    )


if __name__ == "__main__":
    main()
