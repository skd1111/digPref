"""loganalysis.models —— Phase 2F+ V1 日志分析数据类。

设计：
- 全 dataclass + field(default_factory=list)；零外部依赖
- 与 api.py 的 Pydantic schema + extractor 的输入输出三处对齐
- 与 biznav.models 同风格（field 顺序按使用频率排）

调用方：
- extractor.py 产出 ErrorBlock[] → LLM
- scrubber.py 改写 stack_trace 文本
- api.py 接 ErrorBlock[] → RootCauseResponse
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional


# ---- 日志级别常量 ---------------------------------------------------------

LEVEL_DEBUG = "DEBUG"
LEVEL_INFO = "INFO"
LEVEL_WARN = "WARN"
LEVEL_ERROR = "ERROR"
LEVEL_TRACE = "TRACE"
LEVEL_FATAL = "FATAL"

ALL_LEVELS: tuple[str, ...] = (
    LEVEL_DEBUG, LEVEL_INFO, LEVEL_WARN, LEVEL_ERROR, LEVEL_TRACE, LEVEL_FATAL,
)


# ---- ERROR 块 -------------------------------------------------------------


@dataclass
class ErrorBlock:
    """日志中的一段连续错误（含堆栈）。

    一个 block 由 1 个 ERROR 头 + 若干堆栈/上下文行组成；超过 `max_stack_lines`
    自动截断（架构师红线：避免 OOM 与 LLM token 爆炸）。
    """

    start_line: int               # 1-based
    end_line: int                 # 1-based
    header: str                   # 触发 ERROR 块的第一行
    stack_trace: list[str]        # 含 header 的所有行
    level: str = LEVEL_ERROR      # DEBUG/INFO/WARN/ERROR/FATAL/TRACE
    fingerprint: str = ""         # SHA-256(stack_trace) 用于去重

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_line": self.start_line,
            "end_line": self.end_line,
            "header": self.header,
            "stack_trace": list(self.stack_trace),
            "level": self.level,
            "fingerprint": self.fingerprint,
            "line_count": len(self.stack_trace),
        }


# ---- 分析请求 / 响应 -------------------------------------------------------


@dataclass
class RootCauseRequest:
    """POST /loganalysis/root-cause 请求体（Python 内部表示）。"""

    file_path: str
    error_blocks: list[ErrorBlock] = field(default_factory=list)
    context_window: int = 100     # 额外附带 ERROR 块前后多少行做时序上下文
    context_window_lines: list[str] = field(default_factory=list)
    max_tokens: int = 3000        # 架构师红线：L1/L2 token 上限
    analysis_type: str = "log_root_cause"  # 'log_root_cause' | 'log_level_classify'


@dataclass
class RootCauseResponse:
    """POST /loganalysis/root-cause 响应体。"""

    summary: str
    error_count: int
    blocks_analyzed: int
    tokens_used: int = 0
    model_used: str = ""
    elapsed_ms: int = 0
    backend: str = "private"      # 'private' | 'local' | 'mock'
    blocks: list[ErrorBlock] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "error_count": self.error_count,
            "blocks_analyzed": self.blocks_analyzed,
            "tokens_used": self.tokens_used,
            "model_used": self.model_used,
            "elapsed_ms": self.elapsed_ms,
            "backend": self.backend,
            "blocks": [b.to_dict() for b in self.blocks],
        }


# ---- 日志级别分类 ---------------------------------------------------------


@dataclass
class LogLevelResult:
    """单行日志的级别分类结果（来自本地小模型 log_level_classify）。"""

    line: str
    predicted_level: str         # ALL_LEVELS 之一
    confidence: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "line": self.line,
            "predicted_level": self.predicted_level,
            "confidence": self.confidence,
        }


@dataclass
class LogLevelClassifyResponse:
    """批量级别分类响应。"""

    results: list[LogLevelResult] = field(default_factory=list)
    elapsed_ms: int = 0
    backend: str = "local_small"  # 'local_small' | 'ollama' | 'mock'

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "elapsed_ms": self.elapsed_ms,
            "backend": self.backend,
        }


# ---- AI 分析缓存 ----------------------------------------------------------


@dataclass
class AnalysisCacheEntry:
    """log_analysis_cache 表的一行。"""

    id: int = 0
    cache_key: str = ""           # sha256(file_fingerprint + summary)
    file_path: str = ""
    file_fingerprint: str = ""
    analysis_type: str = ""
    payload_json: str = ""        # RootCauseResponse / LogLevelClassifyResponse 的 JSON
    created_at: int = 0
    expires_at: int = 0

    @classmethod
    def new(
        cls,
        *,
        cache_key: str,
        file_path: str,
        file_fingerprint: str,
        analysis_type: str,
        payload_json: str,
        ttl_sec: int = 3600,
    ) -> "AnalysisCacheEntry":
        now = int(time.time())
        return cls(
            cache_key=cache_key,
            file_path=file_path,
            file_fingerprint=file_fingerprint,
            analysis_type=analysis_type,
            payload_json=payload_json,
            created_at=now,
            expires_at=now + ttl_sec,
        )

    def is_expired(self, now: int | None = None) -> bool:
        if now is None:
            now = int(time.time())
        return self.expires_at <= now


# ---- 工具函数 -------------------------------------------------------------


def gen_request_id() -> str:
    """生成请求 ID（SSE 事件关联）。"""
    return str(uuid.uuid4())