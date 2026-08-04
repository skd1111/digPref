"""sessions.semantic —— Phase 6 V1 MACC L3 语义记忆（语义规则蒸馏）。

设计（来自 phase-6-session-mgmt.md §2.3）：
- 定期把情景记忆中的高频模式蒸馏为结构化规则
- 每条规则含 pattern（触发模式）+ rule_text（描述）+ confidence（频次归一化）
- V1 启发式蒸馏：相同 (entity, action) 出现 N 次 → 蒸馏一条规则
- V2 接 LLM 做语义泛化

调用方：
- api.py `POST /sessions/{id}/distill-rules` —— 触发蒸馏
- ContextManager V2 L3 语义层
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict
from typing import Any

from agent.sessions.models_macc import SemanticRule
from agent.sessions.storage import SessionStorage

logger = logging.getLogger(__name__)


# ---- 启发式蒸馏 -----------------------------------------------------------

def distill_rules_from_events(
    session_id: str,
    *,
    storage: SessionStorage,
    min_occurrences: int = 3,
    max_rules: int = 20,
) -> list[SemanticRule]:
    """从 session 的事件图谱蒸馏语义规则（启发式）。

    算法：
        1. 统计 (entity, action) 的出现频次
        2. 出现次数 >= min_occurrences 的 → 蒸馏为一条规则
        3. confidence = min(1.0, count / 10)（频次归一化）
        4. rule_text 由模板生成（V2 接 LLM）
        5. upsert 到 storage.semantic_rules
    """
    # 1. 拿 session 内所有事件
    nodes = storage.list_event_nodes(session_id, limit=1000)
    if not nodes:
        return []

    # 2. 频次统计
    freq: dict[tuple[str, str], int] = defaultdict(int)
    for n in nodes:
        key = (n["entity"], n["action"])
        freq[key] += 1

    # 3. 过滤 + 蒸馏
    rules: list[SemanticRule] = []
    for (entity, action), count in sorted(freq.items(), key=lambda t: -t[1]):
        if count < min_occurrences:
            break
        if len(rules) >= max_rules:
            break
        confidence = min(1.0, count / 10.0)
        pattern = _entity_action_to_pattern(entity, action)
        rule_text = _build_rule_text(entity, action, count)
        rule_id = storage.upsert_semantic_rule(
            pattern=pattern,
            rule_text=rule_text,
            session_id=session_id,
            confidence=confidence,
            source_event_ids=[n["id"] for n in nodes
                              if n["entity"] == entity and n["action"] == action][:5],
        )
        rules.append(SemanticRule(
            id=rule_id,
            session_id=session_id,
            pattern=pattern,
            rule_text=rule_text,
            confidence=confidence,
            last_updated=0,
            source_event_ids=[n["id"] for n in nodes
                              if n["entity"] == entity and n["action"] == action][:5],
        ))
    return rules


def _entity_action_to_pattern(entity: str, action: str) -> str:
    """把 (entity, action) 映射为人类可读的 pattern 短语。"""
    # 简化映射：tool.x.invoke → tool_name；其他保持原样
    if entity.startswith("tool."):
        tool_name = entity.split(".", 1)[1]
        return f"tool:{tool_name}"
    if action.startswith(("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER")):
        return f"sql:{action.split()[0].lower()}"
    return f"{entity}:{action}"


def _build_rule_text(entity: str, action: str, count: int) -> str:
    """生成自然语言规则描述（V1 模板；V2 接 LLM 泛化）。"""
    pattern = _entity_action_to_pattern(entity, action)
    if entity.startswith("tool."):
        tool_name = entity.split(".", 1)[1]
        return f"高频调用工具 {tool_name}（{count} 次）；agent 在类似任务中应优先使用 {tool_name} 工具。"
    if action.startswith(("SELECT", "INSERT", "UPDATE", "DELETE")):
        verb = action.split()[0].upper()
        return f"高频执行 {verb} 语句（{count} 次）；agent 在数据 {verb.lower()} 场景下应直接生成 {verb}。"
    return f"高频事件 {pattern}（{count} 次）；agent 在类似上下文应复用该模式。"


# ---- 规则召回 --------------------------------------------------------------


def recall_relevant_rules(
    storage: SessionStorage,
    query: str,
    *,
    top_k: int = 5,
    min_confidence: float = 0.3,
) -> list[dict]:
    """按 query 关键词命中 + confidence 排序召回语义规则。"""
    keywords = _extract_keywords(query)
    all_rules = storage.list_semantic_rules(min_confidence=min_confidence, limit=200)
    if not keywords:
        return all_rules[:top_k]
    scored: list[tuple[float, dict]] = []
    for r in all_rules:
        score = float(r["confidence"])
        # pattern / rule_text 关键词命中加权
        text = (r["pattern"] + " " + r["rule_text"]).lower()
        for kw in keywords:
            if kw.lower() in text:
                score += 0.5
        scored.append((score, r))
    scored.sort(key=lambda t: -t[0])
    return [r for _, r in scored[:top_k]]


def _extract_keywords(text: str) -> list[str]:
    """从 query 抽关键词（中英文混合，CJK ≥ 2 字符 + 英文 ≥ 3 字符）。"""
    if not text:
        return []
    keywords: list[str] = []
    # CJK 片段
    for m in re.finditer(r"[一-鿿]{2,}", text):
        keywords.append(m.group(0))
    # 英文片段
    for m in re.finditer(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b", text):
        keywords.append(m.group(0))
    return list(set(keywords))


