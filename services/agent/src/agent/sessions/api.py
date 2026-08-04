"""sessions.api —— Phase 6 V0 + V1 + V1.5 FastAPI 路由。

V0 范围（4 核心路由）：
  POST   /sessions              创建会话
  GET    /sessions              列出 active 会话（按 updated_at DESC）
  GET    /sessions/{id}         详情（含消息 + checkpoint 引用）
  DELETE /sessions/{id}         删除会话（CASCADE 删消息 + checkpoint）
  POST   /sessions/kb/search    外部 KB 检索（V0 mock + V1 接 Phase 4）

V1 MACC 端点（设计 §1）：
  POST /sessions/extract-events      启发式事件抽取
  POST /sessions/distill-rules       语义规则蒸馏
  POST /sessions/recall-episode      BFS 召回
  POST /sessions/compress            CompressionRouter + 拼装 prompt

V1.5 新端点（CLAUDE.md §6 §1 HITL + §5 Keyring）：
  GET    /sessions/{id}/stats             消息数 / checkpoint / 事件链 / 压缩 / 分支
  POST   /sessions/{id}/messages          追加消息（前端 SSE 流调用）
  POST   /sessions/{id}/checkpoints       手动打 checkpoint 引用
  GET    /sessions/search                FTS5 全文搜索（跨会话）
  POST   /sessions/{id}/branch           创建分支会话
  GET    /sessions/{id}/branches         列出该会话的所有分支
  POST   /sessions/{id}/share            创建 share_token（owner only）
  DELETE /sessions/{id}/share/{token}    撤销 share_token（owner only）
  POST   /sessions/{id}/share/grant      授予 actor 权限（owner only）
  POST   /sessions/{id}/export           加密 .eas 导出
  POST   /sessions/import                .eas 导入（新会话）
  GET    /sessions/recovery              启动扫描恢复
  GET    /sessions/{id}/event-chain      列出 SessionEvent
  POST   /sessions/{id}/event-chain/verify  校验哈希链
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel, Field

from .checkpointer import SessionCheckpointer
from .knowledge_base import KBConfig, build_kb_context
from .models import MessageRole, Session
from .sharing import ShareManager, SessionAccessDenied
from .storage import SessionStorage

logger = logging.getLogger(__name__)


def _default_db_path() -> str:
    appdata = os.environ.get("APPDATA", str(os.path.expanduser("~")))
    return os.path.join(appdata, "eaide", "sessions.db")


_storage: SessionStorage | None = None
_checkpointer: SessionCheckpointer | None = None


def get_storage() -> SessionStorage:
    """模块级 lazy 单例（与 envconfig.api 同模式）。"""
    global _storage
    if _storage is None:
        _storage = SessionStorage(_default_db_path())
    return _storage


def get_checkpointer() -> SessionCheckpointer:
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = SessionCheckpointer(get_storage())
    return _checkpointer


# ---- Pydantic 模型 --------------------------------------------------------

class CreateSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    owner: str = Field(default="default", max_length=64)
    project_name: str = Field(default="default", max_length=64)
    metadata: dict = Field(default_factory=dict)


class SessionSummary(BaseModel):
    id: str
    title: str
    owner: str
    project_name: str
    status: str
    created_at: int
    updated_at: int
    thread_id: str
    metadata: dict = Field(default_factory=dict)


class SessionDetail(SessionSummary):
    messages: list[dict] = Field(default_factory=list)
    checkpoints: list[dict] = Field(default_factory=list)


class KBSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    top_k: int = Field(default=3, ge=1, le=10)


class KBSearchResponse(BaseModel):
    backend: str
    elapsed_ms: int
    results: list[dict] = Field(default_factory=list)
    snippet: str = ""  # 已 PII 脱敏的 prompt 片段（V1 接 Phase 4 时会真脱敏）


# ---- V1.5 Pydantic 模型（前置：路由 handler 引用）---------------------------
# 注意：StatsResponse / SearchRequest / RecoveryResponse 等必须先定义，
# 否则 /recovery /search /import 路由（早于 /{session_id} 通配符）会 NameError。


class StatsResponse(BaseModel):
    session_id: str
    title: str
    owner: str
    status: str
    is_branch: bool
    parent_session_id: str | None
    branch_label: str
    message_count: int
    checkpoint_count: int
    event_chain_count: int
    compression_count: int
    branch_count: int
    created_at: int
    updated_at: int


class AppendMessageRequest(BaseModel):
    role: MessageRole = "user"
    content: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    metadata: dict = Field(default_factory=dict)
    actor: str = "default"


class AppendMessageResponse(BaseModel):
    message_id: int
    session_id: str
    created_at: int


class RecordCheckpointRequest(BaseModel):
    thread_id: str
    checkpoint_id: str
    label: str = ""
    description: str = ""
    metadata: dict = Field(default_factory=dict)


class RecordCheckpointResponse(BaseModel):
    checkpoint_id: int
    session_id: str
    created_at: int


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    project_name: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class SearchHit(BaseModel):
    session_id: str
    created_at: int
    title: str
    content_snippet: str
    tool_name: str
    tool_result: str
    relevance: float


class SearchResponse(BaseModel):
    query: str
    total: int
    hits: list[SearchHit]


class BranchCreateRequest(BaseModel):
    branch_label: str = Field(..., min_length=1, max_length=200)
    from_checkpoint_id: str | None = None
    title_suffix: str = " (分支)"
    actor: str = "default"


class BranchInfoResponse(BaseModel):
    id: str
    title: str
    parent_session_id: str | None
    branch_from_checkpoint_id: str | None
    branch_label: str
    created_at: int
    updated_at: int
    status: str


class BranchListResponse(BaseModel):
    parent_session_id: str
    branches: list[BranchInfoResponse]
    total: int


class ShareCreateRequest(BaseModel):
    permission: str = Field(default="read", pattern=r"^(read|write)$")
    expires_in_ms: int | None = None
    actor: str = "default"


class ShareTokenResponse(BaseModel):
    token: str
    permission: str
    created_at: int
    expires_at: int | None = None


class ShareRevokeRequest(BaseModel):
    actor: str = "default"


class ShareGrantRequest(BaseModel):
    target_actor: str = Field(..., min_length=1, max_length=64)
    permission: str = Field(..., pattern=r"^(read|write)$")
    granter: str = "default"


class ShareListResponse(BaseModel):
    session_id: str
    share_tokens: list[dict]
    permissions: dict[str, str]


class ExportRequest(BaseModel):
    output_path: str = Field(..., min_length=1, max_length=512)
    actor: str = "default"
    include_messages: bool = True
    include_event_chain: bool = True
    scrub_pii: bool = True


class ExportResponse(BaseModel):
    path: str
    bytes: int
    checksum: str
    exported_at: int


class ImportRequest(BaseModel):
    eas_path: str = Field(..., min_length=1, max_length=512)
    actor: str = "default"
    import_as_branch: bool = False
    parent_session_id: str | None = None


class ImportResponse(BaseModel):
    new_session_id: str
    message_count: int
    checkpoint_count: int
    event_count: int
    checksum: str
    chain_check: dict


class RecoveryResponse(BaseModel):
    total: int
    resumable_ids: list[str]
    oldest_idle_ms: int
    generated_at: int
    threshold_ms: int
    needs_recovery: bool


class EventChainEntry(BaseModel):
    id: int
    session_id: str
    event_type: str
    payload: dict
    prev_hash: str
    hash: str
    actor: str
    created_at: int


class EventChainResponse(BaseModel):
    session_id: str
    total: int
    entries: list[EventChainEntry]


class EventChainVerifyResponse(BaseModel):
    session_id: str
    valid: bool
    total: int
    broken_at_id: int | None
    broken_reason: str | None


def _session_to_summary(s: Session) -> SessionSummary:
    return SessionSummary(
        id=s.id, title=s.title, owner=s.owner, project_name=s.project_name,
        status=s.status, created_at=s.created_at, updated_at=s.updated_at,
        thread_id=s.thread_id, metadata=s.metadata,
    )


# ---- Router ---------------------------------------------------------------

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionSummary, status_code=201)
def create_session(body: CreateSessionRequest) -> SessionSummary:
    """创建会话（V0 同步路由；V1 异步 if 接入更多初始化）。"""
    storage = get_storage()
    s = storage.create_session(
        title=body.title,
        owner=body.owner,
        project_name=body.project_name,
        metadata=body.metadata,
    )
    return _session_to_summary(s)


@router.get("", response_model=list[SessionSummary])
def list_sessions(
    status: Optional[str] = Query(None),
    project_name: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> list[SessionSummary]:
    """列出会话（默认不按 status 过滤，按 updated_at DESC）。

    传 status=active 时仅列出活跃会话；status=null 列出全部（含 archived/deleted）。
    """
    storage = get_storage()
    # 校验 status 值（仅允许 None 或合法 SessionStatus）
    valid_status = {"active", "archived", "deleted"}
    if status is not None and status not in valid_status:
        raise HTTPException(400, f"invalid status: {status}; allowed: {valid_status}")
    sessions = storage.list_sessions(
        status=status,  # type: ignore[arg-type]
        project_name=project_name,
        limit=limit,
    )
    return [_session_to_summary(s) for s in sessions]


# ==== V1.5 优先注册：/recovery + /import + /search 必须在 /{session_id} 之前 ====
# FastAPI/Starlette 按注册顺序匹配路由；/recovery /search /import 是字面量路径，
# 必须早于 /{session_id} 通配符，否则 /sessions/recovery 会被当 session_id="recovery" 解析 → 404。

@router.get("/recovery", response_model=RecoveryResponse)
def recovery_endpoint(
    idle_threshold_ms: int = Query(300_000, ge=10_000, le=86_400_000),
    limit: int = Query(50, ge=1, le=200),
) -> RecoveryResponse:
    """启动恢复扫描：列出 updated_at 距今 > 阈值的活跃会话。"""
    from .recovery import scan_resumable_sessions
    storage = get_storage()
    report = scan_resumable_sessions(
        storage, idle_threshold_ms=idle_threshold_ms, limit=limit,
    )
    return RecoveryResponse(**report.to_dict())


@router.post("/import", response_model=ImportResponse, status_code=201)
def import_endpoint(body: ImportRequest) -> ImportResponse:
    """从 .eas 导入会话（新建 session；可选 import_as_branch）。"""
    from .export import SessionImporter
    storage = get_storage()
    try:
        result = SessionImporter(storage).import_from_file(
            eas_path=body.eas_path,
            actor=body.actor,
            import_as_branch=body.import_as_branch,
            parent_session_id=body.parent_session_id,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return ImportResponse(**result)


@router.post("/search", response_model=SearchResponse)
def search_endpoint(body: SearchRequest) -> SearchResponse:
    """FTS5 全文搜索：跨会话（标题 + 消息 + 工具名 + 工具结果）。"""
    storage = get_storage()
    rows = storage.fts_search(
        body.query, project_name=body.project_name, limit=body.limit,
    )
    return SearchResponse(
        query=body.query,
        total=len(rows),
        hits=[SearchHit(**r) for r in rows],
    )


@router.get("/{session_id}", response_model=SessionDetail)
def get_session(session_id: str) -> SessionDetail:
    """详情：含消息列表 + checkpoint 引用列表。"""
    storage = get_storage()
    s = storage.get_session(session_id)
    if not s:
        raise HTTPException(404, f"session {session_id} not found")
    messages = storage.list_messages(session_id, limit=500)
    cps = storage.list_checkpoints(session_id)
    return SessionDetail(
        **_session_to_summary(s).model_dump(),
        messages=[m.__dict__ for m in messages],
        checkpoints=[c.__dict__ for c in cps],
    )


@router.delete("/{session_id}", status_code=204)
def delete_session(session_id: str) -> None:
    """硬删除会话（CASCADE 删 messages + checkpoints）。"""
    storage = get_storage()
    ok = storage.delete_session(session_id)
    if not ok:
        raise HTTPException(404, f"session {session_id} not found")


@router.post("/kb/search", response_model=KBSearchResponse)
async def kb_search(body: KBSearchRequest) -> KBSearchResponse:
    """外部知识库检索（V0 用 MockKBAdapter；V1 接 Phase 4 / Notion / Confluence）。

    **CLAUDE.md §2 红线**：
      - 不读敏感上下文裸文（KB 查询结果已经过 V0 Mock 内部脱敏示例；
        V1 接 Phase 4 时会走 redact.py）
      - 超时严格（V0 默认 5s，V1 接外部 KB 按 KBConfig.timeout_s 调）
      - 失败 → 空 context，不阻塞 agent 决策
    """
    ctx = await build_kb_context(
        body.query, top_k=body.top_k, adapter=None,
    )
    from .knowledge_base import kb_context_to_prompt_snippet
    snippet = kb_context_to_prompt_snippet(ctx)
    return KBSearchResponse(
        backend=ctx.backend,
        elapsed_ms=ctx.elapsed_ms,
        results=[r.__dict__ for r in ctx.results],
        snippet=snippet,
    )


# =============================================================================
# Phase 6 V1 MACC 端点（事件图谱 + 语义规则 + 压缩路由）
# =============================================================================


class ExtractEventsRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    messages: list[dict] = Field(default_factory=list)
    use_llm: bool = False  # V1 仅启发式；use_llm=True 时需传 llm（V1.5）


class ExtractEventsResponse(BaseModel):
    session_id: str
    extracted_count: int
    node_ids: list[str]


class DistillRulesRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    min_occurrences: int = Field(default=3, ge=2, le=100)
    max_rules: int = Field(default=20, ge=1, le=100)


class DistillRulesResponse(BaseModel):
    session_id: str
    distilled_count: int
    rules: list[dict]


class RecallEpisodeRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    query: str = Field(..., min_length=1, max_length=2000)
    entity_keywords: list[str] | None = None
    max_hops: int = Field(default=2, ge=1, le=5)
    max_nodes: int = Field(default=10, ge=1, le=100)


class RecallEpisodeResponse(BaseModel):
    session_id: str
    nodes: list[dict]
    edges: list[dict]
    node_count: int
    edge_count: int


class CompressRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    token_count: int = Field(default=0, ge=0, le=10_000_000)
    message_count: int = Field(default=0, ge=0, le=100_000)
    task_complexity: str = Field(default="medium")
    memory_entropy: float = Field(default=0.5, ge=0.0, le=1.0)
    has_multimodal: bool = False
    has_code: bool = False
    has_logs: bool = False
    has_db_rows: bool = False
    idle_time_s: int = Field(default=0, ge=0, le=86400)
    messages: list[dict] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


class CompressResponse(BaseModel):
    strategy: str
    before_tokens: int
    after_tokens: int
    compression_ratio: float
    layers_used: list[str]
    formatted_prompt: str
    elapsed_ms: int
    backend: str = "router"


@router.post("/extract-events", response_model=ExtractEventsResponse)
async def extract_events(body: ExtractEventsRequest):
    """从消息轨迹启发式抽取事件节点（V1 无 LLM；use_llm=True 时占位）。"""
    from .event_graph import extract_events_with_llm
    storage = get_storage()
    nodes = await extract_events_with_llm(
        body.session_id, body.messages,
        storage=storage, llm=None,
    )
    return ExtractEventsResponse(
        session_id=body.session_id,
        extracted_count=len(nodes),
        node_ids=[n.id for n in nodes],
    )


@router.post("/distill-rules", response_model=DistillRulesResponse)
async def distill_rules(body: DistillRulesRequest):
    """从 session 的事件图谱蒸馏语义规则。"""
    from .semantic import distill_rules_from_events
    storage = get_storage()
    rules = distill_rules_from_events(
        body.session_id,
        storage=storage,
        min_occurrences=body.min_occurrences,
        max_rules=body.max_rules,
    )
    return DistillRulesResponse(
        session_id=body.session_id,
        distilled_count=len(rules),
        rules=[r.to_dict() for r in rules],
    )


@router.post("/recall-episode", response_model=RecallEpisodeResponse)
async def recall_episode_endpoint(body: RecallEpisodeRequest):
    """从事件图谱 BFS 召回相关历史事件。"""
    from .event_graph import recall_episode
    storage = get_storage()
    nodes = recall_episode(
        storage, body.session_id,
        query=body.query,
        entity_keywords=body.entity_keywords,
        max_hops=body.max_hops,
        max_nodes=body.max_nodes,
    )
    edges = storage.list_event_edges(body.session_id)
    return RecallEpisodeResponse(
        session_id=body.session_id,
        nodes=nodes,
        edges=edges,
        node_count=len(nodes),
        edge_count=len(edges),
    )


@router.post("/compress", response_model=CompressResponse)
async def compress_endpoint(body: CompressRequest):
    """用 CompressionRouter 选策略 + 应用压缩 + 写 compression_log。"""
    from .compression import CompressionRouter
    from .models_macc import CompressionContext
    storage = get_storage()
    router = CompressionRouter(storage)
    ctx = CompressionContext(
        session_id=body.session_id,
        token_count=body.token_count,
        message_count=body.message_count,
        task_complexity=body.task_complexity,  # type: ignore[arg-type]
        memory_entropy=body.memory_entropy,
        has_multimodal=body.has_multimodal,
        has_code=body.has_code,
        has_logs=body.has_logs,
        has_db_rows=body.has_db_rows,
        idle_time_s=body.idle_time_s,
        extra=body.extra,
    )
    result = router.route(ctx, messages=body.messages)
    return CompressResponse(
        strategy=result.strategy,
        before_tokens=result.before_tokens,
        after_tokens=result.after_tokens,
        compression_ratio=result.compression_ratio,
        layers_used=list(result.layers_used),
        formatted_prompt=result.formatted_prompt,
        elapsed_ms=result.elapsed_ms,
        backend=result.backend,
    )


# =============================================================================
# Phase 6 V1.5 端点：stats / messages / checkpoints / search / branch /
#                   share / export / import / recovery / event-chain
# =============================================================================


class StatsResponse(BaseModel):
    session_id: str
    title: str
    owner: str
    status: str
    is_branch: bool
    parent_session_id: str | None
    branch_label: str
    message_count: int
    checkpoint_count: int
    event_chain_count: int
    compression_count: int
    branch_count: int
    created_at: int
    updated_at: int


class AppendMessageRequest(BaseModel):
    role: MessageRole = "user"
    content: str = ""
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_args: dict | None = None
    tool_result: str | None = None
    metadata: dict = Field(default_factory=dict)
    actor: str = "default"


class AppendMessageResponse(BaseModel):
    message_id: int
    session_id: str
    created_at: int


class RecordCheckpointRequest(BaseModel):
    thread_id: str
    checkpoint_id: str
    label: str = ""
    description: str = ""
    metadata: dict = Field(default_factory=dict)


class RecordCheckpointResponse(BaseModel):
    checkpoint_id: int
    session_id: str
    created_at: int


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    project_name: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class SearchHit(BaseModel):
    session_id: str
    created_at: int
    title: str
    content_snippet: str
    tool_name: str
    tool_result: str
    relevance: float


class SearchResponse(BaseModel):
    query: str
    total: int
    hits: list[SearchHit]


class BranchCreateRequest(BaseModel):
    branch_label: str = Field(..., min_length=1, max_length=200)
    from_checkpoint_id: str | None = None
    title_suffix: str = " (分支)"
    actor: str = "default"


class BranchInfoResponse(BaseModel):
    id: str
    title: str
    parent_session_id: str | None
    branch_from_checkpoint_id: str | None
    branch_label: str
    created_at: int
    updated_at: int
    status: str


class BranchListResponse(BaseModel):
    parent_session_id: str
    branches: list[BranchInfoResponse]
    total: int


class ShareCreateRequest(BaseModel):
    permission: str = Field(default="read", pattern=r"^(read|write)$")
    expires_in_ms: int | None = None
    actor: str = "default"


class ShareTokenResponse(BaseModel):
    token: str
    permission: str
    created_at: int
    expires_at: int | None = None


class ShareRevokeRequest(BaseModel):
    actor: str = "default"


class ShareGrantRequest(BaseModel):
    target_actor: str = Field(..., min_length=1, max_length=64)
    permission: str = Field(..., pattern=r"^(read|write)$")
    granter: str = "default"


class ShareListResponse(BaseModel):
    session_id: str
    share_tokens: list[dict]
    permissions: dict[str, str]


class ExportRequest(BaseModel):
    output_path: str = Field(..., min_length=1, max_length=512)
    actor: str = "default"
    include_messages: bool = True
    include_event_chain: bool = True
    scrub_pii: bool = True


class ExportResponse(BaseModel):
    path: str
    bytes: int
    checksum: str
    exported_at: int


class ImportRequest(BaseModel):
    eas_path: str = Field(..., min_length=1, max_length=512)
    actor: str = "default"
    import_as_branch: bool = False
    parent_session_id: str | None = None


class ImportResponse(BaseModel):
    new_session_id: str
    message_count: int
    checkpoint_count: int
    event_count: int
    checksum: str
    chain_check: dict


class RecoveryResponse(BaseModel):
    total: int
    resumable_ids: list[str]
    oldest_idle_ms: int
    generated_at: int
    threshold_ms: int
    needs_recovery: bool


class EventChainEntry(BaseModel):
    id: int
    session_id: str
    event_type: str
    payload: dict
    prev_hash: str
    hash: str
    actor: str
    created_at: int


class EventChainResponse(BaseModel):
    session_id: str
    total: int
    entries: list[EventChainEntry]


class EventChainVerifyResponse(BaseModel):
    session_id: str
    valid: bool
    total: int
    broken_at_id: int | None
    broken_reason: str | None


# ---- Stats / Append / Checkpoint / Search ---------------------------------

@router.get("/{session_id}/stats", response_model=StatsResponse)
def session_stats(session_id: str) -> StatsResponse:
    storage = get_storage()
    try:
        stats = storage.get_session_stats(session_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return StatsResponse(**stats)


@router.post("/{session_id}/messages", response_model=AppendMessageResponse, status_code=201)
def append_message_endpoint(session_id: str, body: AppendMessageRequest) -> AppendMessageResponse:
    """追加消息到会话（前端 SSE 流 / 工具结果回调用）。"""
    storage = get_storage()
    if storage.get_session(session_id) is None:
        raise HTTPException(404, f"session {session_id} not found")
    msg = storage.append_message(
        session_id=session_id,
        role=body.role,
        content=body.content,
        tool_call_id=body.tool_call_id,
        tool_name=body.tool_name,
        tool_args=body.tool_args,
        tool_result=body.tool_result,
        metadata=body.metadata,
    )
    return AppendMessageResponse(
        message_id=msg.id, session_id=session_id, created_at=msg.created_at,
    )


@router.post(
    "/{session_id}/checkpoints", response_model=RecordCheckpointResponse, status_code=201,
)
def record_checkpoint_endpoint(
    session_id: str, body: RecordCheckpointRequest,
) -> RecordCheckpointResponse:
    """手动记录 checkpoint 引用（实际状态存 LangGraph 自己的 SQLite 表）。"""
    storage = get_storage()
    if storage.get_session(session_id) is None:
        raise HTTPException(404, f"session {session_id} not found")
    cp = storage.record_checkpoint(
        session_id=session_id,
        thread_id=body.thread_id,
        checkpoint_id=body.checkpoint_id,
        label=body.label,
        description=body.description,
        metadata=body.metadata,
    )
    return RecordCheckpointResponse(
        checkpoint_id=cp.id, session_id=session_id, created_at=cp.created_at,
    )


# ---- Branch ---------------------------------------------------------------

@router.post("/{session_id}/branch", response_model=BranchInfoResponse, status_code=201)
def branch_endpoint(session_id: str, body: BranchCreateRequest) -> BranchInfoResponse:
    """从父会话创建分支会话。"""
    storage = get_storage()
    try:
        sess = storage.create_branch(
            parent_session_id=session_id,
            branch_label=body.branch_label,
            from_checkpoint_id=body.from_checkpoint_id,
            title_suffix=body.title_suffix,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    return BranchInfoResponse(
        id=sess.id,
        title=sess.title,
        parent_session_id=sess.parent_session_id,
        branch_from_checkpoint_id=sess.branch_from_checkpoint_id,
        branch_label=sess.branch_label,
        created_at=sess.created_at,
        updated_at=sess.updated_at,
        status=sess.status,
    )


@router.get("/{session_id}/branches", response_model=BranchListResponse)
def list_branches_endpoint(session_id: str) -> BranchListResponse:
    storage = get_storage()
    branches = storage.list_branches(session_id)
    return BranchListResponse(
        parent_session_id=session_id,
        total=len(branches),
        branches=[
            BranchInfoResponse(
                id=b.id,
                title=b.title,
                parent_session_id=b.parent_session_id,
                branch_from_checkpoint_id=b.branch_from_checkpoint_id,
                branch_label=b.branch_label,
                created_at=b.created_at,
                updated_at=b.updated_at,
                status=b.status,
            )
            for b in branches
        ],
    )


# ---- Share -----------------------------------------------------------------

@router.post("/{session_id}/share", response_model=ShareTokenResponse, status_code=201)
def share_create_endpoint(
    session_id: str, body: ShareCreateRequest,
) -> ShareTokenResponse:
    """创建分享令牌（owner only；非 owner 返 403）。"""
    storage = get_storage()
    mgr = ShareManager(storage)
    try:
        token = mgr.create_share_token(
            session_id=session_id,
            permission=body.permission,  # type: ignore[arg-type]
            expires_in_ms=body.expires_in_ms,
            actor=body.actor,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except SessionAccessDenied as e:
        raise HTTPException(403, str(e)) from e
    return ShareTokenResponse(
        token=token.token,
        permission=token.permission,
        created_at=token.created_at,
        expires_at=token.expires_at,
    )


@router.delete("/{session_id}/share/{token}", status_code=204)
def share_revoke_endpoint(
    session_id: str, token: str, actor: str = Query("default"),
) -> None:
    """撤销分享令牌。"""
    storage = get_storage()
    mgr = ShareManager(storage)
    try:
        ok = mgr.revoke_share_token(session_id, token, actor=actor)
    except SessionAccessDenied as e:
        raise HTTPException(403, str(e)) from e
    if not ok:
        raise HTTPException(404, "share token not found")


@router.post("/{session_id}/share/grant", status_code=200)
def share_grant_endpoint(
    session_id: str, body: ShareGrantRequest,
) -> dict:
    """授予 actor 权限（owner only）。"""
    storage = get_storage()
    mgr = ShareManager(storage)
    try:
        ok = mgr.grant(
            session_id=session_id,
            target_actor=body.target_actor,
            permission=body.permission,  # type: ignore[arg-type]
            granter=body.granter,
        )
    except SessionAccessDenied as e:
        raise HTTPException(403, str(e)) from e
    if not ok:
        raise HTTPException(404, "session not found or granter not owner")
    return {"ok": True, "session_id": session_id, "granted_to": body.target_actor}


@router.get("/{session_id}/share", response_model=ShareListResponse)
def share_list_endpoint(
    session_id: str, actor: str = Query("default"),
) -> ShareListResponse:
    """列出会话的 share_token + permissions（owner only）。"""
    storage = get_storage()
    mgr = ShareManager(storage)
    sess = storage.get_session(session_id)
    if sess is None:
        raise HTTPException(404, f"session {session_id} not found")
    if sess.owner != actor:
        raise HTTPException(403, "only owner can list share info")
    return ShareListResponse(
        session_id=session_id,
        share_tokens=mgr.list_share_tokens(session_id, actor=actor),
        permissions=mgr.list_permissions(session_id, actor=actor),
    )


# ---- Export / Import -------------------------------------------------------

@router.post("/{session_id}/export", response_model=ExportResponse)
def export_endpoint(session_id: str, body: ExportRequest) -> ExportResponse:
    """加密 .eas 导出（Fernet + Keyring；owner only）。"""
    from .export import SessionExporter
    storage = get_storage()
    try:
        result = SessionExporter(storage).export_to_file(
            session_id=session_id,
            output_path=body.output_path,
            actor=body.actor,
            include_messages=body.include_messages,
            include_event_chain=body.include_event_chain,
            scrub_pii=body.scrub_pii,
        )
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    return ExportResponse(**result)


# ---- Recovery / Event Chain -------------------------------------------------

@router.get("/{session_id}/event-chain", response_model=EventChainResponse)
def event_chain_endpoint(
    session_id: str, limit: int = Query(200, ge=1, le=2000),
) -> EventChainResponse:
    storage = get_storage()
    if storage.get_session(session_id) is None:
        raise HTTPException(404, f"session {session_id} not found")
    events = storage.list_event_chain(session_id, limit=limit)
    return EventChainResponse(
        session_id=session_id,
        total=len(events),
        entries=[
            EventChainEntry(
                id=e.id,
                session_id=e.session_id,
                event_type=e.event_type,
                payload=e.payload,
                prev_hash=e.prev_hash,
                hash=e.hash,
                actor=e.actor,
                created_at=e.created_at,
            )
            for e in events
        ],
    )


@router.post(
    "/{session_id}/event-chain/verify", response_model=EventChainVerifyResponse,
)
def event_chain_verify_endpoint(session_id: str) -> EventChainVerifyResponse:
    """校验会话 SessionEvent 哈希链完整性。"""
    storage = get_storage()
    if storage.get_session(session_id) is None:
        raise HTTPException(404, f"session {session_id} not found")
    result = storage.verify_event_chain(session_id)
    return EventChainVerifyResponse(session_id=session_id, **result)