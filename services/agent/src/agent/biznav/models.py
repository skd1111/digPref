"""biznav.models —— 业务功能点数据类（Phase 2G V1.1）。

设计：
- 全 dataclass + field(default_factory=list)；零外部依赖
- 字段顺序与设计文档 §3.3 严格对齐（related_files 必须在 related_apis 之前）
- JSON 列 encode/decode 集中在模块底部一对 helper：
    related_files_to_json / related_files_from_json
  同样的形态被 related_apis / related_tables / business_rules 复用
- Feature.from_dict / to_dict 兼容 YAML / JSON 反序列化、与 storage 列对齐
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# 关联类型
# ---------------------------------------------------------------------------


@dataclass
class RelatedFile:
    path: str
    role: str = ""


@dataclass
class RelatedApi:
    method: str
    path: str
    description: str = ""


@dataclass
class RelatedTable:
    name: str
    description: str = ""


# ---------------------------------------------------------------------------
# 业务规则（rule_engine 也用）
# ---------------------------------------------------------------------------


@dataclass
class BusinessRule:
    text: str
    structured: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "structured": self.structured}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BusinessRule:
        return cls(
            text=str(d.get("text", "")),
            structured=d.get("structured"),
        )


# ---------------------------------------------------------------------------
# Feature —— 业务功能点主表
# ---------------------------------------------------------------------------


@dataclass
class Feature:
    id: str
    name: str
    description: str = ""
    category: str = "未分类"
    project_name: str = ""
    project_root: str = ""
    # Phase 2H：绑定的业务 Skill（历史字段，保留兼容；运营链路已改由专家团承载）
    skill_id: str | None = None
    # 中期改造（2026-08-07）：功能点直连专家团预设（选中业务零延迟自动选团；
    # 未预设时由 recommender 拿功能点名 + 全部专家团描述让 LLM 判断）
    expert_team_ids: list[str] = field(default_factory=list)
    # 字段顺序相关_files 必须先于 related_apis（设计文档 §3.3 + 序列化兼容）
    related_files: list[RelatedFile] = field(default_factory=list)
    related_apis: list[RelatedApi] = field(default_factory=list)
    related_tables: list[RelatedTable] = field(default_factory=list)
    business_rules: list[BusinessRule] = field(default_factory=list)
    source: str = "ai"  # 'ai' | 'manual' | 'merged'
    ai_confidence: float | None = None
    version: int = 1
    created_at: int = 0
    updated_at: int = 0
    deleted_at: int | None = None

    # ---- 序列化 ---------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """导出 dict（落到 YAML/JSON 时使用）。"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "project_name": self.project_name,
            "project_root": self.project_root,
            "skill_id": self.skill_id,
            "expert_team_ids": list(self.expert_team_ids),
            "related_files": [{"path": rf.path, "role": rf.role} for rf in self.related_files],
            "related_apis": [
                {"method": a.method, "path": a.path, "description": a.description}
                for a in self.related_apis
            ],
            "related_tables": [
                {"name": t.name, "description": t.description} for t in self.related_tables
            ],
            "business_rules": [r.to_dict() for r in self.business_rules],
            "source": self.source,
            "ai_confidence": self.ai_confidence,
            "version": self.version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "deleted_at": self.deleted_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Feature:
        """从 dict 构造（YAML/JSON 导入 + 从 DB row 重建）。容错：缺字段给默认值。"""
        return cls(
            id=str(d.get("id", "")),
            name=str(d.get("name", "")),
            description=str(d.get("description", "")),
            category=str(d.get("category", "未分类")),
            project_name=str(d.get("project_name", "")),
            project_root=str(d.get("project_root", "")),
            skill_id=d.get("skill_id"),
            expert_team_ids=[str(x) for x in (d.get("expert_team_ids") or [])],
            related_files=[
                RelatedFile(path=str(rf.get("path", "")), role=str(rf.get("role", "")))
                for rf in (d.get("related_files") or [])
            ],
            related_apis=[
                RelatedApi(
                    method=str(a.get("method", "")),
                    path=str(a.get("path", "")),
                    description=str(a.get("description", "")),
                )
                for a in (d.get("related_apis") or [])
            ],
            related_tables=[
                RelatedTable(
                    name=str(t.get("name", "")),
                    description=str(t.get("description", "")),
                )
                for t in (d.get("related_tables") or [])
            ],
            business_rules=[BusinessRule.from_dict(r) for r in (d.get("business_rules") or [])],
            source=str(d.get("source", "ai")),
            ai_confidence=d.get("ai_confidence"),
            version=int(d.get("version", 1)),
            created_at=int(d.get("created_at", 0)),
            updated_at=int(d.get("updated_at", 0)),
            deleted_at=d.get("deleted_at"),
        )


# ---------------------------------------------------------------------------
# 上下文注入（FeatureContextPayload）
# ---------------------------------------------------------------------------


