"""sessions.compression —— Phase 6 V1 MACC 三层自适应压缩路由器。

设计（来自 phase-6-session-mgmt.md §5）：
- CompressionRouter 决定压缩策略（NONE / WORKING_ONLY / MEMORY / GIST / HYBRID / KV_CACHE）
- L1 物理层：KV Cache 压缩 → 实际由 Phase 13 DSpark 执行（本模块只标记）
- L2 表示层：Gist Token 压缩 → V1 占位（无真编码器）
- L3 逻辑层：工作记忆 + 情景记忆 + 语义记忆（本模块实装）

策略分发矩阵（§5.2）：
  - 短对话（< 20 条消息，< 8K tokens）→ NONE（原文全量）
  - 中等（20-100 条，8K-32K）→ WORKING_ONLY（滑动窗口）
  - 长会话（100-500 条，32K-128K）→ MEMORY（工作 + 情景 + 语义）
  - 超长（> 500 条，> 128K）→ HYBRID（含 L1 标记 → DSpark 接力）
  - 多模态（含图片/日志）→ GIST
  - 空闲（> 5min 无交互）→ MEMORY（后台整理）

CLAUDE.md §6 红线：
- L3 只存元数据（entity / action / 摘要）；原始对话不入库
- compression_log 完整记录策略 + ratio（可观测性 + 未来 PPO 训练数据）
"""
from __future__ import annotations

import logging
import time
from typing import Any

from agent.sessions.models_macc import (
    CompressionContext,
    CompressionResult,
    CompressionStrategy,
    DEFAULT_ANCHORS,
    EventNode,
    GistToken,
    SemanticRule,
)
from agent.sessions.storage import SessionStorage

logger = logging.getLogger(__name__)


# ---- 阈值常量（设计 §5.2）-----------------------------------------------

_TOKEN_THRESHOLDS: dict[str, int] = {
    "tiny": 8_000,         # < 8K → NONE
    "small": 32_000,        # 8K-32K → WORKING_ONLY
    "medium": 128_000,      # 32K-128K → MEMORY
    "large": 1_000_000,     # 128K-1M → HYBRID（含 L1）
}

_MSG_THRESHOLDS: dict[str, int] = {
    "tiny": 20,
    "small": 100,
    "medium": 500,
}

_IDLE_TRIGGER_S = 300  # 设计 §5.2：> 5min 触发后台整理


# ---- CompressionRouter --------------------------------------------------


