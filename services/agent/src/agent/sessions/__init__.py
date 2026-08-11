"""Phase 6 会话管理与恢复系统（V0 骨架 + V1 MACC 扩展 + V1.5 全功能）。

子模块：
- models: 数据类（Session / Message / SessionCheckpoint + V1.5 SessionEvent / ShareToken / BranchInfo）
- storage: SQLite CRUD（独立 sessions.db）+ V1 MACC 表 + V1.5 FTS5 + 分支 + 共享 + SessionEvent 哈希链
- checkpointer: LangGraph MemorySaver wrapper
- knowledge_base: 外部 KB 适配器接口（V0 mock + V1 接 Phase 4 / 第三方）
- api: FastAPI 路由（4 核心 + KB search + V1 MACC 端点 + V1.5 search/branch/share/export/recovery/stats）
- models_macc: V1 MACC 三层压缩数据类（SemanticRule / EventNode / EventEdge / CompressionContext / CompressionResult）
- event_graph: V1 L3 情景记忆（启发式抽取 + BFS 召回）
- semantic: V1 L3 语义记忆（蒸馏 + 召回）
- compression: V1 CompressionRouter + 三层策略分发
- sharing: V1.5 共享权限矩阵（owner/read/write + share_token）
- export: V1.5 加密 .eas 导出/导入（Fernet + Keyring + PII 脱敏）
- recovery: V1.5 启动恢复扫描（find_resumable_sessions）
"""

from __future__ import annotations

from agent.sessions.compression import CompressionRouter

# V1 MACC 模块
from agent.sessions.event_graph import (
    event_node_from_dict,
    extract_events_with_llm,
    heuristic_extract_from_messages,
    recall_episode,
    serialize_graph,
)
from agent.sessions.export import (
    SessionExporter,
    SessionImporter,
    export_session_to_eas,
    import_session_from_eas,
)

# V0 数据类
from agent.sessions.models import (
    BranchInfo,
    Message,
    MessageRole,
    Session,
    SessionCheckpoint,
    SessionEvent,
    SessionEventType,
    SessionStatus,
    SharePermission,
    ShareToken,
)

# V1 MACC 数据类
from agent.sessions.models_macc import (
    DEFAULT_ANCHORS,
    CompressionContext,
    CompressionResult,
    CompressionStrategy,
    EventEdge,
    EventNode,
    EventRelation,
    EventStatus,
    GistToken,
    SemanticRule,
    WorkingMemoryAnchor,
)
from agent.sessions.recovery import (
    RecoveryReport,
    scan_resumable_sessions,
)
from agent.sessions.semantic import (
    distill_rules_from_events,
    recall_relevant_rules,
)

# V1.5 模块
from agent.sessions.sharing import (
    SessionAccessDenied,
    ShareManager,
    check_session_access,
)

# V0 DAO
from agent.sessions.storage import (
    SESSIONS_DB,
    SessionStorage,
    now_ms,
)

__all__ = [
    "DEFAULT_ANCHORS",
    # V0 DAO
    "SESSIONS_DB",
    # V0 数据类
    "BranchInfo",
    # V1 数据类
    "CompressionContext",
    "CompressionResult",
    "CompressionRouter",
    "CompressionStrategy",
    "EventEdge",
    "EventNode",
    "EventRelation",
    "EventStatus",
    "GistToken",
    "Message",
    "MessageRole",
    "RecoveryReport",
    "SemanticRule",
    "Session",
    "SessionAccessDenied",
    "SessionCheckpoint",
    "SessionEvent",
    "SessionEventType",
    "SessionExporter",
    "SessionImporter",
    "SessionStatus",
    "SessionStorage",
    # V1.5 模块
    "ShareManager",
    "SharePermission",
    "ShareToken",
    "WorkingMemoryAnchor",
    "api",
    "check_session_access",
    "checkpointer",
    "distill_rules_from_events",
    # V1 模块
    "event_node_from_dict",
    "export_session_to_eas",
    "extract_events_with_llm",
    "heuristic_extract_from_messages",
    "import_session_from_eas",
    "knowledge_base",
    # V0
    "models",
    "now_ms",
    "recall_episode",
    "recall_relevant_rules",
    "scan_resumable_sessions",
    "serialize_graph",
    "storage",
]