@dataclass
class FeatureContextPayload:
    """往 LLM 注入的业务上下文。"""

    feature_id: str
    feature_name: str
    feature_description: str
    related_files_content: dict[str, str]  # path -> 文件内容（截断后）
    business_rules: list[str]  # 平铺字符串
    related_apis: list[dict[str, str]]
    related_tables: list[dict[str, str]]


# ---------------------------------------------------------------------------
# 提取任务 + 启发式分组
# ---------------------------------------------------------------------------


@dataclass
class ExtractionJob:
    id: int  # SQLite AUTOINCREMENT 主键
    project_name: str
    project_root: str
    status: str  # 'pending'|'scanning'|'extracting'|'done'|'failed'
    total_files: int = 0
    processed_files: int = 0
    features_generated: int = 0
    error_message: str | None = None
    started_at: int = 0
    finished_at: int | None = None


@dataclass
class CandidateFileGroup:
    """extractor 启发式分组结果（按目录结构分类）。"""

    group_key: str
    role: str
    files: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# 增量 / 同步报告
# ---------------------------------------------------------------------------


@dataclass
class AffectedFeature:
    """V1.3 affected-features 增量更新结果（V1.1 只在 dataclass 层定义）。"""

    feature_id: str
    project_name: str
    change_summary: str
    change_kind: str  # 'content' | 'schema' | 'deletion'


@dataclass
class SyncReport:
    """YAML ↔ DB 合并结果。"""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0
    conflicts: list[dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# JSON 列 encode/decode helpers
# ---------------------------------------------------------------------------


def related_files_to_json(items: list[RelatedFile]) -> str:
    """RelatedFile 列表 → JSON 字符串（存 features.related_files 列）。"""
    return json.dumps([{"path": rf.path, "role": rf.role} for rf in items], ensure_ascii=False)


def related_files_from_json(s: str | None) -> list[RelatedFile]:
    """JSON 字符串 → RelatedFile 列表。空/None/坏字符串一律返空列表。"""
    if not s:
        return []
    try:
        raw = json.loads(s)
    except (ValueError, TypeError):
        return []
    out: list[RelatedFile] = []
    if not isinstance(raw, list):
        return []
    for item in raw:
        if isinstance(item, dict):
            out.append(
                RelatedFile(
                    path=str(item.get("path", "")),
                    role=str(item.get("role", "")),
                )
            )
    return out


def _related_apis_to_json(items: list[RelatedApi]) -> str:
    return json.dumps(
        [{"method": a.method, "path": a.path, "description": a.description} for a in items],
        ensure_ascii=False,
    )


def _related_apis_from_json(s: str | None) -> list[RelatedApi]:
    if not s:
        return []
    try:
        raw = json.loads(s)
    except (ValueError, TypeError):
        return []
    out: list[RelatedApi] = []
    if not isinstance(raw, list):
        return []
    for item in raw:
        if isinstance(item, dict):
            out.append(
                RelatedApi(
                    method=str(item.get("method", "")),
                    path=str(item.get("path", "")),
                    description=str(item.get("description", "")),
                )
            )
    return out


def _related_tables_to_json(items: list[RelatedTable]) -> str:
    return json.dumps(
        [{"name": t.name, "description": t.description} for t in items],
        ensure_ascii=False,
    )


def _related_tables_from_json(s: str | None) -> list[RelatedTable]:
    if not s:
        return []
    try:
        raw = json.loads(s)
    except (ValueError, TypeError):
        return []
    out: list[RelatedTable] = []
    if not isinstance(raw, list):
        return []
    for item in raw:
        if isinstance(item, dict):
            out.append(
                RelatedTable(
                    name=str(item.get("name", "")),
                    description=str(item.get("description", "")),
                )
            )
    return out


def _business_rules_to_json(items: list[BusinessRule]) -> str:
    return json.dumps([r.to_dict() for r in items], ensure_ascii=False)


def _expert_team_ids_from_json(s: str | None) -> list[str]:
    """JSON 字符串 → 专家团 id 列表。空/None/坏字符串一律返空列表。"""
    if not s:
        return []
    try:
        raw = json.loads(s)
    except (ValueError, TypeError):
        return []
    if not isinstance(raw, list):
        return []
    return [str(x) for x in raw]


def _business_rules_from_json(s: str | None) -> list[BusinessRule]:
    if not s:
        return []
    try:
        raw = json.loads(s)
    except (ValueError, TypeError):
        return []
    out: list[BusinessRule] = []
    if not isinstance(raw, list):
        return []
    for item in raw:
        if isinstance(item, dict):
            out.append(BusinessRule.from_dict(item))
    return out


# 对外同样名字的 alias —— FeatureStorage 用得到
feature_to_dict = Feature.to_dict
feature_from_dict = Feature.from_dict
