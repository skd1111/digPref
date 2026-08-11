"""local_intent node —— Phase 4 V1 本地小模型意图分类。

设计：
- 与 intent_node 并行存在；本节点用本地小模型（local_small）做快速分类
- 当前实现：可作为 intent_node 的"性能模式"——`inference_mode=='performance'` 时调用
- 默认不在主路径上（compile.py 用普通 intent_node）；本节点可在 settings 开启时替换
- 候选意图与原 intent_node 一致：`query | mutate | orchestrate | chitchat`

CLAUDE.md §2 红线：
- `local_intent` 在 `_LOCAL_ONLY_TASKS` 中（router.py 已加）→ 永远走本地 Ollama / local_small
- 不联网 —— 不调 private LLM

降级链：
1. 优先调 local_small client
2. local_small 不可用 → 调 llm.classify_intent（走 Ollama 候选）
3. 都不可用 → fallback 到关键词分类（与原 intent 一致）
"""

from __future__ import annotations

import logging

from agent.graph.state import AgentState, record_trace

logger = logging.getLogger(__name__)


# ---- 候选意图（与 AgentState.Intent Literal 对齐）------------------------

CANDIDATE_INTENTS: list[str] = ["query", "mutate", "orchestrate", "chitchat"]


# ---- 关键词分类（降级路径）-----------------------------------------------

_KEYWORDS: dict[str, list[str]] = {
    "query": [
        "查",
        "看",
        "列表",
        "统计",
        "搜索",
        "检索",
        "多少",
        "select",
        "show",
        "list",
        "count",
        "get",
    ],
    "mutate": [
        "改",
        "更新",
        "创建",
        "删除",
        "插入",
        "修改",
        "写入",
        "update",
        "delete",
        "insert",
        "create",
        "drop",
    ],
    "orchestrate": [
        "部署",
        "重启",
        "回滚",
        "发布",
        "上线",
        "构建",
        "打包",
        "deploy",
        "restart",
        "rollback",
        "build",
    ],
}


def _fallback_classify(text: str) -> str:
    """最简关键词分类（无 LLM 时的兜底）。"""
    text_lower = text.lower()
    scores: dict[str, int] = dict.fromkeys(CANDIDATE_INTENTS, 0)
    for intent, keywords in _KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                scores[intent] += 1
    best = max(scores.values())
    if best == 0:
        return "chitchat"
    for intent, s in scores.items():
        if s == best:
            return intent
    return "chitchat"


# ---- 节点 ----------------------------------------------------------------


async def local_intent_node(state: AgentState, llm) -> dict:
    """本地意图分类节点。

    Args:
        state: AgentState
        llm: LMRouter（duck-typed —— 任何有 classify_intent 方法的对象）

    Returns:
        AgentState 的部分更新：{"intent": str, "trace": [record_trace(...)], ...}
    """
    prompt = state.get("user_prompt", "")
    if not prompt and state.get("messages"):
        last = state["messages"][-1]
        prompt = getattr(last, "content", None) or (
            last.get("content") if isinstance(last, dict) else ""
        )
    if not prompt:
        return {
            "intent": "chitchat",
            "trace": [record_trace("local_intent", "ok", intent="chitchat", fallback=True)],
        }

    intent: str | None = None
    backend = "fallback"
    try:
        local_client = getattr(llm, "local_small", None)
        if local_client is not None and hasattr(local_client, "classify_intent"):
            intent = await local_client.classify_intent(prompt)
            backend = "local_small"
    except Exception as e:
        logger.debug("local_small classify_intent failed: %s", e)

    if intent is None:
        try:
            if hasattr(llm, "classify_intent"):
                intent = await llm.classify_intent(prompt)
                backend = "ollama"
        except Exception as e:
            logger.debug("llm classify_intent failed: %s", e)

    if intent is None or intent not in CANDIDATE_INTENTS:
        intent = _fallback_classify(prompt)
        backend = "keyword"

    return {
        "intent": intent,
        "trace": [
            record_trace(
                "local_intent",
                "ok",
                intent=intent,
                backend=backend,
            )
        ],
    }
