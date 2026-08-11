"""biznav.import_export —— FeatureIO（YAML/JSON 序列化 + 同步）（Phase 2G V1.1）。

设计：
- to_yaml / to_json 把 list[Feature] 序列化为字符串
- from_yaml / from_json 严格校验 id/name/category 必填，否则抛 FeatureImportError
- sync_yaml_to_db：合并策略：source='ai' → YAML 覆盖；source='manual' → 保留 DB + 写 conflicts
- sync_db_to_yaml：DB 全量 dump 成 YAML
- 不依赖 yaml 之外的东西；yaml.safe_dump(allow_unicode=True, sort_keys=False)
"""

from __future__ import annotations

import json

import yaml

from .models import Feature, SyncReport
from .storage import FeatureStorage


class FeatureImportError(Exception):
    """YAML / JSON 反序列化失败或字段校验失败。"""


class FeatureIO:
    """纯静态方法集合；不持有状态。"""

    # ---- 导出 -----------------------------------------------------------

    @staticmethod
    def to_yaml(
        project_name: str,
        project_root: str,
        features: list[Feature],
        generated_at: str,
    ) -> str:
        """按设计文档 §3.2 YAML 格式：

        project_name: xxx
        project_root: xxx
        generated_at: 2026-07-28T...
        features:
          - id: ...
            name: ...
            ...
        """
        doc = {
            "project_name": project_name,
            "project_root": project_root,
            "generated_at": generated_at,
            "features": [f.to_dict() for f in features],
        }
        return yaml.safe_dump(
            doc,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )

    @staticmethod
    def to_json(features: list[Feature]) -> str:
        return json.dumps([f.to_dict() for f in features], ensure_ascii=False, indent=2)

    # ---- 导入 -----------------------------------------------------------

    @staticmethod
    def from_yaml(yaml_text: str) -> list[Feature]:
        if not yaml_text or not yaml_text.strip():
            raise FeatureImportError("yaml text is empty")
        try:
            doc = yaml.safe_load(yaml_text)
        except yaml.YAMLError as e:
            raise FeatureImportError(f"yaml parse error: {e}") from e
        if not isinstance(doc, dict):
            raise FeatureImportError(
                "yaml root must be a mapping with project_name/project_root/features"
            )
        features_raw = doc.get("features") or []
        if not isinstance(features_raw, list):
            raise FeatureImportError("'features' must be a list")
        out: list[Feature] = []
        for item in features_raw:
            if not isinstance(item, dict):
                raise FeatureImportError("each feature item must be a mapping")
            if not item.get("id") or not item.get("name") or not item.get("category"):
                raise FeatureImportError(
                    f"feature missing required fields (id/name/category): {item}"
                )
            out.append(Feature.from_dict(item))
        return out

    @staticmethod
    def from_json(json_text: str) -> list[Feature]:
        if not json_text or not json_text.strip():
            raise FeatureImportError("json text is empty")
        try:
            raw = json.loads(json_text)
        except json.JSONDecodeError as e:
            raise FeatureImportError(f"json parse error: {e}") from e
        if not isinstance(raw, list):
            raise FeatureImportError("json must be a list of features")
        out: list[Feature] = []
        for item in raw:
            if not isinstance(item, dict):
                raise FeatureImportError("each feature item must be a mapping")
            if not item.get("id") or not item.get("name") or not item.get("category"):
                raise FeatureImportError(
                    f"feature missing required fields (id/name/category): {item}"
                )
            out.append(Feature.from_dict(item))
        return out

    # ---- 同步（合并） ----------------------------------------------------

    @staticmethod
    def sync_yaml_to_db(
        yaml_text: str,
        project_name: str,
        storage: FeatureStorage,
    ) -> SyncReport:
        """YAML → DB 合并：
        - 同 id + DB.source='ai' → YAML 覆盖（强制 source='ai'）
        - 同 id + DB.source='manual' → 保留 DB + 写 conflicts
        - 新 id → 插入（source='merged'）
        - YAML 没有但 DB 里有 → 不删（人工删除可能有意）
        """
        incoming = FeatureIO.from_yaml(yaml_text)
        report = SyncReport()
        with_storage_features = {
            f.id: f for f in storage.list_by_project(project_name, include_deleted=True)
        }
        for f in incoming:
            # 强制 project_name 一致
            f.project_name = project_name
            existing = with_storage_features.get(f.id)
            if existing is None:
                # 新增（含 deleted_at 也要复活？V1.1 直接插入）
                if f.source in (None, "", "ai", "merged"):
                    f.source = "merged"
                storage.upsert(f)
                report.inserted += 1
            else:
                if existing.source == "manual":
                    # 保留 DB；发生冲突
                    report.skipped += 1
                    report.conflicts.append(
                        {
                            "feature_id": f.id,
                            "reason": "manual source preserved",
                            "db_source": "manual",
                            "yaml_source": f.source,
                        }
                    )
                else:
                    # YAML 覆盖 DB
                    f.source = "ai"
                    f.created_at = existing.created_at  # 保留原 created_at
                    storage.upsert(f)
                    report.updated += 1
        return report

    @staticmethod
    def sync_db_to_yaml(
        storage: FeatureStorage,
        project_name: str,
        project_root: str,
    ) -> str:
        """DB → YAML（包含软删除？V1.1 不包含 —— 落入 export 给用户）。"""
        from datetime import datetime, timezone

        features = storage.list_by_project(project_name, include_deleted=False)
        return FeatureIO.to_yaml(
            project_name=project_name,
            project_root=project_root,
            features=features,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
