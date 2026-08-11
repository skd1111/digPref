"""Phase 7 V0 · 数据专家模式 —— 数据模型。

V0 范围：
  - DataSource / AnalysisTask / ReportTemplate / SqlResult / SandboxResult 数据类
  - SourceType / ExportFormat 枚举
  - TableSchema / ColumnSchema 辅助结构
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum

# ---- 枚举 -------------------------------------------------------------------


class SourceType(str, Enum):
    """数据源类型。"""

    MYSQL = "mysql"
    ORACLE = "oracle"
    CSV = "csv"
    EXCEL = "excel"


class ExportFormat(str, Enum):
    """导出格式。"""

    EXCEL = "excel"
    PDF = "pdf"
    CSV = "csv"


# ---- 辅助结构 -----------------------------------------------------------------


@dataclass
class ColumnSchema:
    """字段元数据（含中文注释）。"""

    name: str
    dtype: str
    comment: str = ""
    is_primary: bool = False
    is_foreign: bool = False


@dataclass
class TableSchema:
    """表结构（含字段列表 + 中文注释）。"""

    name: str
    comment: str = ""
    columns: list[ColumnSchema] = field(default_factory=list)


# ---- 核心数据类 ---------------------------------------------------------------


@dataclass
class DataSource:
    """数据源注册信息。

    Attributes:
        id: UUID4 hex。
        name: 显示名（"核心账务系统"）。
        type: 数据源类型。
        connection_ref: Keyring 引用（禁明文，遵 CLAUDE.md §5）。
        schema_cache: 表结构缓存（JSON 序列化后的 list[TableSchema]）。
        updated_at: 最后同步时间戳（epoch seconds）。
    """

    id: str
    name: str
    type: SourceType
    connection_ref: str = ""
    schema_cache: list[TableSchema] = field(default_factory=list)
    updated_at: int = 0


@dataclass
class AnalysisTask:
    """分析任务（SQL / Python 脚本 + 结果元数据）。

    Attributes:
        id: UUID4 hex。
        name: 任务名（"分行月度坏账率统计"）。
        user_id: 创建人。
        query_sql: 最终执行的只读 SQL。
        python_script: 数据清洗 Python 脚本（可选）。
        result_metadata: 列名、数据类型、行数（dict）。
        result_data_ref: 结果集 Parquet 文件路径（大对象不入库）。
        chart_config: ECharts 配置项（dict）。
        created_at: 创建时间戳。
    """

    id: str
    name: str
    user_id: str = ""
    query_sql: str = ""
    python_script: str = ""
    result_metadata: dict = field(default_factory=dict)
    result_data_ref: str = ""
    chart_config: dict = field(default_factory=dict)
    created_at: int = 0


@dataclass
class ReportTemplate:
    """报表模板（可复用的分析逻辑）。

    Attributes:
        id: UUID4 hex。
        name: 模板名（"分行月度坏账率统计"）。
        description: 描述。
        task_id: 关联的分析任务 ID。
        schedule_cron: 定时执行 Cron（可选）。
        export_format: 导出格式。
        created_by: 创建人。
        is_public: 团队共享。
    """

    id: str
    name: str
    description: str = ""
    task_id: str = ""
    schedule_cron: str = ""
    export_format: ExportFormat = ExportFormat.EXCEL
    created_by: str = ""
    is_public: bool = False


@dataclass
class SqlResult:
    """SQL 执行结果（轻量元数据；大结果集走 Parquet）。"""

    columns: list[str] = field(default_factory=list)
    dtypes: list[str] = field(default_factory=list)
    row_count: int = 0
    elapsed_ms: int = 0
    data_ref: str = ""  # Parquet 文件路径
    truncated: bool = False  # 是否被 LIMIT 截断


@dataclass
class SandboxResult:
    """Python 沙箱执行结果。"""

    ok: bool = True
    out_df_ref: str = ""  # 输出 DataFrame Parquet 路径
    stdout: str = ""
    error: str = ""
    mem_peak_mb: float = 0.0
    elapsed_s: float = 0.0


# ---- 工具 --------------------------------------------------------------------


def generate_id() -> str:
    """生成 UUID4 hex（32 chars）。"""
    return uuid.uuid4().hex


def now_epoch() -> int:
    """当前时间戳（epoch seconds）。"""
    return int(time.time())
