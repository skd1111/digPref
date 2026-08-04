"""Phase 12 多智能体调度（V0 + V1 + V1.5 完整实装）。

V0 范围（不引入 Redis / Worker Pool）：
  - Pydantic 契约：SubAgentSpec / SubAgentReport / ContextPolicy / ModelPolicy
  - 派生树硬上限：max_depth=2 + total_nodes≤30（铁律 2）
  - 同步 spawn：调用方传 SubAgentSpec → 异步任务跑 LLM → 返回 SubAgentReport
  - SSE 事件：sub_agent_spawn / sub_agent_done（前端订阅显示）
  - 进程内状态存储：_SUB_AGENTS dict（V0 简化，不做 Redis）

V1 增量（Phase 12 V1 扩展）：
  - Worker Pool（worker_pool.py）：并发限流 + 3 次重试 + DLQ + 幂等去重
  - 状态锁（locks.py）：乐观 CAS + 字典序分布式锁（进程内 mock；接口兼容 V1.5 真 Redis）
  - Token Bucket 三层限流（token_bucket.py）：租户 × 任务 × 后端
  - HITL 反向 interrupt（hitl_bridge.py）：子 Agent 写操作反向审批
  - SSE 3-way sync：sub_agent_progress（V1 新通道）

V1.5 增量（Phase 12 V1.5 收尾 / 实装 8 个新模块）：
  - events.py：进程内 SSE 事件 deque + emit/consume/flush（与 biznav/skill 模式对齐）
  - sensitive.py：敏感负载二次校验（PII / DB 凭证 / SQL 错误）→ 强制本地
  - context_strategy.py：三类上下文策略（passthrough / shared_memory_pool /
    incremental_summary）+ 必读字段不可压校验 + token 估算
  - schema.sql + state_repo.py：orchestrator.db 4 表（tasks / artifacts / dlq /
    metrics）+ 乐观锁 CAS + 幂等查重 + DLQ 持久化
  - queue.py：三级优先级 asyncio 队列（high / normal / low）+ 幂等去重 + 关闭传播
  - audit_bridge.py：审计 11 类事件 + correlation_id 回放整棵决策树
  - eval_collector.py：评测指标 + Judge 抽样（不作为 CI 闸门）
  - hitl_bridge.py V1.5 升级：`wait_for_user=True` 真 interrupt 复用主图 approval
  - orchestrator.py V1.5 重写：完整流水线（dispatch → enqueue → consume → execute
    → 3 次重试 → CAS 落库 → 推 SSE → Judge 抽样 → 写 metrics）

架构决策（2026-07-31）：
  - 本地 EAIDE 单进程 Tauri 桌面应用 → **不引入 Redis**；SQLite WAL 即权威层
  - 取消 ELK 全链路日志 → 自维护 logs/orchestrator-YYYYMMDD.jsonl（scrub 后写盘）
  - Judge 抽样默认 10%，**不作 CI 闸门**（设计文档 §3.3 明文）

文档：[docs/design/phase-12-multi-agent-scaling.md](../../../docs/design/phase-12-multi-agent-scaling.md)
"""
from agent.orchestrator.spec import (
    ArtifactRef,
    ContextPolicy,
    ModelPolicy,
    StateDelta,
    SubAgentReport,
    SubAgentSpec,
    SubAgentStatus,
)
from agent.orchestrator.tree_guard import (
    TreeLimitExceeded,
    enforce_tree_limits,
)

# V0
from agent.orchestrator.orchestrator import (
    Orchestrator,
    get_orchestrator,
    reset_orchestrator,
)

# V1 扩展
from agent.orchestrator.worker_pool import (
    DLQEntry,
    WorkerPool,
    WorkerResult,
    WorkerTask,
)
from agent.orchestrator.locks import (
    DistributedLockManager,
    VersionedState,
    cas_update,
    get_default_lock_manager,
    reset_default_lock_manager,
)
from agent.orchestrator.token_bucket import (
    BucketConfig,
    TokenBucket,
    TokenBucketManager,
    get_default_bucket_manager,
    reset_default_bucket_manager,
)
from agent.orchestrator.hitl_bridge import (
    HITLBridge,
    HITLDecision,
    HITLRequest,
    get_default_hitl_bridge,
    reset_default_hitl_bridge,
)

