"""orchestrator.context_strategy —— Phase 12 V1.5 三类场景化上下文传递策略。

设计文档 §2.1 / 铁律 4 + 5：

| 场景 | strategy | 传递内容 |
|---|---|---|
| 简单 / 短任务 | `passthrough` | 结构化 input/output + 状态枚举；不传主会话历史 |
| 中等协作 | `shared_memory_pool` | 共享事实键值对 + 版本号；每个子 Agent 只读自己声明的 key |
| 长会话 / 复杂任务 | `incremental_summary` | 结构化摘要（≤ max_summary_tokens）+ 增量 delta + raw_refs |

统一原则：
    - **最小必要**：默认只传当前任务所需信息，不广播完整主会话
    - **必读字段不可压**：`ContextPolicy.required_fields` 在任何策略下原样保留
    - **原文外置**：超长值不进 prompt，转 `ArtifactRef`（content_hash 可追溯）
    - **可追溯**：每个 artifact 携 SHA-256 + byte_size + preview

本模块不依赖 LLM，可纯函数式单测（压缩率 / 必读字段保留率是 CI 指标）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from agent.orchestrator.spec import ArtifactRef, ContextPolicy, SubAgentSpec
from agent.prompts import SUBAGENT_EXECUTION_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

# 单个非必读字段进 prompt 的最大字符数（超出 → 外置成 ArtifactRef）
MAX_INLINE_VALUE_CHARS = 400
# passthrough 策略的 prompt 软上限（token）
PASSTHROUGH_SOFT_TOKEN_LIMIT = 200


def estimate_tokens(text: str) -> int:
    """中英混合 token 粗估（与 knowledge/chunker.estimate_tokens 同口径）。

    ASCII 约 4 字符 / token；CJK 约 1 字符 / token。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    ascii_like = len(text) - cjk
    return int(cjk + ascii_like / 4) + 1


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


# ---- 组装结果 --------------------------------------------------------------


@dataclass
class ComposedContext:
    """上下文组装结果 —— Orchestrator 拿 prompt，评测拿 metrics。"""

    prompt: str
    strategy: str
    tokens_before: int
    tokens_after: int
    required_fields_kept: bool = True
    missing_required_fields: list[str] = field(default_factory=list)
    raw_refs: list[ArtifactRef] = field(default_factory=list)
    shared_facts_used: list[str] = field(default_factory=list)

    @property
    def compression_ratio(self) -> float:
        """token 节省比（0 = 没压缩；0.6 = 省了 60%）。"""
        if self.tokens_before <= 0:
            return 0.0
        saved = self.tokens_before - self.tokens_after
        return max(0.0, min(1.0, saved / self.tokens_before))

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "compression_ratio": round(self.compression_ratio, 4),
            "required_fields_kept": self.required_fields_kept,
            "missing_required_fields": list(self.missing_required_fields),
            "raw_refs": [r.model_dump(mode="json") for r in self.raw_refs],
            "shared_facts_used": list(self.shared_facts_used),
        }


# ---- 共享记忆池（中等协作场景）-----------------------------------------------


@dataclass
class SharedFact:
    """共享事实：值 + 版本号 + content_hash（铁律 4 可追溯）。"""

    key: str
    value: Any
    version: int = 1
    content_hash: str = ""
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "version": self.version,
            "content_hash": self.content_hash,
            "updated_at": self.updated_at,
        }


class SharedMemoryPool:
    """按 run_id 隔离的共享记忆池（进程内；架构决策 2026-07-31 不引入 Redis）。

    - 同一批次的多个同级子 Agent 共享 fact；每个子 Agent 只读自己声明的 key
    - 每次 set 版本号 +1，保证子 Agent 读到一致快照（版本可追溯）
    """

    def __init__(self) -> None:
        self._pools: dict[str, dict[str, SharedFact]] = {}

    def set_fact(self, run_id: str, key: str, value: Any) -> SharedFact:
        pool = self._pools.setdefault(run_id, {})
        prev = pool.get(key)
        fact = SharedFact(
            key=key,
            value=value,
            version=(prev.version + 1) if prev else 1,
            content_hash=_sha256(_stringify(value)),
        )
        pool[key] = fact
        return fact

    def get_fact(self, run_id: str, key: str) -> SharedFact | None:
        return self._pools.get(run_id, {}).get(key)

    def get_many(self, run_id: str, keys: list[str]) -> dict[str, SharedFact]:
        pool = self._pools.get(run_id, {})
        return {k: pool[k] for k in keys if k in pool}

    def snapshot(self, run_id: str) -> dict[str, dict[str, Any]]:
        return {k: f.to_dict() for k, f in self._pools.get(run_id, {}).items()}

    def clear(self, run_id: str | None = None) -> None:
        if run_id is None:
            self._pools.clear()
        else:
            self._pools.pop(run_id, None)


