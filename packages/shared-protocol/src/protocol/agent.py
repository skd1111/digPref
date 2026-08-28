"""ChatMessage, TraceStep, AgentRun — Pydantic mirrors of the TS types."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from protocol.approval import ApprovalRequest

ChatRole = Literal["user", "assistant", "tool", "system"]
AgentRunStatus = Literal["idle", "running", "awaiting_approval", "done", "error", "cancelled"]
ExecutionStatus = Literal["running", "ok", "err"]


class ChatMessage(BaseModel):
    id: str
    role: ChatRole
    content: str
    code: str | None = None
    code_lang: Literal["sql", "json", "python", "bash"] | None = Field(
        default=None, alias="codeLang"
    )
    pending_approval: ApprovalRequest | None = Field(default=None, alias="pendingApproval")
    # Phase 12 V1: Codex/Claude 风格执行链路 — execution kind 让前端渲染成折叠 step 块
    # 2026-08-07：error kind = 流异常终止的系统消息，前端渲染「重试」按钮
    # 2026-08-10：search kind = 搜索/检索类工具调用，前端渲染 aicss 风格搜索卡片
    # 2026-08-19：changed_files kind = 任务结束汇总的改动文件清单（content 为路径 JSON 数组，
    #              前端渲染可点击卡片，点击在 Monaco 打开）
    # 2026-08-26：task_cleanup_confirm kind = 交付后验收清理卡（content 为 {taskId, taskDir} JSON）
    # message/tool_call/tool_result/trace/approval/log/done = events.py 流事件子类使用的 kind
    # skill_matched（Phase 2D）/ heartbeat（BUGFIX #161 看门狗）同为流事件子类 kind
    # run_started/tool_progress/shell_chunk/file_write_preview：执行过程可视化细粒度事件
    kind: (
        Literal[
            "normal",
            "execution",
            "error",
            "search",
            "changed_files",
            "todo",
            "task_cleanup_confirm",
            "message",
            "tool_call",
            "tool_result",
            "trace",
            "approval",
            "log",
            "done",
            "skill_matched",
            "heartbeat",
            "run_started",
            "tool_progress",
            "shell_chunk",
            "file_write_preview",
        ]
        | None
    ) = None
    category: str | None = None
    latency_ms: int | None = Field(default=None, alias="latencyMs")
    status: ExecutionStatus | None = None
    run_id: str | None = Field(default=None, alias="runId")


class AgentRun(BaseModel):
    run_id: str = Field(alias="runId")
    status: AgentRunStatus
    started_at: datetime = Field(alias="startedAt")
    finished_at: datetime | None = Field(default=None, alias="finishedAt")
    error: str | None = None


class TodoItem(BaseModel):
    """待办项（2026-08-25）：模型把多步任务拆成待办并实时更新状态。"""

    content: str
    status: Literal["pending", "in_progress", "done"]


class TraceStep(BaseModel):
    id: str
    node: str
    status: Literal["ok", "fail", "running"]
    duration_ms: int = Field(alias="durationMs")
    summary: str | None = None
    # 任务进度待办列表（2026-08-25）：update_todos 伪工具经 trace 通道下发，
    # 前端按固定 id 原地更新渲染进度卡片
    todos: list[TodoItem] | None = None
