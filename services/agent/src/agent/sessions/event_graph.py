"""sessions.event_graph —— Phase 6 V1 MACC L3 情景记忆（事件图谱）。

设计（来自 phase-6-session-mgmt.md §2.2）：
- 从历史消息 / 工具调用轨迹抽取 (entity, action, result, status)
- LLM 优先；LLM 不可用 → 启发式正则兜底（mock 路径）
- BFS 路径扩展（`SessionStorage.bfs_recall_episode`）按 seed_entities 找相关历史

CLAUDE.md §6 红线：
- L3 事件图谱只存元数据（entity / action / 摘要），不存原始对话文本
- 即便跨会话聚合 entity 频次，也不写任何 PII / 业务敏感负载

调用方：
- api.py `POST /sessions/{id}/extract-events` —— 触发 LLM 提取
- api.py `POST /sessions/{id}/recall-episode` —— BFS 检索
- ContextManager V2 L3 压缩路径
"""

from __future__ import annotations

import logging
import re
from typing import Any

from agent.sessions.models_macc import EventNode
from agent.sessions.storage import SessionStorage

logger = logging.getLogger(__name__)


# ---- 启发式提取（无 LLM 时的兜底）-----------------------------------------

# SQL 模式：<VERB> <entity> —— 简单抽出 (entity, action)
_SQL_VERBS: tuple[str, ...] = (
    "select",
    "insert",
    "update",
    "delete",
    "create",
    "drop",
    "alter",
)
# 匹配 tool_call / tool: / mcp_xxx 多种 tool 调用模式
_TOOL_PATTERN = re.compile(
    r"\b(tool_call|tool|mcp[\w_-]+)\s*[:=.]?\s*([a-zA-Z_]\w*)",
    re.IGNORECASE,
)


def heuristic_extract_from_messages(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    storage: SessionStorage,
) -> list[EventNode]:
    """启发式从消息轨迹抽取事件节点（无 LLM 时的兜底）。

    规则：
        - user 消息含 SELECT / UPDATE 等 SQL → 1 节点
        - assistant 消息含 tool_call → 1 节点
        - tool 消息含 tool_result → 1 节点（关联最近 tool_call）
    """
    nodes: list[EventNode] = []
    last_tool_call_event: EventNode | None = None

    for msg in messages:
        role = msg.get("role", "")
        content = str(msg.get("content", ""))
        if not content:
            continue

        # 1. tool_call 模式优先（assistant role 含 tool_call: xxx）—— 避免被 SQL 模式截胡
        if role == "assistant" and "tool_call" in content.lower():
            tm = _TOOL_PATTERN.search(content)
            if tm:
                tool_name = tm.group(2)
                node = EventNode.new(
                    session_id=session_id,
                    entity=f"tool.{tool_name}",
                    action="invoke",
                    result="",
                    status="ok",
                    metadata={"source": "heuristic", "role": role},
                )
                nodes.append(node)
                last_tool_call_event = node
            continue

        # 2. SQL 模式
        sql_match = re.search(
            r"\b(" + "|".join(_SQL_VERBS) + r")\s+((?:from|into|table)\s+\S+|\S+)",
            content,
            re.IGNORECASE,
        )
        if sql_match:
            verb = sql_match.group(1).upper()
            target = sql_match.group(2).strip().rstrip(",;")
            nodes.append(
                EventNode.new(
                    session_id=session_id,
                    entity=target,
                    action=f"{verb} (user request)",
                    result="",
                    status="ok",
                    metadata={"source": "heuristic", "role": role},
                )
            )
            continue

        # 3. tool_result 模式（关联最近 tool_call）
        if role == "tool" and last_tool_call_event is not None:
            status = "ok" if "error" not in content.lower() else "error"
            result = content[:200].strip()
            last_tool_call_event.result = result
            last_tool_call_event.status = status
            continue

    return nodes


# ---- BFS 召回 --------------------------------------------------------------


