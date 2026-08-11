"""Cross-language protocol types (Python side).

Pydantic models mirror the TypeScript declarations under `src/ts/`.
A test in `tests/test_roundtrip.py` round-trips each model to JSON
and asserts the keys match the TS counterpart.
"""

from protocol.agent import AgentRun, AgentRunStatus, ChatMessage, ChatRole, TraceStep  # noqa: F401
from protocol.approval import ApprovalDecision, ApprovalRequest, PendingApproval  # noqa: F401
from protocol.audit import AuditAction, AuditEntry  # noqa: F401
from protocol.doc_review import (  # noqa: F401
    DocBlock,
    DocCategory,
    DocDetail,
    DocFinding,
    DocFormat,
    DocKbRef,
    DocPage,
    DocPosition,
    DocRiskLevel,
    DocRiskType,
    DocRunStatus,
    DocSummary,
)
from protocol.dspark import (  # noqa: F401
    DSPARK_CONTEXT_SIZE_DEFAULT,
    DSPARK_CONTEXT_SIZE_MAX,
    DSPARK_CONTEXT_SIZE_MIN,
    DSPARK_GPU_LAYERS_DEFAULT,
    DSPARK_GPU_LAYERS_MAX,
    DSPARK_GPU_LAYERS_MIN,
    DSPARK_N_DRAFT_MAX,
    DSPARK_N_DRAFT_MIN,
    DSPARK_SHORT_OUTPUT_DEFAULT,
    DSPARK_SHORT_OUTPUT_MIN,
    DSparkConfig,
    DSparkDecisionReason,
    DSparkDecisionRecord,
    SpeculativeMode,
    SpeculativePolicy,
)
from protocol.events import AgentStreamEvent  # noqa: F401
from protocol.mcp import McpServerInfo, McpServerStatus, McpToolSpec  # noqa: F401
from protocol.tools import ToolCall, ToolResult, ToolRiskLevel  # noqa: F401