_default_pool: SharedMemoryPool | None = None


def get_default_pool() -> SharedMemoryPool:
    global _default_pool
    if _default_pool is None:
        _default_pool = SharedMemoryPool()
    return _default_pool


def reset_default_pool() -> None:
    """测试 hook。"""
    global _default_pool
    _default_pool = None


# ---- 策略自动选型 ----------------------------------------------------------


def select_strategy(spec: SubAgentSpec, *, raw_tokens: int | None = None) -> str:
    """按场景自动选策略（显式声明优先，其次按规模启发式）。

    规则（设计文档 §2.1）：
      1. spec.context_policy.strategy 非 passthrough → 尊重显式声明
      2. 声明了 shared_keys → shared_memory_pool
      3. 估算 token > 2000（或 depth ≥ 2 的长链）→ incremental_summary
      4. 其余 → passthrough
    """
    policy = spec.context_policy
    if policy.strategy != "passthrough":
        return policy.strategy
    if policy.shared_keys:
        return "shared_memory_pool"
    tokens = (
        raw_tokens if raw_tokens is not None else estimate_tokens(_stringify(spec.input_payload))
    )
    if tokens > 2000:
        return "incremental_summary"
    return "passthrough"


# ---- prompt 组装 -----------------------------------------------------------


def _header(spec: SubAgentSpec, strategy: str) -> list[str]:
    return [
        f"任务类型: {spec.task_type}",
        f"任务描述: {spec.task_description}",
        f"上下文策略: {strategy}",
        "",
    ]


def _render_required(policy: ContextPolicy, payload: dict[str, Any]) -> list[str]:
    """必读字段原样渲染（铁律 5：任何压缩策略下都不可压）。"""
    if not policy.required_fields:
        return []
    lines = ["必读字段（不可压缩 / 不可忽略）:"]
    for name in policy.required_fields:
        if name in payload:
            lines.append(f"  - {name}: {_stringify(payload[name])}")
        else:
            lines.append(f"  - {name}: <缺失>")
    lines.append("")
    return lines


def _render_payload(
    payload: dict[str, Any],
    policy: ContextPolicy,
    *,
    inline_limit: int,
    raw_refs: list[ArtifactRef],
) -> list[str]:
    """渲染非必读字段：超长值外置成 ArtifactRef，只留 preview。"""
    required = set(policy.required_fields)
    lines: list[str] = ["输入 payload:"]
    for key, value in payload.items():
        if key in required:
            continue  # 已在必读区渲染，避免重复占 token
        text = _stringify(value)
        if len(text) > inline_limit:
            digest = _sha256(text)
            ref = ArtifactRef(
                artifact_id=f"raw:{key}:{digest[:12]}",
                kind="raw_text",
                content_hash=digest,
                byte_size=len(text.encode("utf-8", errors="replace")),
                preview=text[:100],
            )
            raw_refs.append(ref)
            lines.append(
                f"  - {key}: <原文已外置 artifact={ref.artifact_id} "
                f"bytes={ref.byte_size}> 预览: {ref.preview}"
            )
        else:
            lines.append(f"  - {key}: {text}")
    if len(lines) == 1:
        lines.append("  （无）")
    lines.append("")
    return lines


def _render_execution_template(fields: dict[str, Any]) -> str:
    """按「子智能体执行提示词模板」填充子 Agent prompt。"""
    return (
        SUBAGENT_EXECUTION_PROMPT_TEMPLATE.replace(
            "{{SUBAGENT_NAME}}", str(fields.get("name") or "")
        )
        .replace("{{SUBAGENT_ROLE}}", str(fields.get("role") or ""))
        .replace("{{USER_GOAL}}", str(fields.get("user_goal") or ""))
        .replace("{{TASK}}", str(fields.get("task") or ""))
        .replace(
            "{{INPUTS}}",
            json.dumps(fields.get("inputs") or {}, ensure_ascii=False),
        )
        .replace(
            "{{ALLOWED_TOOLS}}",
            json.dumps(fields.get("allowed_tools") or [], ensure_ascii=False),
        )
        .replace("{{EXPECTED_OUTPUT}}", str(fields.get("expected_output") or ""))
        .replace("{{STOP_CONDITION}}", str(fields.get("stop_condition") or ""))
        .replace(
            "{{SAFETY_POLICY}}",
            json.dumps(fields.get("safety_policy") or {}, ensure_ascii=False),
        )
    )


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    """按 token 估算截断（保留头部；尾部标注截断）。"""
    if max_tokens <= 0 or estimate_tokens(text) <= max_tokens:
        return text
    # 二分近似：按字符比例裁一次，再逐步收敛
    ratio = max_tokens / max(1, estimate_tokens(text))
    cut = max(1, int(len(text) * ratio))
    out = text[:cut]
    while estimate_tokens(out) > max_tokens and len(out) > 1:
        out = out[: int(len(out) * 0.9)]
    return out + " …（已截断）"


