"""biznav —— 业务功能点导航（Phase 2G V1.1）。

定位：把「业务功能点」（Feature）从代码符号（codenav Symbol）拉出来，形成独立层次。
- codenav 管「代码符号」（类/方法/字段）
- biznav 管「业务功能点」（订单管理 / 审批流 / 对账），一个 Feature 关联一组
  related_files / related_apis / related_tables / business_rules

V1.1 范围：
    - 静态存储（FeatureStorage sync SQLite）
    - YAML/JSON 导入导出（FeatureIO）
    - 业务规则 snippet 注入（rule_engine）
    - LLM 提取（FeatureExtractor，4 阶段：scan → group → LLM → persist）
    - 10 个 FastAPI 路由（api）
    - 5 个 audit action 常量（audit）

不在 V1.1 内（V1.3 / V1.5 补）：
    - hot_reload 监听 .eaide/features/*.yaml
    - 增量 affected-features 计算
    - LLM Judge 评测
"""

from __future__ import annotations

from .audit import (
    EVT_FEATURE_DELETE,
    EVT_FEATURE_EXTRACT,
    EVT_FEATURE_IMPORT,
    EVT_FEATURE_UPDATE,
    EVT_YAML_RELOAD,
)
from .events import (
    EVT_EXTRACTION_DONE,
    EVT_FEATURE_AFFECTED,
    EVT_YAML_RELOADED,
    consume_biznav_events,
    emit_biznav_event,
    flush_biznav_events,
)
from .extractor import (
    ExtractionResult,
    FeatureExtractor,
)
from .hot_reload import YamlHotReloader, mark_yaml_written, reload_yaml_to_db
from .import_export import (
    FeatureImportError,
    FeatureIO,
)
from .incremental import AffectedFeaturesWatcher
from .models import (
    AffectedFeature,
    CandidateFileGroup,
    ExtractionJob,
    Feature,
    FeatureContextPayload,
    RelatedApi,
    RelatedFile,
    RelatedTable,
    SyncReport,
    feature_from_dict,
    feature_to_dict,
    related_files_from_json,
    related_files_to_json,
)
from .rule_engine import (
    BusinessRule,
    to_system_prompt_snippet,
    validate_syntax,
)
from .storage import (
    FeatureStorage,
    FeatureVersionConflict,
)

__all__ = [
    "EVT_EXTRACTION_DONE",
    "EVT_FEATURE_AFFECTED",
    "EVT_FEATURE_DELETE",
    # audit constants
    "EVT_FEATURE_EXTRACT",
    "EVT_FEATURE_IMPORT",
    "EVT_FEATURE_UPDATE",
    "EVT_YAML_RELOAD",
    # SSE event constants + helpers (V1.3)
    "EVT_YAML_RELOADED",
    # models
    "AffectedFeature",
    "AffectedFeaturesWatcher",
    # rule engine
    "BusinessRule",
    "CandidateFileGroup",
    "ExtractionJob",
    # extractor
    "ExtractionResult",
    "Feature",
    "FeatureContextPayload",
    "FeatureExtractor",
    # import / export
    "FeatureIO",
    "FeatureImportError",
    # storage
    "FeatureStorage",
    "FeatureVersionConflict",
    "RelatedApi",
    "RelatedFile",
    "RelatedTable",
    "SyncReport",
    # hot reload + incremental (V1.3)
    "YamlHotReloader",
    "consume_biznav_events",
    "emit_biznav_event",
    "feature_from_dict",
    "feature_to_dict",
    "flush_biznav_events",
    "mark_yaml_written",
    "related_files_from_json",
    "related_files_to_json",
    "reload_yaml_to_db",
    "to_system_prompt_snippet",
    "validate_syntax",
]
