"""Cross-language protocol types (Python side).

Pydantic models mirror the TypeScript declarations under `src/ts/`.
A test in `tests/test_roundtrip.py` round-trips each model to JSON
and asserts the keys match the TS counterpart.
"""
from protocol.events import AgentStreamEvent  # noqa: F401
from protocol.tools import ToolCall, ToolResult, ToolRiskLevel  # noqa: F401
from protocol.agent import ChatMessage, ChatRole, AgentRun, AgentRunStatus, TraceStep  # noqa: F401
from protocol.approval import ApprovalRequest, ApprovalDecision, PendingApproval  # noqa: F401
from protocol.audit import AuditEntry, AuditAction  # noqa: F401
from protocol.mcp import McpServerInfo, McpServerStatus, McpToolSpec  # noqa: F401
from protocol.dspark import (  # noqa: F401
    DSparkConfig,
    SpeculativePolicy,
    SpeculativeMode,
    DSparkDecisionRecord,
    DSparkDecisionReason,
    DSPARK_CONTEXT_SIZE_MIN,
    DSPARK_CONTEXT_SIZE_MAX,
    DSPARK_CONTEXT_SIZE_DEFAULT,
    DSPARK_GPU_LAYERS_MIN,
    DSPARK_GPU_LAYERS_MAX,
    DSPARK_GPU_LAYERS_DEFAULT,
    DSPARK_SHORT_OUTPUT_MIN,
    DSPARK_SHORT_OUTPUT_DEFAULT,
    DSPARK_N_DRAFT_MIN,
    DSPARK_N_DRAFT_MAX,
)