"""dict.storage —— 数据字典 SQLite 存储（Phase 2H）。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from .models import DictItem

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def now() -> int:
    return int(time.time())


def _read_schema_sql() -> str:
    return _SCHEMA_PATH.read_text(encoding="utf-8")


# 首次建库时的种子公共参数（银行运营高频公共参数示例，可被 Skill 通过 key 引用）
SEED_ITEMS: list[dict] = [
    {
        "key": "authorization_valid_days",
        "category": "授权",
        "label": "授权有效期（天）",
        "value": "7",
        "description": "业务授权/授权书默认有效天数；超过需重新授权。",
    },
    {
        "key": "large_cash_threshold",
        "category": "现金",
        "label": "大额现金支取起点（元）",
        "value": "50000",
        "description": "单笔大额现金支取审核起点，超过需主管授权。",
    },
    {
        "key": "bo_identification_threshold",
        "category": "对公",
        "label": "受益所有人识别持股阈值",
        "value": "25%",
        "description": "直接或间接持股/表决权达到该比例需识别为受益所有人。",
    },
    {
        "key": "account_opening_docs",
        "category": "对公",
        "label": "对公开户必要资料",
        "value": "营业执照、法人身份证、经办人身份证、授权书、章程、受益所有人信息",
        "description": "对公开户资料清单（公共部分），具体按业务类型在功能点 Skill 中细化。",
    },
    {
        "key": "id_card_expiry_remind_days",
        "category": "客户",
        "label": "证件到期提醒天数",
        "value": "30",
        "description": "证件有效期剩余天数低于该值即触发到期提醒。",
    },
    {
        "key": "aml_suspicious_report_route",
        "category": "反洗钱",
        "label": "可疑交易上报路径",
        "value": "合规部 → 反洗钱监测系统",
        "description": "AI 仅辅助分析，最终上报由人工审核后走该路径。",
    },
    {
        "key": "complaint_response_days",
        "category": "客服",
        "label": "投诉答复时限（工作日）",
        "value": "5",
        "description": "投诉处理完成的时限（工作日），超期需升级。",
    },
]


class DictStorage:
    """数据字典存储（单文件 sqlite）。"""

    def __init__(self, db_path: str):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_read_schema_sql())
        self._seed_if_empty()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _seed_if_empty(self) -> None:
        ts = now()
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM dict_items").fetchone()
            if int(row["n"]) > 0:
                return
            for item in SEED_ITEMS:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO dict_items(
                        key, category, label, value, description, source,
                        updated_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'seed', '', ?, ?)
                    """,
                    (
                        item["key"],
                        item["category"],
                        item["label"],
                        item["value"],
                        item["description"],
                        ts,
                        ts,
                    ),
                )

    def upsert(self, item: DictItem, *, replace_seed: bool = False) -> DictItem:
        """新建/更新。seed 条目默认只读（除非 replace_seed=True，UI 显式编辑时用）。"""
        existing = self.get(item.key)
        ts = now()
        source = item.source
        if existing is not None:
            if existing.source == "seed" and not replace_seed:
                raise ValueError(
                    f"dictionary key {item.key} is a seed entry; edit it explicitly to override"
                )
            source = "manual"
        item.source = source
        item.updated_at = ts
        if existing is None:
            item.created_at = ts
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO dict_items(
                        key, category, label, value, description, source,
                        updated_by, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.key,
                        item.category,
                        item.label,
                        item.value,
                        item.description,
                        item.source,
                        item.updated_by,
                        item.created_at,
                        item.updated_at,
                    ),
                )
        else:
            item.created_at = existing.created_at
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE dict_items SET
                        category = ?, label = ?, value = ?, description = ?,
                        source = ?, updated_by = ?, updated_at = ?
                    WHERE key = ?
                    """,
                    (
                        item.category,
                        item.label,
                        item.value,
                        item.description,
                        item.source,
                        item.updated_by,
                        item.updated_at,
                        item.key,
                    ),
                )
        return item

    def get(self, key: str) -> DictItem | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM dict_items WHERE key = ?", (key,)).fetchone()
        return DictItem.from_dict(dict(row)) if row else None

    def list(self, category: str | None = None) -> list[DictItem]:
        sql = "SELECT * FROM dict_items WHERE 1=1"
        params: list = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        sql += " ORDER BY category, key"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [DictItem.from_dict(dict(r)) for r in rows]

    def search(self, query: str, limit: int = 50) -> list[DictItem]:
        """LIKE 模糊搜索：key / label / value / description / category。"""
        q = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM dict_items
                WHERE key LIKE ? OR label LIKE ? OR value LIKE ?
                      OR description LIKE ? OR category LIKE ?
                ORDER BY category, key LIMIT ?
                """,
                (q, q, q, q, q, max(1, min(int(limit), 200))),
            ).fetchall()
        return [DictItem.from_dict(dict(r)) for r in rows]

    def categories(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT category FROM dict_items ORDER BY category"
            ).fetchall()
        return [str(r["category"]) for r in rows]

    def delete(self, key: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM dict_items WHERE key = ?", (key,))
        return cur.rowcount > 0