class CompressionRouter:
    """MACC 三层自适应压缩路由器。

    用法：
        router = CompressionRouter(storage)
        result = router.route(ctx, messages)
        # result.strategy / result.compression_ratio / result.formatted_prompt
    """

    def __init__(
        self,
        storage: SessionStorage,
        *,
        window_size: int = 20,
    ):
        self.storage = storage
        self.window_size = window_size

    def route(
        self,
        ctx: CompressionContext,
        messages: list[dict[str, Any]] | None = None,
    ) -> CompressionResult:
        """根据 ctx 选压缩策略 + 应用压缩 + 返 CompressionResult。"""
        started = time.monotonic()
        messages = messages or []

        # 1. 选策略
        strategy = self._decide_strategy(ctx)

        # 2. 应用压缩（拿到 before / after tokens + 各层结果）
        before = ctx.token_count
        result = self._apply(strategy, ctx, messages)

        # 3. 算 ratio + elapsed
        after = result.after_tokens if result.after_tokens > 0 else before
        result.before_tokens = before
        result.after_tokens = after
        result.compression_ratio = (after / max(1, before)) if before > 0 else 1.0
        result.elapsed_ms = int((time.monotonic() - started) * 1000)

        # 4. 写 compression_log（用于可观测性 + 未来 PPO 训练）
        try:
            self.storage.log_compression(
                session_id=ctx.session_id,
                strategy=strategy,
                before_tokens=before,
                after_tokens=after,
                layers_used=list(result.layers_used),
                elapsed_ms=result.elapsed_ms,
            )
        except Exception as e:
            logger.warning("log_compression failed: %s", e)

        return result

    # ---- 策略决策 ------------------------------------------------------

    def _decide_strategy(self, ctx: CompressionContext) -> CompressionStrategy:
        """按 §5.2 决策矩阵选策略（V0 静态规则；V2 可接 RL）。"""
        # 空闲触发后台整理
        if ctx.idle_time_s >= _IDLE_TRIGGER_S:
            return "MEMORY"
        # 超长 → HYBRID（含 L1）
        if ctx.token_count >= _TOKEN_THRESHOLDS["medium"] and ctx.message_count > _MSG_THRESHOLDS["medium"]:
            return "HYBRID"
        # 多模态 → GIST
        if ctx.has_multimodal and ctx.token_count >= 64_000:
            return "GIST"
        # 长会话 → MEMORY
        if ctx.token_count >= _TOKEN_THRESHOLDS["small"] and ctx.message_count > _MSG_THRESHOLDS["small"]:
            return "MEMORY"
        # 中等 → WORKING_ONLY
        if ctx.token_count >= _TOKEN_THRESHOLDS["tiny"] and ctx.message_count > _MSG_THRESHOLDS["tiny"]:
            return "WORKING_ONLY"
        # 默认 → NONE（短对话）
        return "NONE"

    # ---- 策略应用 ------------------------------------------------------

    def _apply(
        self,
        strategy: CompressionStrategy,
        ctx: CompressionContext,
        messages: list[dict[str, Any]],
    ) -> CompressionResult:
        """按策略应用压缩，构造 CompressionResult。"""
        layers_used: list[str] = []
        result = CompressionResult(
            strategy=strategy,
            before_tokens=0,
            after_tokens=0,
            compression_ratio=1.0,
            layers_used=layers_used,
        )

        if strategy == "NONE":
            # 原文全量
            result.after_tokens = ctx.token_count
            result.formatted_prompt = self._format_no_compression(messages)
            return result

        # L3 工作记忆（所有 L3 策略都跑这一层）
        working = self._build_working_memory(ctx, messages)
        result.working_memory = working  # type: ignore[assignment]
        layers_used.append("L3.WM")
        after_tokens = sum(self._estimate_tokens(w) for w in working)

        if strategy == "WORKING_ONLY":
            result.after_tokens = after_tokens
            result.formatted_prompt = self._format_working_only(working)
            return result

        # L3 情景记忆（MEMORY / HYBRID 才有）
        if strategy in ("MEMORY", "HYBRID"):
            events = self._load_episode_events(ctx)
            result.event_graph_nodes = events
            layers_used.append("L3.EM")
            after_tokens += sum(self._estimate_tokens(self._format_node(n)) for n in events)

            # L3 语义记忆
            rules = self._load_semantic_rules(ctx)
            result.semantic_rules = rules
            layers_used.append("L3.SM")
            after_tokens += sum(self._estimate_tokens(r.rule_text) for r in rules)

        # L2 Gist Token（GIST / HYBRID 才有 —— V1 占位）
        if strategy in ("GIST", "HYBRID"):
            gists = self._placeholder_gist_tokens(ctx)
            result.gist_tokens = gists
            layers_used.append("L2")
            after_tokens += sum(g.token_count for g in gists)

        # L1 KV Cache（HYBRID 标记 —— 实际由 DSpark 执行）
        if strategy == "HYBRID":
            layers_used.append("L1")  # marker only

        result.after_tokens = after_tokens
        result.formatted_prompt = self._format_macc_prompt(
            working=result.working_memory,
            events=result.event_graph_nodes,
            rules=result.semantic_rules,
            gists=result.gist_tokens,
            strategy=strategy,
        )
        return result

    # ---- L3 工作记忆 ----------------------------------------------------

    def _build_working_memory(
        self, ctx: CompressionContext, messages: list[dict[str, Any]],
    ) -> list[str]:
        """滑动窗口 + 关键状态锚点（设计 §2.1）。

        流程：
            1. 取最后 window_size 条消息的 content
            2. 提取关键状态变量（来自 ctx.extra 或 state）—— V1 占位
            3. 拼接为 list[str]（每条 = 1 行工作记忆单元）
        """
        working: list[str] = []
        # 1. 关键状态变量锚点（架构师约定的 4 个节点）
        state_anchors = ctx.extra.get("state_anchors") if ctx.extra else None
        anchors = state_anchors or self._default_anchors_from_ctx(ctx)
        for a in anchors:
            working.append(
                f"[ANCHOR] {a.node_name}: {', '.join(a.vars)}",
            )
        # 2. 滑动窗口
        if messages:
            tail = messages[-self.window_size:]
            for m in tail:
                role = m.get("role", "?")
                content = str(m.get("content", "")).strip()
                if not content:
                    continue
                # 截断单条内容到 200 字符（避免单条过大）
                snippet = content[:200] + ("..." if len(content) > 200 else "")
                working.append(f"[{role}] {snippet}")
        return working

    def _default_anchors_from_ctx(self, ctx: CompressionContext):
        """从 ctx 抽默认锚点（V1 简化：默认返回 DEFAULT_ANCHORS）。"""
        return list(DEFAULT_ANCHORS)

    # ---- L3 情景记忆 ----------------------------------------------------

    def _load_episode_events(self, ctx: CompressionContext) -> list[EventNode]:
        """从 storage 读 session 的事件图谱节点（最多 10 条 + 频次排序）。"""
        nodes = self.storage.list_event_nodes(ctx.session_id, limit=100)
        # 按频次（events 频次粗估 = 列表长度；后续可补真实频次）
        return [
            EventNode(
                id=n["id"],
                session_id=n["session_id"],
                entity=n["entity"],
                action=n["action"],
                result=n.get("result", ""),
                status=n.get("status", "ok"),
                metadata=n.get("metadata", {}),
                created_at=int(n.get("created_at", 0)),
            )
            for n in nodes[:10]
        ]

    # ---- L3 语义记忆 ----------------------------------------------------

    def _load_semantic_rules(self, ctx: CompressionContext) -> list[SemanticRule]:
        """从 storage 读 session 的语义规则（按 confidence 排序 + top 5）。"""
        rules_dicts = self.storage.list_semantic_rules(
            min_confidence=0.3, limit=5,
        )
        return [
            SemanticRule(
                id=r["id"],
                session_id=r["session_id"],
                pattern=r["pattern"],
                rule_text=r["rule_text"],
                confidence=float(r["confidence"]),
                last_updated=int(r["last_updated"]),
                source_event_ids=list(r.get("source_event_ids") or []),
            )
            for r in rules_dicts
        ]

    # ---- L2 Gist Token（占位）-------------------------------------------

    def _placeholder_gist_tokens(self, ctx: CompressionContext) -> list[GistToken]:
        """V1 占位：返回 0 个 gist token（V2 才接真 Perceiver Resampler）。

        Args:
            ctx: CompressionContext（保留接口以便 V2 实装）
        """
        return []

    # ---- Prompt 格式化 --------------------------------------------------

    def _format_no_compression(self, messages: list[dict]) -> str:
        """NONE 策略：原文全量。"""
        lines = ["## 完整上下文（无压缩）"]
        for m in messages[-50:]:  # 上限 50 条
            lines.append(f"[{m.get('role', '?')}] {str(m.get('content', ''))[:500]}")
        return "\n".join(lines)

    def _format_working_only(self, working: list[str]) -> str:
        return "[L3 Working Memory]\n" + "\n".join(working)

    def _format_node(self, n: EventNode) -> str:
        return f"[{n.entity}] {n.action} → {n.status}"

    def _format_macc_prompt(
        self,
        *,
        working,
        events,
        rules,
        gists,
        strategy,
    ) -> str:
        """MACC 多层拼装（设计 §6）。"""
        parts: list[str] = [f"[MACC strategy={strategy}]"]
        if rules:
            parts.append("\n## Semantic Memory (L3)")
            for r in rules:
                parts.append(f"- {r.pattern}: {r.rule_text} (conf={r.confidence:.2f})")
        if gists:
            parts.append("\n## Gist Tokens (L2)")
            parts.append(f"- {len(gists)} tokens")
        if events:
            parts.append("\n## Event Graph (L3)")
            for e in events:
                parts.append(self._format_node(e))
        if working:
            parts.append("\n## Working Memory (L3)")
            parts.extend(working)
        return "\n".join(parts)

    # ---- 工具 -----------------------------------------------------------

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """粗估 token（4 字符 ≈ 1 token）。"""
        return max(1, len(text or "") // 4)