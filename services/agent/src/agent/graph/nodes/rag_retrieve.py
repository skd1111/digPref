"""rag_retrieve node —— Phase 4 V1 RAG 检索增强节点。

设计：
- 在 intent → planner 之间插入（可选）
- 检测 user_query 是否需要知识库（关键词 / 显式 trigger）
- 调 RAGRetriever.retrieve() → RAGContext
- 把 formatted_prompt 写入 state.system_prompt_addon（planner/responder 后续用）
- 失败兜底：embedding 不可用 → 退化到 LIKE；都失败 → state 不变（best-effort，不阻塞）

CLAUDE.md §2 红线：
- RAG 检索不触及 `_LOCAL_ONLY_TASKS`（路径上是 planner/responder 在跑 LLM）
- 不引入写操作 → 不需 HITL
- 不写 audit.sqlite（V1 留作 V2 扩展点）
"""

from __future__ import annotations

import logging
from typing import Any

from agent.graph.state import AgentState, record_trace

logger = logging.getLogger(__name__)


# ---- 触发关键词 ---------------------------------------------------------

_RAG_TRIGGER_KEYWORDS: tuple[str, ...] = (
    "文档",
    "知识库",
    "参考",
    "规范",
    "说明",
    "合规",
    "审核",
    "制度",
    "法规",
    "条款",
    "政策",
    "规定",
    "标准",
    "风险",
    "报销",
    "流程",
    "是什么",
    "怎么用",
    "如何",
    "doc",
    "knowledge",
    "reference",
    "spec",
    "guide",
    "compliance",
    "policy",
    "regulation",
    "audit",
    "risk",
    "how to",
    "what is",
)


def _should_retrieve(prompt: str) -> bool:
    """简单判断是否值得走 RAG 检索。"""
    p = (prompt or "").strip().lower()
    if not p:
        return False
    if any(kw in p for kw in _RAG_TRIGGER_KEYWORDS):
        return True
    if len(p) >= 12:
        return True
    return False


def _augment_followup_query(query: str, state: AgentState) -> str:
    """短追问拼上一轮主题（会话式查询改写，仅用于检索，无任何副作用）。

    「知识库里没有吗」这类追问，即使意图改写后仍可能很短、缺真实检索
    对象；取最近一条更早的用户轮主题拼接，让 BM25/向量拿到「对公转账汇兑
    规章制度」这类实质词。只在检索词短（<12 字）时触发。
    """
    q = (query or "").strip()
    if len(q) >= 12:
        return q
    try:
        from agent.llm.prompts import normalize_message
    except Exception:  # 归一入口不可用→不拼，保底原查询
        return q
    user_prompt = str(state.get("user_prompt") or "").strip()
    for msg in reversed(list(state.get("messages") or [])):
        parsed = normalize_message(msg)
        if parsed is None:
            continue
        role, text = parsed
        if role != "user" or text in (q, user_prompt):
            continue  # 跳过非用户轮与当轮输入
        if len(text) > len(q):
            return f"{text[:60].strip()} {q}".strip()
    return q


# ---- 节点 ----------------------------------------------------------------


async def rag_retrieve_node(state: AgentState, retriever=None) -> dict:
    """RAG 检索节点。

    Args:
        state: AgentState
        retriever: RAGRetriever 实例（duck-typed）
                   None 时降级为 best-effort noop
    """
    prompt = state.get("user_prompt", "")
    if not prompt and state.get("messages"):
        last = state["messages"][-1]
        prompt = getattr(last, "content", None) or (
            last.get("content") if isinstance(last, dict) else ""
        )

    # 检索词优选（根因修复 2026-09-04）：意图改写句携带了追问的真实主题
    # （如「知识库里没有吗」→「在知识库中查找是否有对公转账汇兑相关的规章制度
    # 文档」），用它检索才不会退化到只匹配「知识库」这种泛词（日志 query_chars=7
    # bm25=15）；改写句仍短则拼上一轮主题。
    query = str(state.get("rewritten_query") or "").strip() or str(prompt or "")
    query = _augment_followup_query(query, state)

    if not _should_retrieve(query):
        return {
            "rag_context": None,
            "system_prompt_addon": "",
            "trace": [
                record_trace(
                    "rag_retrieve",
                    "skipped",
                    reason="no trigger keywords",
                )
            ],
        }

    if retriever is None:
        try:
            from agent.knowledge.retriever import get_default_retriever

            retriever = get_default_retriever()
        except Exception as e:
            logger.debug("default retriever unavailable: %s", e)
            return {
                "rag_context": None,
                "system_prompt_addon": "",
                "trace": [
                    record_trace(
                        "rag_retrieve",
                        "skipped",
                        reason="no retriever",
                    )
                ],
            }

    try:
        ctx = await retriever.retrieve(query)
    except Exception as e:
        logger.warning("rag retrieve failed: %s", e)
        return {
            "rag_context": None,
            "system_prompt_addon": "",
            "trace": [
                record_trace(
                    "rag_retrieve",
                    "fail",
                    error=str(e),
                )
            ],
        }

    if not ctx.results:
        return {
            "rag_context": None,
            "system_prompt_addon": "",
            "trace": [
                record_trace(
                    "rag_retrieve",
                    "ok",
                    results_count=0,
                    elapsed_ms=ctx.elapsed_ms,
                    backend=ctx.backend,
                )
            ],
        }

    refs: list[dict[str, Any]] = []
    for r in ctx.results[:8]:
        meta = getattr(r.chunk, "metadata", {}) or {}
        refs.append(
            {
                "source": str(meta.get("source") or r.citation or ""),
                "doc_title": r.doc_title,
                "snippet": str(meta.get("child_content") or r.chunk.content or "")[:200],
                "page_no": int(meta.get("page_no", 1) or 1),
                "matched": list(meta.get("matched", []) or [])[:8],
            }
        )

    return {
        "rag_context": ctx,
        "system_prompt_addon": ctx.formatted_prompt,
        "trace": [
            record_trace(
                "rag_retrieve",
                "ok",
                results_count=len(ctx.results),
                elapsed_ms=ctx.elapsed_ms,
                backend=ctx.backend,
                refs=refs,
            )
        ],
    }
