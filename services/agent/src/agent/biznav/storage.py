"""biznav.storage —— 业务功能点同步 SQLite 存储（Phase 2G V1.1）。

设计要点（与 codenav/query.py 一致）：
- 所有方法**同步**。上层若在 async 上下文里调用，使用 asyncio.to_thread() 包裹；
  API 层 FastAPI sync route 直接调用即可。
- 内部用 connection-per-call + `with` 上下文，避免跨线程锁。
- 乐观锁：UPDATE WHERE version=? + 自增 1；affect 0 行 → 抛 FeatureVersionConflict。
- 每次 upsert 强制写 feature_edit_history（V1.1 不允许关闭）。
- 反向索引 feature_file_index 通过 ON CONFLICT IGNORE 维护。

红线：
- 不用 aiosqlite（避免与运行 event loop 冲突）
- 不持久化任何敏感字段（业务规则只是业务文本，不含密钥）
- 软删除保留行（deleted_at = now()），硬删除级联清 feature_file_index
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .models import (
    Feature,
    _business_rules_from_json,
    _business_rules_to_json,
    _expert_team_ids_from_json,
    _related_apis_from_json,
    _related_apis_to_json,
    _related_tables_from_json,
    _related_tables_to_json,
    related_files_from_json,
    related_files_to_json,
)
from .rule_engine import validate_syntax


class FeatureVersionConflict(Exception):
    """乐观锁版本冲突：expected_version 与 DB 中 version 不一致。"""


def now() -> int:
    """毫秒时间戳（与 audit tool 保持一致风格，但 V1.1 内部全部用毫秒）。"""
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# FeatureStorage
# ---------------------------------------------------------------------------


class FeatureStorage:
    """biznav 同步 SQLite 封装。sync sqlite3 + per-call connection."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(self._read_schema_sql())
        # Phase 2H 迁移：存量 biznav.db 无 skill_id 列 → ALTER 增量加列（幂等）
        self._migrate_skill_id()
        # 中期改造迁移：存量库无 expert_team_ids 列 → ALTER 增量加列（幂等）
        self._migrate_expert_team_ids()

    def _migrate_skill_id(self) -> None:
        """为存量库加 skill_id 列（新库已在 CREATE TABLE 中含该列，ALTER 静默跳过）。"""
        with self._connect() as conn:
            try:
                conn.execute("ALTER TABLE features ADD COLUMN skill_id TEXT")
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" not in msg:
                    raise

    def _migrate_expert_team_ids(self) -> None:
        """为存量库加 expert_team_ids 列（功能点直连专家团预设，新库已在 CREATE TABLE 中）。"""
        with self._connect() as conn:
            try:
                conn.execute("ALTER TABLE features ADD COLUMN expert_team_ids TEXT")
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column" not in msg:
                    raise

    # ---- schema ----------------------------------------------------------

    @staticmethod
    def _read_schema_sql() -> str:
        here = Path(__file__).resolve().parent
        sql_path = here / "schema.sql"
        return sql_path.read_text(encoding="utf-8")

    def _connect(self) -> sqlite3.Connection:
        # timeout=5 让 SQLITE_BUSY 自动重试最多 5s（解决 upsert + add_file_index
        # 双 connection 偶发的 database is locked）；isolation_level=None 让
        # sqlite3 自动 BEGIN/COMMIT 配合 with 上下文清晰
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ---- CRUD ------------------------------------------------------------

    def upsert(self, feature: Feature) -> None:
        """插入或更新；触发乐观锁 + 强制写 feature_edit_history。

        规则：
        - 若同 id 存在且 expected_version 传入：按 version 匹配更新；否则按 id 强覆盖
        - VALID：business_rules 中每条必须通过 validate_syntax，否则抛 ValueError
        - 自动维护 created_at（首次）/ updated_at / version+1
        """
        # 1. 校验 business_rules
        for r in feature.business_rules:
            errs = validate_syntax(r)
            if errs:
                raise ValueError("invalid business rule: " + "; ".join(errs))

        ts = now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM features WHERE id = ? AND project_name = ?",
                (feature.id, feature.project_name),
            ).fetchone()

            before_json = None
            if row is None:
                # 新增
                if feature.created_at <= 0:
                    feature.created_at = ts
                feature.updated_at = ts
                feature.version = 1
                conn.execute(
                    """
                    INSERT INTO features(
                        id, name, description, category, project_name, project_root,
                        skill_id, expert_team_ids, related_files, related_apis, related_tables,
                        business_rules,
                        source, ai_confidence, version, created_at, updated_at, deleted_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        feature.id,
                        feature.name,
                        feature.description,
                        feature.category,
                        feature.project_name,
                        feature.project_root,
                        feature.skill_id,
                        json.dumps(list(feature.expert_team_ids), ensure_ascii=False),
                        related_files_to_json(feature.related_files),
                        _related_apis_to_json(feature.related_apis),
                        _related_tables_to_json(feature.related_tables),
                        _business_rules_to_json(feature.business_rules),
                        feature.source,
                        feature.ai_confidence,
                        feature.version,
                        feature.created_at,
                        feature.updated_at,
                        feature.deleted_at,
                    ),
                )
            else:
                # 更新：乐观锁
                current_version = int(row["version"])
                if current_version != feature.version:
                    raise FeatureVersionConflict(
                        f"feature {feature.id} version conflict: expected={feature.version}, "
                        f"actual={current_version}"
                    )
                before_json = json.dumps(dict(row), ensure_ascii=False, default=str)
                feature.created_at = int(row["created_at"])
                feature.version = current_version + 1
                feature.updated_at = ts
                feature.deleted_at = row["deleted_at"]  # 保留软删除状态
                conn.execute(
                    """
                    UPDATE features SET
                        name=?, description=?, category=?, project_name=?, project_root=?,
                        skill_id=?, expert_team_ids=?, related_files=?, related_apis=?,
                        related_tables=?, business_rules=?,
                        source=?, ai_confidence=?, version=?, updated_at=?, deleted_at=?
                    WHERE id=? AND project_name=?
                    """,
                    (
                        feature.name,
                        feature.description,
                        feature.category,
                        feature.project_name,
                        feature.project_root,
                        feature.skill_id,
                        json.dumps(list(feature.expert_team_ids), ensure_ascii=False),
                        related_files_to_json(feature.related_files),
                        _related_apis_to_json(feature.related_apis),
                        _related_tables_to_json(feature.related_tables),
                        _business_rules_to_json(feature.business_rules),
                        feature.source,
                        feature.ai_confidence,
                        feature.version,
                        feature.updated_at,
                        feature.deleted_at,
                        feature.id,
                        feature.project_name,
                    ),
                )

            # V1.1 强制写 feature_edit_history
            after_json = json.dumps(feature.to_dict(), ensure_ascii=False, default=str)
            if before_json is None:
                # 首次插入：before 用空对象占位
                before_json = "{}"
            conn.execute(
                """
                INSERT INTO feature_edit_history(
                    feature_id, project_name, before_json, after_json, edited_at, editor_id
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    feature.id,
                    feature.project_name,
                    before_json,
                    after_json,
                    ts,
                    None,
                ),
            )

            # 同步反向索引（inline 复用外层 conn，避免 nested connection → SQLITE_BUSY）
            # 路径统一用 POSIX 正斜杠规范化，确保跨平台 JOIN 一致
            if feature.related_files:
                conn.executemany(
                    "INSERT OR IGNORE INTO feature_file_index(feature_id, file_path) VALUES (?, ?)",
                    [(feature.id, str(Path(rf.path).as_posix())) for rf in feature.related_files],
                )

    def get(self, feature_id: str, project_name: str) -> Feature | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM features WHERE id = ? AND project_name = ?",
                (feature_id, project_name),
            ).fetchone()
            if row is None:
                return None
            return self._row_to_feature(row)

    def list_by_project(self, project_name: str, include_deleted: bool = False) -> list[Feature]:
        with self._connect() as conn:
            if include_deleted:
                rows = conn.execute(
                    "SELECT * FROM features WHERE project_name = ? ORDER BY category, name",
                    (project_name,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM features WHERE project_name = ? AND deleted_at IS NULL "
                    "ORDER BY category, name",
                    (project_name,),
                ).fetchall()
            return [self._row_to_feature(r) for r in rows]

    def list_all(self, include_deleted: bool = False) -> list[Feature]:
        """跨项目列出全部功能点（前端 open 工程后无法确定 project_name 时的兜底查询）。"""
        with self._connect() as conn:
            if include_deleted:
                rows = conn.execute(
                    "SELECT * FROM features ORDER BY project_name, category, name"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM features WHERE deleted_at IS NULL "
                    "ORDER BY project_name, category, name"
                ).fetchall()
            return [self._row_to_feature(r) for r in rows]

    def list_categories(self, project_name: str) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM features WHERE project_name = ? "
                "AND deleted_at IS NULL ORDER BY category",
                (project_name,),
            ).fetchall()
            return [r["category"] for r in rows]

    def soft_delete(self, feature_id: str, project_name: str) -> None:
        ts = now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM features WHERE id = ? AND project_name = ?",
                (feature_id, project_name),
            ).fetchone()
            if row is None:
                return
            before_json = json.dumps(dict(row), ensure_ascii=False, default=str)
            conn.execute(
                "UPDATE features SET deleted_at = ?, updated_at = ? WHERE id = ? AND project_name = ?",
                (ts, ts, feature_id, project_name),
            )
            # 写 edit_history
            after_json = json.dumps(
                {**dict(row), "deleted_at": ts}, ensure_ascii=False, default=str
            )
            conn.execute(
                "INSERT INTO feature_edit_history(feature_id, project_name, before_json, after_json, edited_at, editor_id) "
                "VALUES (?,?,?,?,?,?)",
                (feature_id, project_name, before_json, after_json, ts, None),
            )

    def delete(self, feature_id: str, project_name: str) -> None:
        """硬删除：features 行 + feature_file_index 级联 + 写 edit_history。"""
        ts = now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM features WHERE id = ? AND project_name = ?",
                (feature_id, project_name),
            ).fetchone()
            if row is None:
                return
            before_json = json.dumps(dict(row), ensure_ascii=False, default=str)
            # 先写 edit_history（row 删前要拿到 before）
            conn.execute(
                "INSERT INTO feature_edit_history(feature_id, project_name, before_json, after_json, edited_at, editor_id) "
                "VALUES (?,?,?,?,?,?)",
                (feature_id, project_name, before_json, "{}", ts, None),
            )
            # PRAGMA foreign_keys = ON → 自动清 feature_file_index
            conn.execute(
                "DELETE FROM features WHERE id = ? AND project_name = ?",
                (feature_id, project_name),
            )

    # ---- 反向索引 -------------------------------------------------------

    def add_file_index(self, feature_id: str, file_paths: list[str]) -> None:
        if not file_paths:
            return
        with self._connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO feature_file_index(feature_id, file_path) VALUES (?, ?)",
                [(feature_id, str(Path(p).as_posix())) for p in file_paths],
            )

    def remove_file_index(self, feature_id: str, file_paths: list[str] | None = None) -> None:
        with self._connect() as conn:
            if file_paths is None:
                conn.execute(
                    "DELETE FROM feature_file_index WHERE feature_id = ?",
                    (feature_id,),
                )
            else:
                conn.executemany(
                    "DELETE FROM feature_file_index WHERE feature_id = ? AND file_path = ?",
                    [(feature_id, p) for p in file_paths],
                )

    def rebuild_file_index(self, feature_id: str, file_paths: list[str]) -> None:
        """DELETE 全部 + INSERT ALL（保持 ON CONFLICT 防御）。"""
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM feature_file_index WHERE feature_id = ?",
                (feature_id,),
            )
            if file_paths:
                conn.executemany(
                    "INSERT OR IGNORE INTO feature_file_index(feature_id, file_path) VALUES (?, ?)",
                    [(feature_id, str(Path(p).as_posix())) for p in file_paths],
                )

    def find_features_by_file(self, file_path: str, project_name: str) -> list[Feature]:
        """JOIN 反向索引 + 软删除过滤。"""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT f.* FROM features f
                JOIN feature_file_index idx ON idx.feature_id = f.id
                WHERE idx.file_path = ? AND f.project_name = ? AND f.deleted_at IS NULL
                ORDER BY f.category, f.name
                """,
                (file_path, project_name),
            ).fetchall()
            return [self._row_to_feature(r) for r in rows]

    # ---- 项目级 ----------------------------------------------------------

    def list_projects(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT project_name FROM features ORDER BY project_name"
            ).fetchall()
            return [r["project_name"] for r in rows]

    def delete_project(self, project_name: str) -> None:
        """硬删整个项目：features + feature_file_index + 写 edit_history。"""
        ts = now()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM features WHERE project_name = ?",
                (project_name,),
            ).fetchall()
            for r in rows:
                before_json = json.dumps(dict(r), ensure_ascii=False, default=str)
                conn.execute(
                    "INSERT INTO feature_edit_history(feature_id, project_name, before_json, after_json, edited_at, editor_id) "
                    "VALUES (?,?,?,?,?,?)",
                    (r["id"], project_name, before_json, "{}", ts, None),
                )
            # 先写 history 再删（按 id 级联清 file_index）
            conn.execute(
                "DELETE FROM features WHERE project_name = ?",
                (project_name,),
            )

    # ---- extraction jobs （最小 CRUD）-----------------------------------

    def create_job(self, project_name: str, project_root: str) -> int:
        ts = now()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO extraction_jobs(project_name, project_root, status, started_at)
                VALUES (?, ?, 'pending', ?)
                """,
                (project_name, project_root, ts),
            )
            return int(cur.lastrowid)

    def update_job(
        self,
        job_id: int,
        *,
        status: str | None = None,
        total_files: int | None = None,
        processed_files: int | None = None,
        features_generated: int | None = None,
        error_message: str | None = None,
        finished: bool = False,
    ) -> None:
        sets: list[str] = []
        params: list = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if total_files is not None:
            sets.append("total_files = ?")
            params.append(total_files)
        if processed_files is not None:
            sets.append("processed_files = ?")
            params.append(processed_files)
        if features_generated is not None:
            sets.append("features_generated = ?")
            params.append(features_generated)
        if error_message is not None:
            sets.append("error_message = ?")
            params.append(error_message)
        if finished:
            sets.append("finished_at = ?")
            params.append(now())
        if not sets:
            return
        params.append(job_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE extraction_jobs SET {', '.join(sets)} WHERE id = ?",
                params,
            )

    def get_job(self, job_id: int) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM extraction_jobs WHERE id = ?",
                (job_id,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    def latest_job(self, project_name: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM extraction_jobs WHERE project_name = ? ORDER BY id DESC LIMIT 1",
                (project_name,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    # ---- project profiles（init 风格项目画像，2026-08-05）---------------

    def upsert_profile(self, project_name: str, project_root: str, profile_text: str) -> None:
        """写入/更新项目画像（按 project_name 覆盖）。"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO project_profiles(project_name, project_root, profile_text, updated_at)
                VALUES (?,?,?,?)
                ON CONFLICT(project_name) DO UPDATE SET
                    project_root = excluded.project_root,
                    profile_text = excluded.profile_text,
                    updated_at   = excluded.updated_at
                """,
                (project_name, project_root, profile_text, now()),
            )

    def get_profile(self, project_name: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM project_profiles WHERE project_name = ?",
                (project_name,),
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    # ---- private ---------------------------------------------------------

    def _row_to_feature(self, row: sqlite3.Row) -> Feature:
        return Feature(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            category=row["category"],
            project_name=row["project_name"],
            project_root=row["project_root"],
            skill_id=row["skill_id"],
            expert_team_ids=_expert_team_ids_from_json(row["expert_team_ids"]),
            related_files=related_files_from_json(row["related_files"]),
            related_apis=_related_apis_from_json(row["related_apis"]),
            related_tables=_related_tables_from_json(row["related_tables"]),
            business_rules=_business_rules_from_json(row["business_rules"]),
            source=row["source"],
            ai_confidence=row["ai_confidence"],
            version=int(row["version"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            deleted_at=row["deleted_at"],
        )
