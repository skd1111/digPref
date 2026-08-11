"""SQLite 符号查询接口。

Phase 2F V0 实现：
- search(): 模糊匹配符号名（LIKE %name%），可按 kind 过滤
- get_file_symbols(): 列出文件的所有符号
- get_status(): 当前索引状态
- delete_file_symbols(): 删除文件的全部符号（文件被删时）
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from agent.codenav.indexer import _read_schema_sql  # 复用 schema 初始化
from agent.codenav.models import IndexStatus, Symbol


class SymbolQuery:
    def __init__(self, db_path: str):
        self._db_path = db_path
        # 确保表存在（不重建数据）
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            conn.executescript(_read_schema_sql())

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def search(self, name: str, kind: str | None = None, limit: int = 10) -> list[Symbol]:
        """按名称模糊匹配（LIKE %name%），可按 kind 过滤。

        空 name → 返回所有符号（受 limit 限制）。
        """
        if not name:
            sql = "SELECT name, kind, file_path, start_line, end_line, signature, parent_class, language FROM symbols"
            params: list = []
            if kind:
                sql += " WHERE kind = ?"
                params.append(kind)
            sql += " ORDER BY name ASC LIMIT ?"
            params.append(int(limit))
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            return [
                Symbol(
                    name=r[0],
                    kind=r[1],
                    file_path=r[2],
                    start_line=r[3],
                    end_line=r[4],
                    signature=r[5],
                    parent_class=r[6],
                    language=r[7],
                )
                for r in rows
            ]
        sql = "SELECT name, kind, file_path, start_line, end_line, signature, parent_class, language FROM symbols WHERE name LIKE ?"
        params: list = [f"%{name}%"]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY name ASC LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            Symbol(
                name=r[0],
                kind=r[1],
                file_path=r[2],
                start_line=r[3],
                end_line=r[4],
                signature=r[5],
                parent_class=r[6],
                language=r[7],
            )
            for r in rows
        ]

    def get_file_symbols(self, file_path: str) -> list[Symbol]:
        """列出文件中所有符号（按 start_line 排序）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, kind, file_path, start_line, end_line, signature, parent_class, language "
                "FROM symbols WHERE file_path = ? ORDER BY start_line ASC",
                (file_path,),
            ).fetchall()
        return [
            Symbol(
                name=r[0],
                kind=r[1],
                file_path=r[2],
                start_line=r[3],
                end_line=r[4],
                signature=r[5],
                parent_class=r[6],
                language=r[7],
            )
            for r in rows
        ]

    def get_status(self) -> IndexStatus:
        """返回索引状态统计。"""
        with self._connect() as conn:
            total_files = conn.execute("SELECT COUNT(DISTINCT file_path) FROM symbols").fetchone()[
                0
            ]
            total_symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        return IndexStatus(
            total_files=total_files,
            total_symbols=total_symbols,
            last_full_scan=None,
            last_incremental=None,
            is_scanning=False,
        )

    def delete_file_symbols(self, file_path: str) -> None:
        """删除指定文件的所有符号。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))

    def count_by_kind(self) -> dict[str, int]:
        """按 kind 统计（用于状态页）。"""
        with self._connect() as conn:
            rows = conn.execute("SELECT kind, COUNT(*) FROM symbols GROUP BY kind").fetchall()
        return {r[0]: r[1] for r in rows}