def build_context(
    spec: SubAgentSpec,
    *,
    pool: SharedMemoryPool | None = None,
    previous_summary: str = "",
    state_delta: dict[str, Any] | None = None,
    strategy: str | None = None,
) -> ComposedContext:
    """按策略组装子 Agent 的 prompt。

    Args:
        spec: 子 Agent 规格
        pool: 共享记忆池（shared_memory_pool 策略用；默认取全局单例）
        previous_summary: 上一轮摘要（incremental_summary 策略用）
        state_delta: 本轮增量状态（incremental_summary 策略用）
        strategy: 强制指定策略（默认自动选型）
    """
    policy = spec.context_policy
    payload = dict(spec.input_payload or {})
    # Phase 12 V2：自动派生时携带执行模板字段 → 子 Agent prompt 直接用模板渲染
    exec_template = payload.pop("execution_template_fields", None)
    raw_text = _stringify(payload)
    tokens_before = estimate_tokens(f"{spec.task_description}\n{raw_text}\n{previous_summary}")
    chosen = strategy or select_strategy(spec, raw_tokens=estimate_tokens(raw_text))

    raw_refs: list[ArtifactRef] = []
    shared_used: list[str] = []
    lines = _header(spec, chosen)
    lines.extend(_render_required(policy, payload))

    if chosen == "shared_memory_pool":
        active_pool = pool or get_default_pool()
        facts = active_pool.get_many(spec.parent_run_id, policy.shared_keys)
        lines.append("共享记忆池事实（只读自己声明的 key）:")
        if facts:
            for key, fact in facts.items():
                shared_used.append(key)
                value_text = _truncate_to_tokens(
                    _stringify(fact.value), max(50, policy.max_summary_tokens // 2)
                )
                lines.append(
                    f"  - {key} (v{fact.version} sha={fact.content_hash[:8]}): {value_text}"
                )
        else:
            lines.append("  （池中暂无声明的 fact）")
        lines.append("")
        lines.extend(
            _render_payload(payload, policy, inline_limit=MAX_INLINE_VALUE_CHARS, raw_refs=raw_refs)
        )

    elif chosen == "incremental_summary":
        if previous_summary:
            lines.append("上一轮摘要（增量基线）:")
            lines.append(_truncate_to_tokens(previous_summary, policy.max_summary_tokens))
            lines.append("")
        if state_delta:
            lines.append("增量状态 delta:")
            for k, v in state_delta.items():
                lines.append(f"  - {k}: {_truncate_to_tokens(_stringify(v), 120)}")
            lines.append("")
        # 长会话：非必读字段一律小 inline 限额，原文外置
        lines.extend(_render_payload(payload, policy, inline_limit=120, raw_refs=raw_refs))
        lines.append("请只输出结构化摘要（不要复述原文），并显式保留必读字段。")

    else:  # passthrough
        lines.extend(
            _render_payload(payload, policy, inline_limit=MAX_INLINE_VALUE_CHARS, raw_refs=raw_refs)
        )

    prompt = "\n".join(lines).rstrip() + "\n"
    if isinstance(exec_template, dict) and exec_template:
        prompt = _render_execution_template(exec_template)
    tokens_after = estimate_tokens(prompt)

    # 必读字段保留校验（CI 指标：保留率必须 100%）
    missing = [
        name
        for name in policy.required_fields
        if name in payload and _stringify(payload[name]) not in prompt
    ]
    if missing:
        logger.error("[context_strategy] 必读字段丢失 strategy=%s missing=%s", chosen, missing)

    if chosen == "passthrough" and tokens_after > PASSTHROUGH_SOFT_TOKEN_LIMIT * 4:
        logger.warning(
            "[context_strategy] passthrough prompt 偏大（%d tokens）—— 建议改用 incremental_summary",
            tokens_after,
        )

    return ComposedContext(
        prompt=prompt,
        strategy=chosen,
        tokens_before=tokens_before,
        tokens_after=tokens_after,
        required_fields_kept=not missing,
        missing_required_fields=missing,
        raw_refs=raw_refs,
        shared_facts_used=shared_used,
    )


__all__ = [
    "MAX_INLINE_VALUE_CHARS",
    "PASSTHROUGH_SOFT_TOKEN_LIMIT",
    "ComposedContext",
    "SharedFact",
    "SharedMemoryPool",
    "build_context",
    "estimate_tokens",
    "get_default_pool",
    "reset_default_pool",
    "select_strategy",
]
