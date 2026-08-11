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

from agent.graph.state import AgentState, record_trace

logger = logging.getLogger(__name__)


# ---- 触发关键词 ---------------------------------------------------------

_RAG_TRIGGER_KEYWORDS: tuple[str, ...] = (
    "文档",
    "知识库",
    "参考",
    "规范",
    "说明",
    "是什么",
    "怎么用",
    "如何",
    "doc",
    "knowledge",
    "reference",
    "spec",
    "guide",
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

    if not _should_retrieve(prompt):
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
        ctx = await retriever.retrieve(prompt)
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
            )
        ],
    }
