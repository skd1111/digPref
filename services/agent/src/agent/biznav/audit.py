"""biznav.audit —— 业务功能点审计事件常量。

Phase 2G V1.1 (2026-07-28): 集中定义 5 个 audit action 字符串常量，避免
`biznav/api.py` 各路由散落字符串字面量。两侧审计表（Python `audit.sqlite`
+ Rust `audit.sqlite`）当前 **无 CHECK 约束**（CLAUDE.md §6 + 实际两 schema
都只有 `action TEXT` 列），新增 action 不需 schema 迁移；只需约定字符串。

约定（与 docs/design/phase-2g-business-nav.md §11.1 对齐）：
    FEATURE_EXTRACT    — extractor 完成（异步任务完成时一次性写）
    FEATURE_UPDATE     — UI 编辑保存（人工微调；source='ai' → 'manual' 转换点）
    FEATURE_DELETE     — 软删除 / 硬删除
    FEATURE_IMPORT     — YAML/JSON 导入（外部配置恢复）
    YAML_RELOAD        — hot_reload 检测到 features.yaml 变更 + 防自激合并完成

后续 V1.3 hot_reload.py emit 使用 YAML_RELOAD；V1.1 / V1.2 不 emit。
"""
from __future__ import annotations

# 5 个 audit action 字符串常量
EVT_FEATURE_EXTRACT = "FEATURE_EXTRACT"
EVT_FEATURE_UPDATE = "FEATURE_UPDATE"
EVT_FEATURE_DELETE = "FEATURE_DELETE"
EVT_FEATURE_IMPORT = "FEATURE_IMPORT"
EVT_YAML_RELOAD = "YAML_RELOAD"

__all__ = [
    "EVT_FEATURE_EXTRACT",
    "EVT_FEATURE_UPDATE",
    "EVT_FEATURE_DELETE",
    "EVT_FEATURE_IMPORT",
    "EVT_YAML_RELOAD",
]
