"""Phase 16 · 思维链数据模型（ThinkingStep / FileOperation）。

与 SQLite `thinking_steps` 表一一映射；JSON 字段（tool_calls /
file_operations）序列化后入库。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---- 文件操作类型 ------------------------------------------------------------

OP_READ = "read"            # 读取文件
OP_WRITE = "write"          # 新建 / 覆盖写入
OP_EDIT = "edit"            # search-replace 编辑
OP_GREP = "grep"            # 内容搜索
OP_REFERENCE = "reference"  # 思考文本中引用（📄 标记识别）

ALL_OP_TYPES = (OP_READ, OP_WRITE, OP_EDIT, OP_GREP, OP_REFERENCE)


@dataclass
class FileOperation:
    """一次文件操作记录（含 unified diff 与预览片段）。"""

    type: str                                # OP_* 之一
    path: str                                # 文件路径（沙箱校验后的绝对路径）
    diff: str | None = None                  # unified diff（read/grep 为 None）
    preview: str | None = None               # diff 关键片段（前后 50 行）
    lines_added: int = 0                     # + 行数统计
    lines_removed: int = 0                   # - 行数统计
    start_line: int | None = None            # 读取/搜索的行范围起
    end_line: int | None = None              # 读取/搜索的行范围止
    ok: bool = True                          # 工具执行是否成功
    error: str | None = None                 # 失败原因

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "path": self.path,
            "diff": self.diff,
            "preview": self.preview,
            "lines_added": self.lines_added,
            "lines_removed": self.lines_removed,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "ok": self.ok,
            "error": self.error,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> FileOperation:
        return FileOperation(
            type=str(d.get("type", OP_REFERENCE)),
            path=str(d.get("path", "")),
            diff=d.get("diff"),
            preview=d.get("preview"),
            lines_added=int(d.get("lines_added", 0)),
            lines_removed=int(d.get("lines_removed", 0)),
            start_line=d.get("start_line"),
            end_line=d.get("end_line"),
            ok=bool(d.get("ok", True)),
            error=d.get("error"),
        )


@dataclass
class ThinkingStep:
    """思维链单步 —— LangGraph 一个节点的一次执行。"""

    session_id: str                          # 会话 / run id
    node_name: str                           # LangGraph 节点名
    step_index: int = 0                      # 本会话内递增序号
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    message_id: str | None = None
    thinking: str | None = None              # 中文思考内容（【思考】…）
    thinking_tokens: int | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    file_operations: list[FileOperation] = field(default_factory=list)
    decision: str | None = None              # 【决策】最终结论
    tokens_used: int | None = None
    latency_ms: int | None = None
    created_at: int = field(
        default_factory=lambda: int(datetime.now(timezone.utc).timestamp() * 1000)
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "message_id": self.message_id,
            "step_index": self.step_index,
            "node_name": self.node_name,
            "thinking": self.thinking,
            "thinking_tokens": self.thinking_tokens,
            "tool_calls": self.tool_calls,
            "file_operations": [op.to_dict() for op in self.file_operations],
            "decision": self.decision,
            "tokens_used": self.tokens_used,
            "latency_ms": self.latency_ms,
            "created_at": self.created_at,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> ThinkingStep:
        return ThinkingStep(
            id=str(d.get("id", uuid.uuid4().hex)),
            session_id=str(d.get("session_id", "")),
            message_id=d.get("message_id"),
            step_index=int(d.get("step_index", 0)),
            node_name=str(d.get("node_name", "")),
            thinking=d.get("thinking"),
            thinking_tokens=d.get("thinking_tokens"),
            tool_calls=list(d.get("tool_calls") or []),
            file_operations=[
                FileOperation.from_dict(op) for op in (d.get("file_operations") or [])
            ],
            decision=d.get("decision"),
            tokens_used=d.get("tokens_used"),
            latency_ms=d.get("latency_ms"),
            created_at=int(d.get("created_at", 0)),
        )