def recall_episode(
    storage: SessionStorage,
    session_id: str,
    *,
    query: str,
    max_hops: int = 2,
    max_nodes: int = 10,
    entity_keywords: list[str] | None = None,
) -> list[dict]:
    """从事件图谱召回与 query 相关的历史事件。

    流程：
        1. 从 query 中提取 entity 候选（启发式：表名 / 工具名）
        2. 用 BFS 从这些实体扩展
        3. 返 top-k 节点
    """
    seeds = list(entity_keywords or [])
    if not seeds:
        # 启发式：从 query 抽大写 token + 工具名前缀
        seeds = _extract_seed_entities(query)
    if not seeds:
        return []
    return storage.bfs_recall_episode(
        session_id,
        seed_entities=seeds,
        max_hops=max_hops,
        max_nodes=max_nodes,
    )


def _extract_seed_entities(query: str) -> list[str]:
    """从 query 抽 seed entity 候选。

    启发式：
        - 大写 / 含 _ 的 token（表名 / 函数名 / 工具名）
        - `t_<word>` 模式（金融常见表名）
        - 单个英文 token ≥ 4 字符（如 "orders" / "users"）
    """
    if not query:
        return []
    candidates = set()
    # 1. 高优先级：含 _ 或 t_ 前缀
    for m in re.finditer(r"\b(t_\w+|[A-Z][a-zA-Z]+(?:_[a-zA-Z]+)+|[a-z]+_[a-z]+)\b", query):
        candidates.add(m.group(1))
    # 2. 中优先级：单个英文 token ≥ 4 字符
    for m in re.finditer(r"\b[a-z]{4,}\b", query):
        candidates.add(m.group(0))
    return list(candidates)


# ---- LLM 抽取（V1 占位 —— V1.5 接本地 0.3B）---------------------------------


async def extract_events_with_llm(
    session_id: str,
    messages: list[dict[str, Any]],
    *,
    storage: SessionStorage,
    llm: Any = None,
) -> list[EventNode]:
    """用 LLM 抽取事件节点（V1 占位 —— 无 LLM 时退化到启发式）。

    Returns:
        EventNode 列表；已写入 storage.event_graph_nodes
    """
    nodes: list[EventNode] = []
    if llm is not None and hasattr(llm, "extract_events"):
        # V1.5 接本地 0.3B
        try:
            extracted = await llm.extract_events(messages)
            for item in extracted:
                nodes.append(
                    EventNode.new(
                        session_id=session_id,
                        entity=str(item.get("entity", "")),
                        action=str(item.get("action", "")),
                        result=str(item.get("result", "")),
                        status=item.get("status", "ok"),
                        metadata={"source": "llm"},
                    )
                )
        except Exception as e:
            logger.warning("LLM extract_events failed, fallback heuristic: %s", e)
            nodes = heuristic_extract_from_messages(
                session_id,
                messages,
                storage=storage,
            )
    else:
        nodes = heuristic_extract_from_messages(
            session_id,
            messages,
            storage=storage,
        )

    # 写入 storage
    if nodes:
        for n in nodes:
            storage.insert_event_node(
                session_id=n.session_id,
                entity=n.entity,
                action=n.action,
                result=n.result,
                status=n.status,
                metadata=n.metadata,
                node_id=n.id,
            )
    return nodes


# ---- 序列化 / 反序列化 helper ----------------------------------------------


def event_node_from_dict(d: dict[str, Any]) -> EventNode:
    """从 dict 构造（兼容 DB row / API JSON）。"""
    return EventNode(
        id=str(d.get("id", "")),
        session_id=str(d.get("session_id", "")),
        entity=str(d.get("entity", "")),
        action=str(d.get("action", "")),
        result=str(d.get("result", "")),
        status=d.get("status", "ok"),
        metadata=dict(d.get("metadata") or {}),
        created_at=int(d.get("created_at", 0) or 0),
    )


def serialize_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    """序列化为 GraphML 风格 dict（便于返回前端 / 持久化）。"""
    return {
        "nodes": [
            {
                "id": n["id"],
                "entity": n["entity"],
                "action": n["action"],
                "result": n.get("result", ""),
                "status": n.get("status", "ok"),
                "hops": n.get("hops", 0),
                "metadata": n.get("metadata", {}),
                "created_at": n["created_at"],
            }
            for n in nodes
        ],
        "edges": [
            {
                "from": e["from_node"],
                "to": e["to_node"],
                "relation": e["relation"],
            }
            for e in edges
        ],
        "node_count": len(nodes),
        "edge_count": len(edges),
    }
