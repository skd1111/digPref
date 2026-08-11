"""reqflow.storage —— reqcards.db 存储层（批次/卡片 CRUD + 自动编号 + 版本快照）。

形态对齐 biznav/storage.py：sync sqlite3 + row_factory + 首次访问建表。
版本规则：卡片创建 = v1；每次 update_card 先把旧版整卡快照写入
req_card_versions，再 version+1 —— 列表/详情永远返回最新版。
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import (
    ALL_STATUSES,
    APPROVED,
    DRAFT,
    REJECTED,
    ReqBatch,
    ReqCard,
    can_transition,
)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# update_card 允许修改的字段（status 单独走流转校验）
_EDITABLE_FIELDS = (
    "title",
    "system_name",
    "business_value",
    "change_points",
    "feasibility",
    "feasibility_notes",
    "impact",
    "priority",
    "conversation_summary",
)
_EDITABLE_JSON_FIELDS = ("feature_ids", "external_systems")


def now() -> int:
    return int(time.time())


class ReqCardStorage:
    """需求批次 / 卡片存储（单文件 sqlite）。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialized = False

    # ---- 连接 ---------------------------------------------------------------

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            if not self._initialized:
                conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
                self._initialized = True
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- 编号 ---------------------------------------------------------------

    def _next_seq(self, prefix: str) -> str:
        """当日三位递增序号：{PREFIX}-{YYYYMMDD}-{NNN}。"""
        date_str = time.strftime("%Y%m%d")
        id_prefix = f"{prefix}-{date_str}-"
        table = "req_batches" if prefix == "BAT" else "req_cards"
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT id FROM {table} WHERE id LIKE ?", (f"{id_prefix}%",)
            ).fetchall()
        max_seq = 0
        for r in rows:
            try:
                max_seq = max(max_seq, int(str(r["id"]).rsplit("-", 1)[1]))
            except (ValueError, IndexError):
                continue
        return f"{id_prefix}{max_seq + 1:03d}"

    # ---- 批次 ---------------------------------------------------------------

    def create_batch(self, project_name: str, name: str = "", created_by: str = "") -> ReqBatch:
        ts = now()
        batch = ReqBatch(
            id=self._next_seq("BAT"),
            name=name or time.strftime("%Y-%m-%d 批次"),
            project_name=project_name,
            status="open",
            created_by=created_by,
            created_at=ts,
            updated_at=ts,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO req_batches(id, name, project_name, status, created_by, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    batch.id,
                    batch.name,
                    batch.project_name,
                    batch.status,
                    batch.created_by,
                    batch.created_at,
                    batch.updated_at,
                ),
            )
        return batch

    def list_batches(self, project_name: str | None = None) -> list[ReqBatch]:
        with self._connect() as conn:
            if project_name:
                rows = conn.execute(
                    "SELECT * FROM req_batches WHERE project_name = ? ORDER BY created_at DESC",
                    (project_name,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM req_batches ORDER BY created_at DESC").fetchall()
        return [ReqBatch.from_dict(dict(r)) for r in rows]

    def get_batch(self, batch_id: str) -> ReqBatch | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM req_batches WHERE id = ?", (batch_id,)).fetchone()
        return ReqBatch.from_dict(dict(row)) if row else None

    def batch_stats(self, batch_id: str) -> dict[str, int]:
        """批次完成情况统计：{total, 每个状态计数}。"""
        stats: dict[str, int] = {"total": 0}
        for s in ALL_STATUSES:
            stats[s] = 0
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM req_cards WHERE batch_id = ? GROUP BY status",
                (batch_id,),
            ).fetchall()
        for r in rows:
            stats[str(r["status"])] = int(r["n"])
            stats["total"] += int(r["n"])
        return stats

    # ---- 卡片 ---------------------------------------------------------------

    def create_card(
        self,
        *,
        batch_id: str,
        project_name: str,
        system_name: str,
        title: str,
        feature_ids: list[str] | None = None,
        business_value: str = "",
        change_points: str = "",
        feasibility: str = "",
        feasibility_notes: str = "",
        impact: str = "",
        external_systems: list[str] | None = None,
        priority: str = "P2",
        conversation_summary: str = "",
        session_id: str = "",
        created_by: str = "",
    ) -> ReqCard:
        ts = now()
        card = ReqCard(
            id=self._next_seq("REQ"),
            batch_id=batch_id,
            project_name=project_name,
            system_name=system_name,
            title=title,
            feature_ids=list(feature_ids or []),
            business_value=business_value,
            change_points=change_points,
            feasibility=feasibility,
            feasibility_notes=feasibility_notes,
            impact=impact,
            external_systems=list(external_systems or []),
            priority=priority,
            status=DRAFT,
            conversation_summary=conversation_summary,
            session_id=session_id,
            version=1,
            created_by=created_by,
            created_at=ts,
            updated_at=ts,
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO req_cards(
                    id, batch_id, project_name, system_name, title, feature_ids,
                    business_value, change_points, feasibility, feasibility_notes,
                    impact, external_systems, priority, status,
                    conversation_summary, session_id, approved_by, approved_at,
                    version, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    card.id,
                    card.batch_id,
                    card.project_name,
                    card.system_name,
                    card.title,
                    json.dumps(card.feature_ids, ensure_ascii=False),
                    card.business_value,
                    card.change_points,
                    card.feasibility,
                    card.feasibility_notes,
                    card.impact,
                    json.dumps(card.external_systems, ensure_ascii=False),
                    card.priority,
                    card.status,
                    card.conversation_summary,
                    card.session_id,
                    card.approved_by,
                    card.approved_at,
                    card.version,
                    card.created_by,
                    card.created_at,
                    card.updated_at,
                ),
            )
        return card

    def get_card(self, card_id: str) -> ReqCard | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM req_cards WHERE id = ?", (card_id,)).fetchone()
        return self._row_to_card(row) if row else None

    def list_cards(
        self,
        batch_id: str | None = None,
        status: str | None = None,
        feature_id: str | None = None,
        project_name: str | None = None,
    ) -> list[ReqCard]:
        sql = "SELECT * FROM req_cards WHERE 1=1"
        params: list[Any] = []
        if batch_id:
            sql += " AND batch_id = ?"
            params.append(batch_id)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if project_name:
            sql += " AND project_name = ?"
            params.append(project_name)
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        cards = [self._row_to_card(r) for r in rows]
        if feature_id:
            cards = [c for c in cards if feature_id in c.feature_ids]
        return cards

    def update_card(self, card_id: str, changed_by: str = "", **fields: Any) -> ReqCard:
        """改字段 / 切状态。

        - status 变更走 can_transition 校验，非法抛 ValueError
        - 修改前旧版整卡快照写入 req_card_versions，version +1
        - 无可改字段 → 原样返回，不记版本
        """
        current = self.get_card(card_id)
        if current is None:
            raise KeyError(f"card {card_id} not found")

        edits: dict[str, Any] = {}
        for k in _EDITABLE_FIELDS:
            if k in fields and fields[k] is not None:
                edits[k] = str(fields[k])
        for k in _EDITABLE_JSON_FIELDS:
            if k in fields and fields[k] is not None:
                edits[k] = [str(x) for x in fields[k]]

        new_status = fields.get("status")
        if new_status is not None and new_status != current.status:
            if not can_transition(current.status, str(new_status)):
                raise ValueError(f"illegal status transition: {current.status} -> {new_status}")
            edits["status"] = str(new_status)

        if not edits:
            return current

        ts = now()
        old_snapshot = current.to_dict()
        approved_by = current.approved_by
        approved_at = current.approved_at
        # 审批预留：切到 approved/rejected 时记录（V1 用操作人）
        if edits.get("status") in (APPROVED, REJECTED):
            approved_by = changed_by or current.created_by
            approved_at = ts

        with self._connect() as conn:
            # 1. 旧版快照存档
            conn.execute(
                "INSERT INTO req_card_versions(card_id, version, snapshot, "
                "changed_by, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    card_id,
                    current.version,
                    json.dumps(old_snapshot, ensure_ascii=False),
                    changed_by,
                    ts,
                ),
            )
            # 2. 应用修改 + version+1
            sets: list[str] = []
            params: list[Any] = []
            for k, v in edits.items():
                if k in _EDITABLE_JSON_FIELDS:
                    sets.append(f"{k} = ?")
                    params.append(json.dumps(v, ensure_ascii=False))
                else:
                    sets.append(f"{k} = ?")
                    params.append(v)
            sets.append("approved_by = ?")
            params.append(approved_by)
            sets.append("approved_at = ?")
            params.append(approved_at)
            sets.append("version = ?")
            params.append(current.version + 1)
            sets.append("updated_at = ?")
            params.append(ts)
            params.append(card_id)
            conn.execute(f"UPDATE req_cards SET {', '.join(sets)} WHERE id = ?", params)

        updated = self.get_card(card_id)
        assert updated is not None
        return updated

    def delete_card(self, card_id: str) -> None:
        """仅 draft 可删（硬删 + 清历史版本）；已流转的保留。"""
        current = self.get_card(card_id)
        if current is None:
            return
        if current.status != DRAFT:
            raise ValueError(f"only draft cards can be deleted (status={current.status})")
        with self._connect() as conn:
            conn.execute("DELETE FROM req_card_versions WHERE card_id = ?", (card_id,))
            conn.execute("DELETE FROM req_cards WHERE id = ?", (card_id,))

    # ---- 历史版本 ------------------------------------------------------------

    def list_versions(self, card_id: str) -> list[dict[str, Any]]:
        """倒序返回 [{version, changed_by, created_at}]（不含快照全文）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT version, changed_by, created_at FROM req_card_versions "
                "WHERE card_id = ? ORDER BY version DESC",
                (card_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_version(self, card_id: str, version: int) -> dict[str, Any]:
        """指定版本快照（只读）；不存在抛 KeyError。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT snapshot FROM req_card_versions WHERE card_id = ? AND version = ?",
                (card_id, version),
            ).fetchone()
        if row is None:
            raise KeyError(f"version {version} of card {card_id} not found")
        return json.loads(str(row["snapshot"]))

    # ---- private -------------------------------------------------------------

    @staticmethod
    def _row_to_card(row: sqlite3.Row) -> ReqCard:
        d = dict(row)
        d["feature_ids"] = json.loads(d.get("feature_ids") or "[]")
        d["external_systems"] = json.loads(d.get("external_systems") or "[]")
        return ReqCard.from_dict(d)
