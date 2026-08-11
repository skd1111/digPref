"""AgentStreamEvent — discriminated union over the wire."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from protocol.agent import ChatMessage, TraceStep
from protocol.approval import ApprovalRequest
from protocol.tools import ToolCall, ToolResult


class MessageEvent(ChatMessage):
    kind: Literal["message"] = "message"


class ToolCallEvent(ChatMessage):
    kind: Literal["tool_call"] = "tool_call"
    id: str
    call: ToolCall


class ToolResultEvent(ChatMessage):
    kind: Literal["tool_result"] = "tool_result"
    id: str
    result: ToolResult


class TraceEvent(ChatMessage):
    kind: Literal["trace"] = "trace"
    step: TraceStep


class ApprovalEvent(ChatMessage):
    kind: Literal["approval"] = "approval"
    approval: ApprovalRequest


class LogEvent(ChatMessage):
    kind: Literal["log"] = "log"
    line: str


class DoneEvent(ChatMessage):
    kind: Literal["done"] = "done"
    run_id: str = Field(alias="runId")


class ErrorEvent(ChatMessage):
    kind: Literal["error"] = "error"
    message: str


AgentStreamEvent = Annotated[
    MessageEvent
    | ToolCallEvent
    | ToolResultEvent
    | TraceEvent
    | ApprovalEvent
    | LogEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="kind"),
]
