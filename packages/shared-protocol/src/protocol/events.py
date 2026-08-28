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
    # 任务级工作目录（2026-08-26）：本轮属于哪个任务文件夹（前端弹验收清理卡用）
    task_id: str | None = Field(default=None, alias="taskId")
    task_dir: str | None = Field(default=None, alias="taskDir")


class SkillMatchedEvent(ChatMessage):
    kind: Literal["skill_matched"] = "skill_matched"
    skill_id: str = Field(alias="skill_id")
    skill_name: str = Field(default="", alias="skill_name")
    confidence: float = 0.0


class ErrorEvent(ChatMessage):
    kind: Literal["error"] = "error"
    message: str


class HeartbeatEvent(ChatMessage):
    """流保活心跳（BUGFIX #161）：后端每 15s 无图块时下发，
    前端看门狗据此感知流存活，静默超阈判定断连并解锁。"""

    kind: Literal["heartbeat"] = "heartbeat"
    run_id: str | None = Field(default=None, alias="runId")


class RunStartedEvent(ChatMessage):
    """run 显式开始事件：SSE 流建立后第一帧，前端据此锁定页签忙碌态。
    （此前靠流建立隐式感知；多会话并发时显式事件防串台判定漂移。）"""

    kind: Literal["run_started"] = "run_started"


class ToolProgressEvent(ChatMessage):
    """工具执行中进度：长耗时工具（搜索/编译/批处理）推送阶段文案，
    前端把 running 卡片文案从工具名细化为进度描述。"""

    kind: Literal["tool_progress"] = "tool_progress"
    call_id: str | None = Field(default=None, alias="call_id")
    tool_name: str | None = Field(default=None, alias="tool_name")
    message: str = ""
    percent: float | None = None


class ShellChunkEvent(ChatMessage):
    """shell 命令流式输出片段：执行期间逐批下发，结束帧带 exit_code。
    前端按 call_id 归并到对应工具卡的输出面板。"""

    kind: Literal["shell_chunk"] = "shell_chunk"
    call_id: str | None = Field(default=None, alias="call_id")
    stream: Literal["stdout", "stderr"] = "stdout"
    chunk: str = ""
    exit_code: int | None = Field(default=None, alias="exit_code")


class FileWritePreviewEvent(ChatMessage):
    """写前 Diff 预览：写类工具真正落盘前先下发 unified diff，
    前端渲染红绿 diff + 走 HITL 审批，批准后才执行写入。"""

    kind: Literal["file_write_preview"] = "file_write_preview"
    call_id: str | None = Field(default=None, alias="call_id")
    path: str = ""
    diff: str = ""
    risk_level: str | None = Field(default=None, alias="risk_level")


AgentStreamEvent = Annotated[
    MessageEvent
    | ToolCallEvent
    | ToolResultEvent
    | TraceEvent
    | ApprovalEvent
    | LogEvent
    | DoneEvent
    | SkillMatchedEvent
    | ErrorEvent
    | HeartbeatEvent
    | RunStartedEvent
    | ToolProgressEvent
    | ShellChunkEvent
    | FileWritePreviewEvent,
    Field(discriminator="kind"),
]