# V1.5 增量
from agent.orchestrator.events import (
    EVT_APPROVAL,
    EVT_SUB_AGENT_DONE,
    EVT_SUB_AGENT_PROGRESS,
    EVT_SUB_AGENT_SPAWN,
    consume_orchestrator_events,
    emit_orchestrator_event,
    flush_orchestrator_events,
)
from agent.orchestrator.sensitive import (
    SensitivityVerdict,
    classify_spec,
    prompt_safe_for_remote,
)
from agent.orchestrator.context_strategy import (
    ComposedContext,
    SharedFact,
    SharedMemoryPool,
    build_context,
    estimate_tokens,
    get_default_pool,
    reset_default_pool,
    select_strategy,
)
from agent.orchestrator.state_repo import (
    StateRepo,
    StateVersionConflict,
    get_default_repo,
    reset_default_repo,
)
from agent.orchestrator.queue import (
    PriorityTaskQueue,
    QueueClosed,
    QueueItem,
    get_default_queue,
    reset_default_queue,
)
from agent.orchestrator.audit_bridge import (
    ACTOR_SUB_AGENT,
    EVENT_CANCEL,
    EVENT_CLOSED,
    EVENT_DLQ,
    EVENT_DONE,
    EVENT_HITL_DECIDED,
    EVENT_HITL_REQUESTED,
    EVENT_JUDGE,
    EVENT_PROGRESS,
    EVENT_QUEUED,
    EVENT_REQUEUED,
    EVENT_RETRY,
    EVENT_SPAWN,
    build_tree,
    log_event,
    make_correlation_id,
    replay_summary,
    replay_tree,
)
from agent.orchestrator.eval_collector import (
    EvalCollector,
    JudgeVerdict,
    THRESHOLDS,
    get_default_collector,
    judge_report,
    reset_default_collector,
)
from agent.orchestrator.observability import (
    StructuredLogger,
    get_default_logger,
    reset_default_logger,
)

__all__ = [
    # V0
    "SubAgentSpec",
    "SubAgentReport",
    "ContextPolicy",
    "ModelPolicy",
    "SubAgentStatus",
    "ArtifactRef",
    "StateDelta",
    "enforce_tree_limits",
    "TreeLimitExceeded",
    "Orchestrator",
    "get_orchestrator",
    "reset_orchestrator",
    # V1 Worker Pool
    "WorkerPool",
    "WorkerTask",
    "WorkerResult",
    "DLQEntry",
    # V1 Locks
    "VersionedState",
    "cas_update",
    "DistributedLockManager",
    "get_default_lock_manager",
    "reset_default_lock_manager",
    # V1 Token Bucket
    "BucketConfig",
    "TokenBucket",
    "TokenBucketManager",
    "get_default_bucket_manager",
    "reset_default_bucket_manager",
    # V1 HITL Bridge
    "HITLRequest",
    "HITLDecision",
    "HITLBridge",
    "get_default_hitl_bridge",
    "reset_default_hitl_bridge",
    # V1.5 events
    "EVT_SUB_AGENT_SPAWN",
    "EVT_SUB_AGENT_PROGRESS",
    "EVT_SUB_AGENT_DONE",
    "EVT_APPROVAL",
    "emit_orchestrator_event",
    "consume_orchestrator_events",
    "flush_orchestrator_events",
    # V1.5 sensitive
    "SensitivityVerdict",
    "classify_spec",
    "prompt_safe_for_remote",
    # V1.5 context strategy
    "ComposedContext",
    "SharedFact",
    "SharedMemoryPool",
    "build_context",
    "select_strategy",
    "estimate_tokens",
    "get_default_pool",
    "reset_default_pool",
    # V1.5 state repo
    "StateRepo",
    "StateVersionConflict",
    "get_default_repo",
    "reset_default_repo",
    # V1.5 queue
    "PriorityTaskQueue",
    "QueueItem",
    "QueueClosed",
    "get_default_queue",
    "reset_default_queue",
    # V1.5 audit bridge
    "ACTOR_SUB_AGENT",
    "EVENT_SPAWN",
    "EVENT_PROGRESS",
    "EVENT_DONE",
    "EVENT_DLQ",
    "EVENT_CANCEL",
    "EVENT_CLOSED",
    "EVENT_QUEUED",
    "EVENT_REQUEUED",
    "EVENT_RETRY",
    "EVENT_HITL_REQUESTED",
    "EVENT_HITL_DECIDED",
    "EVENT_JUDGE",
    "log_event",
    "make_correlation_id",
    "replay_tree",
    "replay_summary",
    "build_tree",
    # V1.5 eval
    "EvalCollector",
    "JudgeVerdict",
    "THRESHOLDS",
    "judge_report",
    "get_default_collector",
    "reset_default_collector",
    # V1.5 observability
    "StructuredLogger",
    "get_default_logger",
    "reset_default_logger",
]
