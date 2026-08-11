"""Phase 7 V0 · 数据专家模式 —— V0 公开 API。

V0 范围：
  - 数据模型（DataSource / AnalysisTask / ReportTemplate / SqlResult / SandboxResult）
  - 只读闸（enforce_readonly / inject_limit / is_heavy）
  - NL2SQL（Schema 链接 + 业务字典 + Few-shot + LMRouter）
  - Python 沙箱（AST 校验 + 受限子进程）
  - 可视化（图表推荐 + ECharts 配置）
  - 导出（Excel / PDF / CSV + 水印 + PII 脱敏）
  - FastAPI 9 端点（/data/*）
  - SSE 三处同步 4 新事件
"""

from __future__ import annotations

from agent.dataexpert.api import router as data_api_router
from agent.dataexpert.events import (
    EVT_DATA_CHART_READY,
    EVT_DATA_EXPORT_DONE,
    EVT_DATA_PYTHON_RESULT,
    EVT_DATA_QUERY_RESULT,
)
from agent.dataexpert.models import (
    AnalysisTask,
    DataSource,
    ExportFormat,
    ReportTemplate,
    SandboxResult,
    SourceType,
    SqlResult,
    TableSchema,
    generate_id,
    now_epoch,
)
from agent.dataexpert.readonly.guard import (
    WriteBlockedError,
    enforce_readonly,
    inject_limit,
    is_heavy,
)
from agent.dataexpert.storage import (
    DataExpertStorage,
    get_default_storage,
    reset_default_storage,
)

__all__ = [
    "EVT_DATA_CHART_READY",
    "EVT_DATA_EXPORT_DONE",
    "EVT_DATA_PYTHON_RESULT",
    # 事件常量
    "EVT_DATA_QUERY_RESULT",
    "AnalysisTask",
    # 存储
    "DataExpertStorage",
    # 数据类
    "DataSource",
    "ExportFormat",
    "ReportTemplate",
    "SandboxResult",
    "SourceType",
    "SqlResult",
    "TableSchema",
    "WriteBlockedError",
    # API router
    "data_api_router",
    # 只读闸
    "enforce_readonly",
    # 工具
    "generate_id",
    "get_default_storage",
    "inject_limit",
    "is_heavy",
    "now_epoch",
    "reset_default_storage",
]
