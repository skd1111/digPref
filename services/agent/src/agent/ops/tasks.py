"""ops.tasks —— 业务办理台账（业务汇总，2026-11-09）。

台账 ≠ 业务记录卡片（business_records）：
  - business_records：业务**办完之后** AI 生成的小结（导出时插入一条，不可回溯过程）；
  - business_tasks：业务**发起即建**、状态随办理流转的台账（一条业务一行）。
两者用 case_id 关联：台账「结束」时可回链到那张小结卡片。

四态：
  in_progress   正在办理 —— 上传第一份材料时建档（默认态）
  suspended     挂起     —— 客户经理人工标记（界面只读冻结，恢复后接着办）
  manual_review 人工确认 —— 审核异常（打回 / 超时 / 模型不可用）自动转入，待人工接入
  closed        结束     —— 全部验收通过并导出交付物后自动转入
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 台账状态（四态）
TASK_IN_PROGRESS = "in_progress"
TASK_SUSPENDED = "suspended"
TASK_MANUAL_REVIEW = "manual_review"
TASK_CLOSED = "closed"

ALL_TASK_STATUSES = (TASK_IN_PROGRESS, TASK_SUSPENDED, TASK_MANUAL_REVIEW, TASK_CLOSED)

TASK_STATUS_LABEL: dict[str, str] = {
    TASK_IN_PROGRESS: "正在办理",
    TASK_SUSPENDED: "挂起",
    TASK_MANUAL_REVIEW: "人工确认",
    TASK_CLOSED: "结束",
}

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now() -> int:
    return int(time.time())


def _read_schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


@dataclass
class BusinessTask:
    """一条业务办理台账（一笔业务一行）。"""

    id: str
    case_id: str = ""
    project_name: str = ""
    feature_id: str = ""
    feature_label: str = ""
    category: str = ""
    category_label: str = ""
    title: str = ""
    status: str = TASK_IN_PROGRESS
    status_note: str = ""
    pending_items: list[dict[str, Any]] = field(default_factory=list)
    operator: str = ""
    record_id: str = ""
    created_at: int = 0
    updated_at: int = 0
    suspended_at: int = 0
    closed_at: int = 0

    @property
    def status_label(self) -> str:
        return TASK_STATUS_LABEL.get(self.status, self.status)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "case_id": self.case_id,
            "project_name": self.project_name,
            "feature_id": self.feature_id,
            "feature_label": self.feature_label,
            "category": self.category,
            "category_label": self.category_label,
            "title": self.title,
            "status": self.status,
            "status_label": self.status_label,
            "status_note": self.status_note,
            "pending_items": list(self.pending_items),
            "operator": self.operator,
            "record_id": self.record_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "suspended_at": self.suspended_at,
            "closed_at": self.closed_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> BusinessTask:
        items = d.get("pending_items")
        if not isinstance(items, list):
            items = []
        return cls(
            id=str(d.get("id", "")),
            case_id=str(d.get("case_id", "")),
            project_name=str(d.get("project_name", "")),
            feature_id=str(d.get("feature_id", "")),
            feature_label=str(d.get("feature_label", "")),
            category=str(d.get("category", "")),
            category_label=str(d.get("category_label", "")),
            title=str(d.get("title", "")),
            status=str(d.get("status", TASK_IN_PROGRESS)),
            status_note=str(d.get("status_note", "")),
            pending_items=[x for x in items if isinstance(x, dict)],
            operator=str(d.get("operator", "")),
            record_id=str(d.get("record_id", "")),
            created_at=int(d.get("created_at", 0)),
            updated_at=int(d.get("updated_at", 0)),
            suspended_at=int(d.get("suspended_at", 0)),
            closed_at=int(d.get("closed_at", 0)),
        )


class TaskStorage:
    """业务台账存储（与 business_records / case_files 共用同一个 ops sqlite 文件）。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_read_schema_sql())

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    # ---- 建档 --------------------------------------------------------------

    def ensure(
        self,
        *,
        case_id: str,
        project_name: str = "",
        feature_id: str = "",
        feature_label: str = "",
        category: str = "",
        category_label: str = "",
        title: str = "",
        operator: str = "",
    ) -> BusinessTask:
        """按 case_id 建档（幂等）：已存在则只补齐缺失的业务分类标签。"""
        existing = self.get_by_case(case_id)
        if existing is not None:
            return self._fill_labels(
                existing,
                project_name=project_name,
                feature_id=feature_id,
                feature_label=feature_label,
                category=category,
                category_label=category_label,
                title=title,
                operator=operator,
            )
        ts = now()
        task = BusinessTask(
            id=f"BT-{time.strftime('%Y%m%d%H%M%S')}-{secrets.token_hex(2)}",
            case_id=case_id,
            project_name=project_name,
            feature_id=feature_id,
            feature_label=feature_label,
            category=category,
            category_label=category_label,
            title=title or feature_label,
            status=TASK_IN_PROGRESS,
            operator=operator,
            created_at=ts,
            updated_at=ts,
        )
        with self._connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO business_tasks(
                        id, case_id, project_name, feature_id, feature_label,
                        category, category_label, title, status, status_note, pending_items,
                        operator, record_id, created_at, updated_at, suspended_at, closed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '[]', ?, '', ?, ?, 0, 0)
                    """,
                    (
                        task.id,
                        task.case_id,
                        task.project_name,
                        task.feature_id,
                        task.feature_label,
                        task.category,
                        task.category_label,
                        task.title,
                        task.status,
                        task.operator,
                        ts,
                        ts,
                    ),
                )
            except sqlite3.IntegrityError:
                # case_id 唯一索引：并发首建时另一条已入库 → 取那条
                existing = self.get_by_case(case_id)
                if existing is not None:
                    return existing
                raise
        return task

    def _fill_labels(
        self,
        task: BusinessTask,
        *,
        project_name: str,
        feature_id: str,
        feature_label: str,
        category: str,
        category_label: str,
        title: str,
        operator: str,
    ) -> BusinessTask:
        updates: dict[str, str] = {}
        if project_name and not task.project_name:
            updates["project_name"] = project_name
        if feature_id and not task.feature_id:
            updates["feature_id"] = feature_id
        if feature_label and not task.feature_label:
            updates["feature_label"] = feature_label
        if category and not task.category:
            updates["category"] = category
        if category_label and not task.category_label:
            updates["category_label"] = category_label
        if title and not task.title:
            updates["title"] = title
        if operator and not task.operator:
            updates["operator"] = operator
        if not updates:
            return task
        sets = ", ".join(f"{k} = ?" for k in updates)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE business_tasks SET {sets}, updated_at = ? WHERE id = ?",
                (*updates.values(), now(), task.id),
            )
        refreshed = self.get(task.id)
        return refreshed if refreshed is not None else task

    # ---- 读 ---------------------------------------------------------------

    def get(self, task_id: str) -> BusinessTask | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM business_tasks WHERE id = ?", (task_id,)).fetchone()
        return self._row_to_task(row) if row else None

    def get_by_case(self, case_id: str) -> BusinessTask | None:
        if not case_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM business_tasks WHERE case_id = ? ORDER BY created_at DESC LIMIT 1",
                (case_id,),
            ).fetchone()
        return self._row_to_task(row) if row else None

    def list_tasks(
        self,
        *,
        statuses: list[str] | None = None,
        category: str | None = None,
        feature_id: str | None = None,
        project_name: str | None = None,
        case_id: str | None = None,
        date_from: int | None = None,
        date_to: int | None = None,
        keyword: str | None = None,
        limit: int = 200,
    ) -> list[BusinessTask]:
        sql, params = self._build_filter(
            statuses=statuses,
            category=category,
            feature_id=feature_id,
            project_name=project_name,
            case_id=case_id,
            date_from=date_from,
            date_to=date_to,
            keyword=keyword,
        )
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_task(r) for r in rows]

    def counts(
        self,
        *,
        statuses: list[str] | None = None,
        category: str | None = None,
        feature_id: str | None = None,
        project_name: str | None = None,
        case_id: str | None = None,
        date_from: int | None = None,
        date_to: int | None = None,
        keyword: str | None = None,
    ) -> dict[str, int]:
        """按状态计数（同一组筛选条件下）——查询页顶部状态条用。"""
        sql, params = self._build_filter(
            statuses=None,
            category=category,
            feature_id=feature_id,
            project_name=project_name,
            case_id=case_id,
            date_from=date_from,
            date_to=date_to,
            keyword=keyword,
        )
        sql = sql.replace(
            "SELECT * FROM business_tasks",
            "SELECT status, COUNT(*) AS n FROM business_tasks",
        )
        sql += " GROUP BY status"
        out = dict.fromkeys(ALL_TASK_STATUSES, 0)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        for r in rows:
            out[str(r["status"])] = int(r["n"])
        return out

    def _build_filter(
        self,
        *,
        statuses: list[str] | None,
        category: str | None,
        feature_id: str | None,
        project_name: str | None,
        case_id: str | None,
        date_from: int | None,
        date_to: int | None,
        keyword: str | None,
    ) -> tuple[str, list[Any]]:
        sql = "SELECT * FROM business_tasks WHERE 1=1"
        params: list[Any] = []
        if statuses:
            valid = [s for s in statuses if s in ALL_TASK_STATUSES]
            if valid:
                sql += " AND status IN (" + ",".join("?" for _ in valid) + ")"
                params.extend(valid)
        if category:
            sql += " AND category = ?"
            params.append(category)
        if feature_id:
            sql += " AND feature_id = ?"
            params.append(feature_id)
        if project_name:
            sql += " AND project_name = ?"
            params.append(project_name)
        if case_id:
            sql += " AND case_id = ?"
            params.append(case_id)
        if date_from:
            sql += " AND created_at >= ?"
            params.append(int(date_from))
        if date_to:
            sql += " AND created_at <= ?"
            params.append(int(date_to))
        if keyword:
            like = f"%{keyword.strip()}%"
            sql += " AND (title LIKE ? OR feature_label LIKE ? OR category_label LIKE ?)"
            params.extend([like, like, like])
        return sql, params

    # ---- 写 ---------------------------------------------------------------

    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        status_note: str | None = None,
        pending_items: list[dict[str, Any]] | None = None,
        record_id: str | None = None,
        clear_closed: bool = False,
        clear_suspended: bool = False,
    ) -> BusinessTask | None:
        sets: list[str] = []
        params: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if status_note is not None:
            sets.append("status_note = ?")
            params.append(status_note)
        if pending_items is not None:
            sets.append("pending_items = ?")
            params.append(json.dumps(pending_items, ensure_ascii=False))
        if record_id is not None:
            sets.append("record_id = ?")
            params.append(record_id)
        if clear_closed:
            sets.append("closed_at = 0")
        if clear_suspended:
            sets.append("suspended_at = 0")
        if not sets:
            return self.get(task_id)
        ts = now()
        sets.append("updated_at = ?")
        params.append(ts)
        params.append(task_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE business_tasks SET {', '.join(sets)} WHERE id = ?", params)
        return self.get(task_id)

    def set_suspended(self, task_id: str, *, note: str = "") -> BusinessTask | None:
        """人工挂起：界面冻结，状态不再被系统自动改写。"""
        ts = now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE business_tasks SET status = ?, status_note = ?, suspended_at = ?, "
                "updated_at = ? WHERE id = ?",
                (TASK_SUSPENDED, note, ts, ts, task_id),
            )
        return self.get(task_id)

    def set_closed(
        self, task_id: str, *, note: str = "", record_id: str = ""
    ) -> BusinessTask | None:
        ts = now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE business_tasks SET status = ?, status_note = ?, closed_at = ?, "
                "record_id = CASE WHEN ? != '' THEN ? ELSE record_id END, updated_at = ? "
                "WHERE id = ?",
                (TASK_CLOSED, note, ts, record_id, record_id, ts, task_id),
            )
        return self.get(task_id)

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> BusinessTask:
        d = dict(row)
        try:
            d["pending_items"] = json.loads(d.get("pending_items") or "[]")
        except (ValueError, TypeError):
            d["pending_items"] = []
        return BusinessTask.from_dict(d)
