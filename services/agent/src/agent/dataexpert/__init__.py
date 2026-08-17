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

v2.87 设计补强（2026-08-13）：
  - MetricResolver 抽象层（业务字典 / 指标平台 / dws 桥接 三档可切换配置）
  - DictMetricResolver V0 默认实现（包装 nl2sql/dictionary.py 业务字典，零代码改动）
  - PlatformMetricResolver / BridgeMetricResolver 占位（V1 / V1.5 接力）
  - build_resolver 工厂 + EAIDE_METRIC_RESOLVER 环境变量切换（V0 唯一生效来源；yaml 预留未接线）
  - _LOCAL_ONLY_TASKS 加 metric_resolve（指标识别含业务字典翻译，可能敏感）
"""

from __future__ import annotations

from agent.dataexpert.api import router as data_api_router
from agent.dataexpert.events import (
    EVT_DATA_CHART_READY,
    EVT_DATA_EXPORT_DONE,
    EVT_DATA_PYTHON_RESULT,
    EVT_DATA_QUERY_RESULT,
)
from agent.dataexpert.metric_resolver import (
    AggKind,
    BridgeMetricResolver,
    DictMetricResolver,
    MetricDef,
    MetricResolver,
    MetricResolverConfigError,
    PlatformMetricResolver,
    ResolvedQuery,
    SourceKind,
    build_resolver,
    get_default_resolver,
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
    # 事件常量
    "EVT_DATA_CHART_READY",
    "EVT_DATA_EXPORT_DONE",
    "EVT_DATA_PYTHON_RESULT",
    "EVT_DATA_QUERY_RESULT",
    "AggKind",
    "AnalysisTask",
    "BridgeMetricResolver",
    "DataExpertStorage",
    "DataSource",
    "DictMetricResolver",
    "ExportFormat",
    # 数据类
    "MetricDef",
    # v2.87 MetricResolver
    "MetricResolver",
    "MetricResolverConfigError",
    "PlatformMetricResolver",
    "ReportTemplate",
    "ResolvedQuery",
    "SandboxResult",
    "SourceKind",
    "SourceType",
    "SqlResult",
    "TableSchema",
    "WriteBlockedError",
    # 工具
    "build_resolver",
    # API router
    "data_api_router",
    # 只读闸
    "enforce_readonly",
    "generate_id",
    "get_default_resolver",
    "get_default_storage",
    "inject_limit",
    "is_heavy",
    "now_epoch",
    "reset_default_storage",
]
