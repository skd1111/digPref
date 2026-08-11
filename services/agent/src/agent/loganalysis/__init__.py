"""agent.loganalysis —— Phase 2F+ V1 日志分析引擎。

公开 API：
- 数据类：ErrorBlock / RootCauseRequest / RootCauseResponse / LogLevelResult / AnalysisCacheEntry
- 提取：extract_error_blocks / detect_level / assert_known_level
- 脱敏：scrub_text / scrub_lines / scrub_error_block / scrub_error_blocks
- 路由：analyze_root_cause / classify_log_levels
- 存储：LogAnalysisStorage + get_default_storage + reset_default_storage
- API：FastAPI router（/loganalysis/*）
- BLOB：encode_u64_le / decode_u64_le（与 logviewer/storage.rs 对齐）
"""

from __future__ import annotations

# 提取
from agent.loganalysis.extractor import (
    assert_known_level,
    detect_level,
    extract_error_blocks,
    level_to_color_hint,
)

# 数据类
from agent.loganalysis.models import (
    ALL_LEVELS,
    LEVEL_DEBUG,
    LEVEL_ERROR,
    LEVEL_FATAL,
    LEVEL_INFO,
    LEVEL_TRACE,
    LEVEL_WARN,
    AnalysisCacheEntry,
    ErrorBlock,
    LogLevelClassifyResponse,
    LogLevelResult,
    RootCauseRequest,
    RootCauseResponse,
    gen_request_id,
)

# 路由（LLM dispatch）
from agent.loganalysis.router import (
    analyze_root_cause,
    classify_log_levels,
)

# 脱敏
from agent.loganalysis.scrubber import (
    scrub_error_block,
    scrub_error_blocks,
    scrub_lines,
    scrub_text,
)

# 存储
from agent.loganalysis.storage import (
    LogAnalysisStorage,
    decode_u64_le,
    encode_u64_le,
    get_default_storage,
    reset_default_storage,
)

__all__ = [
    # 数据类
    "ALL_LEVELS",
    "LEVEL_DEBUG",
    "LEVEL_ERROR",
    "LEVEL_FATAL",
    "LEVEL_INFO",
    "LEVEL_TRACE",
    "LEVEL_WARN",
    "AnalysisCacheEntry",
    "ErrorBlock",
    # 存储
    "LogAnalysisStorage",
    "LogLevelClassifyResponse",
    "LogLevelResult",
    "RootCauseRequest",
    "RootCauseResponse",
    # 路由
    "analyze_root_cause",
    # 提取
    "assert_known_level",
    "classify_log_levels",
    "decode_u64_le",
    "detect_level",
    "encode_u64_le",
    "extract_error_blocks",
    "gen_request_id",
    "get_default_storage",
    "level_to_color_hint",
    "reset_default_storage",
    # 脱敏
    "scrub_error_block",
    "scrub_error_blocks",
    "scrub_lines",
    "scrub_text",
]
