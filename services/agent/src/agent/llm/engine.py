"""RouterEngine（engine.py）—— Phase 2C V2 编排器。

V0 编排顺序（**硬规则先于评分**）：
  1. hard_rules 过滤（CLAUDE.md §2 _LOCAL_ONLY_TASKS 永不可绕过）
  2. 五维评分排序候选
  3. budget 检查
  4. circuit_breaker 检查
  5. cache_l1 命中（V0 简化：占位 + 总是 miss）
  6. fallback chain 选定最终后端
  7. metrics 记录 + SSE emit

CLAUDE.md 红线：
- **LMRouter 4 个公开 API 完全冻结**（classify_intent / plan / repair_call / summarise）
- Engine 内部编排，**router.py 委托 engine**，不破坏 node 调用方

V0 简化：engine 暴露给 router 的接口是 `route_request(task_kind, sensitivity, request_id)`，
返回 `RoutingDecision`。Router 在 LMRouter 内部把 engine 的决策应用到链构造。

V2 增量：
- `set_weights()` 热生效评分权重（PUT /router/weights）
- `spark_route()` Spark 模式双跳（reasoning→execution）
- `route_request` 末尾调 `metrics.emit_event("llm_route_decided", ...)`
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from agent.llm.budget import BudgetController
from agent.llm.circuit_breaker import CircuitBreakerRegistry
from agent.llm.metrics import MetricsRecorder
from agent.llm.models import (
    LLMBackend,
    RoutingDecision,
    Sensitivity,
    TaskCategory,
)
from agent.llm.prompts import load_prompt, render_prompt
from agent.llm.rules import apply_hard_rules
from agent.llm.scoring import score_backend

logger = logging.getLogger(__name__)


class RouterEngine:
    """V0 路由器引擎：编排 hard_rules + scoring + budget + circuit_breaker。"""

    def __init__(
        self,
        backends: list[LLMBackend],
        budget: BudgetController | None = None,
        breakers: CircuitBreakerRegistry | None = None,
        metrics: MetricsRecorder | None = None,
        weights: dict | None = None,
        spark_enabled: bool = False,
        backend_callers: dict[str, Callable[[str, str], Awaitable[str]]] | None = None,
    ):
        self._backends = list(backends)
        self._budget = budget or BudgetController()
        self._breakers = breakers or CircuitBreakerRegistry()
        self._metrics = metrics or MetricsRecorder()
        self._weights = (
            dict(weights)
            if weights
            else {
                "capability": 0.35,
                "cost": 0.25,
                "latency": 0.20,
                "compliance": 0.15,
                "availability": 0.05,
            }
        )
        self._spark_enabled = spark_enabled
        # V2.5 增量：spark_route 用 backend_callers 真调 LLM（reasoning → execution）
        # backend_name → async (kind, user_prompt) → str
        # None 时 spark_route 退化为 V2.0 placeholder（向后兼容 V1.5）
        self._backend_callers: dict[str, Callable[[str, str], Awaitable[str]]] = (
            backend_callers or {}
        )
        # 确保每个 backend 都有熔断器
        for b in self._backends:
            self._breakers.get_or_create(b.name)

    @property
    def backends(self) -> list[LLMBackend]:
        return list(self._backends)

    @property
    def weights(self) -> dict:
        return dict(self._weights)

    @property
    def spark_enabled(self) -> bool:
        return self._spark_enabled

    def set_spark_enabled(self, enabled: bool) -> None:
        """运行时切换 Spark 模式（前端 toggle 直连）。"""
        self._spark_enabled = enabled
        logger.info("engine_spark_toggle enabled=%s", enabled)

    def set_weights(self, weights: dict) -> None:
        """V2 增量：热更新评分权重（PUT /router/weights 调用）。

        校验由 engine_api.py 的 WeightsBody 完成；这里仅同步内存 + 落库（调用方负责）。
        """
        # 数值合法性兜底（防御纵深）
        for k in ("capability", "cost", "latency", "compliance", "availability"):
            v = float(weights.get(k, 0))
            if not (0 <= v <= 1):
                raise ValueError(f"weight {k} out of [0, 1]: {v}")
        self._weights = {
            "capability": float(weights.get("capability", 0)),
            "cost": float(weights.get("cost", 0)),
            "latency": float(weights.get("latency", 0)),
            "compliance": float(weights.get("compliance", 0)),
            "availability": float(weights.get("availability", 0)),
        }
        logger.info("engine_weights_updated %s", self._weights)

    def _failure_count(self, name: str) -> int:
        """从熔断器读失败计数（V0 简化：state 反映）。"""
        cb = self._breakers.get_or_create(name)
        # 0 / 1 / 3 三档：closed 无失败，half_open 1 失败，open 3 失败
        if cb.state.value == "open":
            return 3
        if cb.state.value == "half_open":
            return 1
        return 0

    def route_request(
        self,
        task_kind: str,
        category: TaskCategory,
        sensitivity: Sensitivity,
        request_id: str | None = None,
        estimated_tokens: int = 1000,
        user_id: str = "anonymous",
        role_override: str | None = None,
    ) -> RoutingDecision:
        """编排一次路由决策。返回 RoutingDecision（含 fallback chain + Trace）。

        V2 新增 role_override：Spark 模式推理/执行两段时强制 role 锁。
        """
        req_id = request_id or str(uuid.uuid4())
        decision = RoutingDecision(
            request_id=req_id,
            user_id=user_id,
            task_category=category,
            sensitivity=sensitivity,
        )

        # 1. 硬规则过滤
        candidates = apply_hard_rules(self._backends, task_kind=task_kind, sensitivity=sensitivity)
        # V2 增量：role_override 进一步过滤（Spark 推理/执行需要特定 role）
        if role_override:
            candidates = [b for b in candidates if b.role == role_override]
        if not candidates:
            decision.primary_backend = None
            decision.actual_backend = None
            decision.candidates = []
            logger.warning(
                "engine_no_candidates_after_rules request_id=%s role_override=%s",
                req_id,
                role_override,
            )
            self._metrics.record(decision)
            self._metrics.emit_event("llm_route_decided", decision.trace_dict())
            return decision

        # 2. 五维评分排序
        scored = [
            (b, score_backend(b, category, failure_count=self._failure_count(b.name)))
            for b in candidates
        ]
        scored.sort(key=lambda x: x[1].total, reverse=True)
        decision.candidates = [(b.name, s) for b, s in scored]
        decision.primary_backend = scored[0][0].name
        decision.fallback_chain = [b.name for b, _ in scored]

        # 3. 预算 + 4. 熔断 联合检查
        actual_backend_name: str | None = None
        for b, _ in scored:
            # 熔断器放行
            cb = self._breakers.get_or_create(b.name)
            if not cb.allow():
                logger.info("engine_circuit_open_skip backend=%s", b.name)
                continue
            # 预算检查
            verdict = self._budget.check(b, estimated_tokens)
            if not verdict.allowed:
                logger.info(
                    "engine_budget_exceed_skip backend=%s reason=%s",
                    b.name,
                    verdict.reason,
                )
                continue
            actual_backend_name = b.name
            break

        decision.actual_backend = actual_backend_name
        decision.fallback_used = (
            (actual_backend_name != decision.primary_backend) if actual_backend_name else False
        )
        decision.estimated_cost = (
            self._budget.estimate(scored[0][0], estimated_tokens) if scored else 0.0
        )

        # 5. cache_l1 命中（V0 简化：占位，总是 miss）
        decision.cache_hit = False

        # 6. Phase 13 DSpark 决策注入（best-effort，DSpark runtime 可能未初始化）
        try:
            from agent.llm.dspark.api import decide_for_task

            pol, reason = decide_for_task(
                task_category=task_kind,
                max_tokens=estimated_tokens,
            )
            decision.speculative_enabled = pol.enabled
            decision.n_draft = pol.n_draft
            decision.draft_p_min = pol.draft_p_min
            # 草稿模型路径：从 dspark config 拿（policy.enabled 时才有）
            if pol.enabled:
                from agent.llm.dspark.api import get_runtime

                rt = get_runtime()
                decision.draft_model = rt.config.draft_model_path
            decision.dspark_reason = reason
            # 记录到 DSpark 引擎（供前端加速卡读取）
            from agent.llm.dspark.engine import engine as dspark_engine
            from agent.llm.dspark.engine import make_record

            dspark_engine.record(
                make_record(
                    task_category=task_kind,
                    decision=decision,
                    reason=reason,
                    max_tokens=estimated_tokens,
                )
            )
        except Exception as e:
            # DSpark 决策失败不能阻塞主路由（best-effort）
            logger.warning("engine_dspark_decide_failed request_id=%s err=%s", req_id, e)

        # 7. metrics 记录 + SSE emit
        self._metrics.record(decision)
        self._metrics.emit_event("llm_route_decided", decision.trace_dict())
        return decision

    async def spark_route(
        self,
        *,
        task_kind: str,
        user_prompt: str,
        history: list,
        tool_specs: list[dict],
        request_id: str,
        spark_timeout_sec: float = 60.0,
    ) -> RoutingDecision:
        """V2 增量：Spark 模式双跳（reasoning → execution）。

        链路（来自 phase-2c-smart-router.md §13.4.7）：
            1. reasoning 后端产出 draft（粗略计划）
            2. execution 后端拼 prompt 前缀（"[草稿]\\n\\n---\\n请继续完善"）+ 执行
            3. 返回 execution 决策（带 spark_draft + spark_execution_output）

        V2.5 增量（真拼 prompt）：
            - 若 backend_callers 注入（Phase 4 / llama.cpp 场景），真调 LLM：
              1. reasoning backend 拿 draft（粗略计划）
              2. execution backend 用 draft 拼 prompt 前缀执行
            - 未注入 caller：退化 V2.0 placeholder（向后兼容 V1.5）
            - history 和 tool_specs 会注入到 reasoning/execution prompt 的上下文中。
        """
        # 构建共享的上下文前缀（对话历史 + 可用工具）
        context_prefix = _build_spark_context(history, tool_specs)

        # 第 1 跳：reasoning
        reasoning_decision = self.route_request(
            task_kind=task_kind,
            category=TaskCategory.COMPLEX,
            sensitivity=Sensitivity.INTERNAL,
            request_id=request_id + "-draft",
            role_override="reasoning",
        )
        # 第 2 跳：execution
        execution_decision = self.route_request(
            task_kind=task_kind,
            category=TaskCategory.COMPLEX,
            sensitivity=Sensitivity.INTERNAL,
            request_id=request_id + "-exec",
            role_override="execution",
        )
        # 合并：execution 是 actual_backend，spark_draft/execution_output 由调用方填充
        execution_decision.spark_draft = (
            f"[reasoning draft from {reasoning_decision.actual_backend}] "
            f"(user_prompt len={len(user_prompt)})"
        )
        execution_decision.spark_execution_output = (
            f"[execution output placeholder from {execution_decision.actual_backend}]"
        )
        execution_decision.spark_reasoning_backend = reasoning_decision.actual_backend
        execution_decision.spark_execution_backend = execution_decision.actual_backend

        # V2.5 增量：真调 LLM（注入 caller 时）
        reasoning_backend = reasoning_decision.actual_backend
        execution_backend = execution_decision.actual_backend
        if reasoning_backend and execution_backend:
            reasoning_caller = self._backend_callers.get(reasoning_backend)
            execution_caller = self._backend_callers.get(execution_backend)
            if reasoning_caller and execution_caller:
                try:
                    # 第 1 跳：reasoning → 拿 draft（带超时）
                    draft_prompt = render_prompt(
                        load_prompt("spark_reasoning"),
                        CONTEXT_PREFIX=context_prefix,
                        USER_PROMPT=user_prompt,
                    )
                    draft = await asyncio.wait_for(
                        reasoning_caller(f"spark-reasoning-{task_kind}", draft_prompt),
                        timeout=spark_timeout_sec,
                    )
                    execution_decision.spark_draft = draft
                    # 第 2 跳：execution → draft 拼 prompt 前缀 + 实际推理（带超时）
                    execution_prompt = render_prompt(
                        load_prompt("spark_execution"),
                        DRAFT=draft,
                        CONTEXT_PREFIX=context_prefix,
                        USER_PROMPT=user_prompt,
                    )
                    execution_output = await asyncio.wait_for(
                        execution_caller(f"spark-execution-{task_kind}", execution_prompt),
                        timeout=spark_timeout_sec,
                    )
                    execution_decision.spark_execution_output = execution_output
                except asyncio.TimeoutError:
                    logger.warning(
                        "spark_route_timeout reason=%s exec=%s timeout=%.1fs",
                        reasoning_backend,
                        execution_backend,
                        spark_timeout_sec,
                    )
                except Exception as e:
                    # LLM 调用失败不阻塞（best-effort；回退到 placeholder）
                    # 若 reasoning 成功但 execution 失败，重置 draft 标记部分成功
                    if (
                        execution_decision.spark_draft
                        and not execution_decision.spark_draft.startswith("[reasoning")
                    ):
                        execution_decision.spark_draft = (
                            f"[reasoning draft from {reasoning_backend} (execution failed: {e})]"
                        )
                    logger.warning(
                        "spark_route_llm_call_failed reason=%s exec=%s err=%s",
                        reasoning_backend,
                        execution_backend,
                        e,
                    )

        # emit SSE 标记 Spark 模式双跳
        self._metrics.emit_event(
            "llm_route_decided",
            {
                **execution_decision.trace_dict(),
                "spark_mode": True,
                "spark_reasoning_backend": reasoning_decision.actual_backend,
                "spark_execution_backend": execution_decision.actual_backend,
            },
        )
        return execution_decision

    # ---- 状态查询接口（前端面板用） ----

    def circuit_states(self) -> dict[str, str]:
        """返回所有后端熔断器状态。"""
        return {name: cb.state.value for name, cb in self._breakers._registry.items()}

    def budget_status(self) -> dict:
        """返回预算状态。"""
        return {
            "daily_spent": self._budget.daily_spent,
            "daily_limit": self._budget.daily_limit,
        }


def _build_spark_context(history: list, tool_specs: list[dict]) -> str:
    """根据对话历史和工具定义构建 spark prompt 上下文前缀。

    参数：
        history: 对话历史 [{role, content}, ...]
        tool_specs: 工具定义列表 [{"function": {"name": ..., ...}}, ...]

    返回一个可能为空的字符串，可直接拼接到 prompt 前面。
    """
    parts: list[str] = []

    # 对话历史（最近 10 条，每条截断 500 字符）
    if history:
        lines: list[str] = ["对话历史："]
        for msg in history[-10:]:
            role = msg.get("role", "user") if isinstance(msg, dict) else "?"
            content = ""
            if isinstance(msg, dict):
                content = str(msg.get("content", ""))
                if isinstance(msg.get("content"), list):
                    # 多模态 content（图片 + 文字）
                    content = " ".join(
                        str(c.get("text", "")) if isinstance(c, dict) else str(c)
                        for c in msg["content"]
                    )
            lines.append(f"[{role}]: {content[:500]}")
        parts.append("\n".join(lines))

    # 可用工具（只列名字，最多 20 个）
    if tool_specs:
        tool_names: list[str] = []
        for t in tool_specs:
            if isinstance(t, dict):
                fn = t.get("function", {})
                name = fn.get("name", "") if isinstance(fn, dict) else ""
                if name:
                    tool_names.append(name)
        if tool_names:
            parts.append(f"可用工具：{', '.join(tool_names[:20])}")

    if not parts:
        return ""
    return "\n".join(parts) + "\n\n"
