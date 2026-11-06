"""knowledge.storage —— 本地知识库混合检索 SQLite DAO。

单库（kb.db）内并存三层索引，全部走 SQL 引擎、单文件复制即迁移：
    - FTS5 虚表 chunks_fts：jieba 分词后的 token 串，原生 bm25() 排序（稀疏通道）
    - sqlite-vec 虚表 kb_chunks_vec：子块向量 float32 BLOB，余弦扫描（稠密通道）
    - parents 表：父块原文（small-to-big：命中子块回喂父块给 LLM）

红线：
    - 敏感素材只在内网本地库，向量/FTS 同库同机，不出云；
    - sqlite-vec 不可用 → 向量通道返空，BM25/文本通道仍可用（best-effort 退化）；
    - 库内零绝对路径：上传文件只存相对 files/ 的 source_relpath。

兼容：保留既有 upsert_chunks / get_chunk / search_by_vector / search_by_text 语义
（KnowledgeChunk 无 index_text/parent 时按 content 建 FTS、无父块）。
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

from agent import vector_store as vs
from agent.knowledge import tokenizer as tk

logger = logging.getLogger(__name__)

_CHUNK_VEC_TABLE = "kb_chunks_vec"
_FTS_TABLE = "chunks_fts"
_DOCS_FTS_TABLE = "docs_fts"
_ENT_FTS_TABLE = "entities_fts"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    doc_id         TEXT PRIMARY KEY,
    title          TEXT NOT NULL DEFAULT '',
    file_name      TEXT NOT NULL DEFAULT '',
    source_type    TEXT NOT NULL DEFAULT 'markdown',
    source_relpath TEXT,
    category       TEXT NOT NULL DEFAULT '',
    size_bytes     INTEGER NOT NULL DEFAULT 0,
    status         TEXT NOT NULL DEFAULT 'pending',
    chunk_count    INTEGER NOT NULL DEFAULT 0,
    error          TEXT,
    metadata_json  TEXT NOT NULL DEFAULT '{}',
    group_id       TEXT NOT NULL DEFAULT '',
    version        INTEGER NOT NULL DEFAULT 1,
    is_active      INTEGER NOT NULL DEFAULT 1,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    deleted_at     INTEGER
);
CREATE INDEX IF NOT EXISTS idx_docs_status ON docs (status, deleted_at);

CREATE TABLE IF NOT EXISTS parents (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id       TEXT NOT NULL,
    parent_ord   INTEGER NOT NULL DEFAULT 0,
    content      TEXT NOT NULL,
    heading_path TEXT NOT NULL DEFAULT '',
    page_no      INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_parents_doc ON parents (doc_id);

CREATE TABLE IF NOT EXISTS chunks (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    chunk_id      TEXT NOT NULL UNIQUE,
    doc_id        TEXT NOT NULL,
    ord           INTEGER NOT NULL DEFAULT 0,
    parent_seq    INTEGER,
    content       TEXT NOT NULL,
    tokens        TEXT NOT NULL DEFAULT '',
    heading_path  TEXT NOT NULL DEFAULT '',
    page_no       INTEGER NOT NULL DEFAULT 1,
    category      TEXT NOT NULL DEFAULT '',
    source_type   TEXT NOT NULL DEFAULT 'markdown',
    token_count   INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks (doc_id);
CREATE INDEX IF NOT EXISTS idx_chunks_parent ON chunks (parent_seq);

CREATE TABLE IF NOT EXISTS kb_meta (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    embed_model TEXT NOT NULL DEFAULT '',
    dim         INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER NOT NULL DEFAULT 0
);

-- RAG 参数持久化（2026-09-03）：从 rag_config.json 迁入库，与向量/源文件同一
-- 可复制单元（拷 kb.db 即迁移参数）。key-value，值统一存字符串（读时按白名单转型）。
CREATE TABLE IF NOT EXISTS kb_config (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL DEFAULT '',
    updated_at INTEGER NOT NULL DEFAULT 0
);

-- 实体索引（知识图谱阶段一：入库期 LLM 抽取，检索期 FTS5 匹配）
CREATE TABLE IF NOT EXISTS entities (
    seq          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_name  TEXT NOT NULL,
    entity_type  TEXT NOT NULL DEFAULT '',
    doc_id       TEXT NOT NULL,
    parent_seq   INTEGER NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_entities_doc ON entities (doc_id);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities (entity_type);
"""

# FTS5 虚表单独建（虚拟表 DDL 与普通表分开更清晰；unicode61 分词，token 已由 jieba 预切）
_FTS_SCHEMA = f"CREATE VIRTUAL TABLE IF NOT EXISTS {_FTS_TABLE} USING fts5(tokens);"

# 文档级 FTS5：索引 title + file_name，供文档列表搜索（万级规模 <10ms）
_DOCS_FTS_SCHEMA = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_DOCS_FTS_TABLE} USING fts5(doc_id, tokens);"
)

# 实体级 FTS5：索引 entity_name + entity_type，供检索期匹配实体名
_ENT_FTS_SCHEMA = (
    f"CREATE VIRTUAL TABLE IF NOT EXISTS {_ENT_FTS_TABLE}"
    f" USING fts5(entity_name, entity_type, content=entities, content_rowid=seq);"
)

# 新增列（对旧库渐进迁移；缺列时 ALTER 补，默认值保证既有行为不变）
_CHUNK_COLUMNS: tuple[tuple[str, str], ...] = (
    ("parent_seq", "INTEGER"),
    ("tokens", "TEXT NOT NULL DEFAULT ''"),
    ("heading_path", "TEXT NOT NULL DEFAULT ''"),
    ("page_no", "INTEGER NOT NULL DEFAULT 1"),
    ("category", "TEXT NOT NULL DEFAULT ''"),
    ("source_type", "TEXT NOT NULL DEFAULT 'markdown'"),
)

# docs 表版本化列（逻辑资料 group_id + 版本号 version + 是否生效 is_active）。
# 旧库缺列时 ALTER 补，随后一次性回填：每条既有文档自成单版本 group（行为不变）。
_DOCS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("group_id", "TEXT NOT NULL DEFAULT ''"),
    ("version", "INTEGER NOT NULL DEFAULT 1"),
    ("is_active", "INTEGER NOT NULL DEFAULT 1"),
)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """从对象直接属性或其 metadata dict 取值（兼容 KnowledgeChunk 与富分块对象）。"""
    val = getattr(obj, name, None)
    if val is None:
        meta = getattr(obj, "metadata", None)
        if isinstance(meta, dict):
            val = meta.get(name)
    return default if val is None else val


class KnowledgeStorage:
    """知识库 SQLite DAO（文档 CRUD + 父子分块 + FTS5/向量三层索引）。"""

    def __init__(self, db_path: str):
        self.db_path = str(db_path)
        logger.debug("[KB storage] initialized at %s", self.db_path)

    # ---- 连接 ---------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        vs.load_extension(conn)  # best-effort；不可用时向量通道返空
        conn.executescript(_SCHEMA)
        try:
            conn.execute(_FTS_SCHEMA)
        except sqlite3.Error as exc:  # pragma: no cover - FTS5 缺失极罕见
            logger.warning("[KB storage] FTS5 unavailable: %s", exc)
        try:
            conn.execute(_DOCS_FTS_SCHEMA)
        except sqlite3.Error as exc:
            logger.debug("[KB storage] docs FTS5 unavailable: %s", exc)
        try:
            conn.execute(_ENT_FTS_SCHEMA)
        except sqlite3.Error as exc:
            logger.debug("[KB storage] entities FTS5 unavailable: %s", exc)
        self._migrate_columns(conn)
        self._migrate_docs_columns(conn)
        # idx_docs_group 引用版本化新列，必须在 _migrate_docs_columns 补列之后建
        # （旧库 docs 已存在时 CREATE TABLE IF NOT EXISTS 会跳过，列由 ALTER 补齐）。
        try:
            conn.execute("CREATE INDEX IF NOT EXISTS idx_docs_group ON docs (group_id, is_active)")
        except sqlite3.Error as exc:  # pragma: no cover - 索引建失败不阻断读写
            logger.debug("[KB storage] create idx_docs_group failed: %s", exc)
        # 文档搜索 FTS 全量重建（万级规模 <100ms，保证旧库升级/数据漂移后索引正确）
        try:
            self._rebuild_docs_fts(conn)
        except sqlite3.Error as exc:
            logger.debug("[KB storage] rebuild docs FTS failed: %s", exc)
        return conn

    @staticmethod
    def _migrate_columns(conn: sqlite3.Connection) -> None:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(chunks)")}
        for name, decl in _CHUNK_COLUMNS:
            if name not in existing:
                try:
                    conn.execute(f"ALTER TABLE chunks ADD COLUMN {name} {decl}")
                except sqlite3.Error as exc:  # pragma: no cover
                    logger.debug("[KB storage] add column %s failed: %s", name, exc)

    @staticmethod
    def _migrate_docs_columns(conn: sqlite3.Connection) -> None:
        """docs 表版本化列渐进迁移；首次补 group_id 后回填旧库（每条文档自成单版本组）。"""
        existing = {r[1] for r in conn.execute("PRAGMA table_info(docs)")}
        added_group = False
        for name, decl in _DOCS_COLUMNS:
            if name not in existing:
                try:
                    conn.execute(f"ALTER TABLE docs ADD COLUMN {name} {decl}")
                    if name == "group_id":
                        added_group = True
                except sqlite3.Error as exc:  # pragma: no cover
                    logger.debug("[KB storage] add docs column %s failed: %s", name, exc)
        if added_group:
            # 旧库回填：group_id 缺省=自身 doc_id，version=1，is_active=1（完全保持既有行为）
            conn.execute(
                "UPDATE docs SET group_id = doc_id WHERE group_id = '' OR group_id IS NULL"
            )
            conn.execute("UPDATE docs SET version = 1 WHERE version IS NULL")
            conn.execute("UPDATE docs SET is_active = 1 WHERE is_active IS NULL")
            conn.commit()

    # ---- 文档搜索 FTS5（title + file_name 全文索引，供列表搜索）-----------------

    def _rebuild_docs_fts(self, conn: sqlite3.Connection) -> None:
        """全量重建文档搜索 FTS（遍历未删除且生效的 docs）；万级规模 <100ms。"""
        conn.execute(f"DELETE FROM {_DOCS_FTS_TABLE}")
        rows = conn.execute(
            "SELECT doc_id, title, file_name FROM docs WHERE deleted_at IS NULL AND is_active = 1"
        ).fetchall()
        for r in rows:
            tokens = tk.tokens_to_fts(f"{r['title']} {r['file_name']}")
            if tokens.strip():
                conn.execute(
                    f"INSERT INTO {_DOCS_FTS_TABLE}(doc_id, tokens) VALUES (?, ?)",
                    (r["doc_id"], tokens),
                )

    def _sync_docs_fts(self, conn: sqlite3.Connection, doc_id: str) -> None:
        """单条文档 FTS 增量同步（先删后插；文档不存在时仅删）。"""
        conn.execute(f"DELETE FROM {_DOCS_FTS_TABLE} WHERE doc_id = ?", (doc_id,))
        row = conn.execute(
            "SELECT title, file_name FROM docs WHERE doc_id = ?"
            " AND deleted_at IS NULL AND is_active = 1",
            (doc_id,),
        ).fetchone()
        if row:
            tokens = tk.tokens_to_fts(f"{row['title']} {row['file_name']}")
            if tokens.strip():
                conn.execute(
                    f"INSERT INTO {_DOCS_FTS_TABLE}(doc_id, tokens) VALUES (?, ?)",
                    (doc_id, tokens),
                )

    def _remove_docs_fts(self, conn: sqlite3.Connection, doc_id: str) -> None:
        """从文档搜索 FTS 移除指定文档。"""
        conn.execute(f"DELETE FROM {_DOCS_FTS_TABLE} WHERE doc_id = ?", (doc_id,))

    # ---- 迁移健康度（模型/维度漂移检测）--------------------------------------

    def get_meta(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT embed_model, dim FROM kb_meta WHERE id = 1").fetchone()
        return {"embed_model": row[0] if row else "", "dim": int(row[1]) if row else 0}

    def set_meta(self, embed_model: str, dim: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO kb_meta (id, embed_model, dim, updated_at) VALUES (1, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET embed_model=excluded.embed_model,"
                " dim=excluded.dim, updated_at=excluded.updated_at",
                (embed_model, int(dim), int(time.time())),
            )
            conn.commit()

    # ---- RAG 参数持久化（kb_config 表，与向量/源文件同库可复制迁移）----

    def get_all_config(self) -> dict[str, str]:
        """读取全部持久化 RAG 参数（key -> 字符串值）；表空/不存在返 {}。"""
        with self._connect() as conn:
            rows = conn.execute("SELECT key, value FROM kb_config").fetchall()
        return {str(r["key"]): str(r["value"]) for r in rows}

    def set_config_many(self, patch: dict[str, str]) -> None:
        """批量 upsert RAG 参数（值统一转字符串落库）。"""
        if not patch:
            return
        now = int(time.time())
        with self._connect() as conn:
            for key, value in patch.items():
                conn.execute(
                    "INSERT INTO kb_config (key, value, updated_at) VALUES (?, ?, ?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                    " updated_at=excluded.updated_at",
                    (str(key), str(value), now),
                )
            conn.commit()

    def needs_reindex(self, embed_model: str, dim: int) -> bool:
        """当前模型/维度与建库时不一致 → 需重建向量（迁移到不同模型环境时自愈）。"""
        meta = self.get_meta()
        if not meta["dim"]:
            return False  # 尚无向量，谈不上漂移
        return meta["embed_model"] != embed_model or int(meta["dim"]) != int(dim)

    # ---- 文档级 CRUD --------------------------------------------------------

    def insert_doc(
        self,
        *,
        doc_id: str,
        title: str,
        file_name: str = "",
        source_type: str = "markdown",
        source_relpath: str | None = None,
        category: str = "",
        size_bytes: int = 0,
        status: str = "pending",
        metadata: dict[str, Any] | None = None,
        group_id: str | None = None,
        version: int = 1,
        is_active: bool | int = 1,
    ) -> None:
        now = int(time.time())
        gid = str(group_id or doc_id)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO docs (doc_id, title, file_name, source_type, source_relpath,"
                " category, size_bytes, status, chunk_count, error, metadata_json,"
                " group_id, version, is_active, created_at, updated_at, deleted_at)"
                " VALUES (?,?,?,?,?,?,?,?,0,NULL,?,?,?,?,?,?,NULL)"
                " ON CONFLICT(doc_id) DO UPDATE SET title=excluded.title,"
                " file_name=excluded.file_name, source_type=excluded.source_type,"
                " source_relpath=excluded.source_relpath, category=excluded.category,"
                " size_bytes=excluded.size_bytes, status=excluded.status,"
                " metadata_json=excluded.metadata_json, group_id=excluded.group_id,"
                " version=excluded.version, updated_at=excluded.updated_at, deleted_at=NULL",
                (
                    doc_id,
                    title,
                    file_name,
                    source_type,
                    source_relpath,
                    category,
                    int(size_bytes),
                    status,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    gid,
                    int(version),
                    1 if is_active else 0,
                    now,
                    now,
                ),
            )
            conn.commit()
            self._sync_docs_fts(conn, doc_id)

    # 兼容旧符号（api.py 调 upsert_doc）
    def upsert_doc(self, doc: Any) -> Any:
        doc_id = str(_attr(doc, "id", "") or _attr(doc, "doc_id", "") or "")
        if not doc_id:
            return doc
        self.insert_doc(
            doc_id=doc_id,
            title=str(_attr(doc, "title", "") or ""),
            source_type=str(_attr(doc, "source_type", "markdown") or "markdown"),
            source_relpath=_attr(doc, "source_path", None),
            metadata=_attr(doc, "metadata", {}) or {},
        )
        return doc

    def set_doc_status(
        self, doc_id: str, status: str, *, error: str | None = None, chunk_count: int | None = None
    ) -> None:
        sets = ["status = ?", "updated_at = ?"]
        params: list[Any] = [status, int(time.time())]
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if chunk_count is not None:
            sets.append("chunk_count = ?")
            params.append(int(chunk_count))
        params.append(doc_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE docs SET {', '.join(sets)} WHERE doc_id = ?", params)
            conn.commit()

    def _doc_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "doc_id": row["doc_id"],
            "id": row["doc_id"],
            "title": row["title"],
            "file_name": row["file_name"],
            "source_type": row["source_type"],
            "source_relpath": row["source_relpath"],
            "category": row["category"],
            "size_bytes": row["size_bytes"],
            "status": row["status"],
            "chunk_count": row["chunk_count"],
            "error": row["error"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "group_id": row["group_id"] or row["doc_id"],
            "version": int(row["version"] or 1),
            "is_active": bool(row["is_active"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "deleted_at": row["deleted_at"],
        }

    def get_doc(self, doc_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM docs WHERE doc_id = ? AND deleted_at IS NULL", (doc_id,)
            ).fetchone()
        return self._doc_from_row(row) if row else None

    def resolve_file_path(self, doc_id: str) -> str:
        """该文档已复制入库源文件的绝对路径（供前端点击预览）；无/丢失时空串。

        零绝对路径入库红线不变：只读时由相对 source_relpath 拼当前数据根 files/。
        """
        doc = self.get_doc(doc_id)
        rel = str((doc or {}).get("source_relpath") or "")
        if not rel:
            return ""
        try:
            from agent.knowledge.rag_config import kb_files_dir

            fp = kb_files_dir() / rel
            return str(fp) if fp.is_file() else ""
        except OSError:
            return ""

    def list_docs(
        self,
        source_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
        *,
        include_deleted: bool = False,
        active_only: bool = True,
        q: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出文档；默认每个逻辑资料只返回其生效版本（active_only），并附 version_count。

        q 非空时走 FTS5 全文检索（title + file_name），按 BM25 排序；
        category 非空时按分类精确过滤。
        """
        clauses: list[str] = []
        params: list[Any] = []
        if not include_deleted:
            clauses.append("d.deleted_at IS NULL")
        if active_only:
            clauses.append("d.is_active = 1")
        if source_type:
            clauses.append("d.source_type = ?")
            params.append(source_type)
        if category:
            clauses.append("d.category = ?")
            params.append(category)

        fts_doc_ids: list[str] | None = None
        if q and q.strip():
            match_q = tk.build_match_query(q)
            if match_q:
                try:
                    with self._connect() as fts_conn:
                        fts_rows = fts_conn.execute(
                            f"SELECT doc_id FROM {_DOCS_FTS_TABLE}"
                            f" WHERE tokens MATCH ? ORDER BY bm25({_DOCS_FTS_TABLE})",
                            (match_q,),
                        ).fetchall()
                    fts_doc_ids = [str(r["doc_id"]) for r in fts_rows]
                except sqlite3.Error as exc:
                    logger.debug("docs FTS search failed, returning empty: %s", exc)
                    return []
                if not fts_doc_ids:
                    return []
                clauses.append(f"d.doc_id IN ({','.join('?' * len(fts_doc_ids))})")
                params.extend(fts_doc_ids)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        order = "ORDER BY d.created_at DESC"
        if q and q.strip() and fts_doc_ids is not None:
            rank_sub = (
                f"(SELECT bm25({_DOCS_FTS_TABLE}) FROM {_DOCS_FTS_TABLE} WHERE doc_id = d.doc_id)"
            )
            order = f"ORDER BY {rank_sub}"

        sql = (
            "SELECT d.*, (SELECT COUNT(*) FROM docs d2 WHERE d2.group_id = d.group_id"
            " AND d2.deleted_at IS NULL) AS version_count"
            f" FROM docs d{where} {order} LIMIT ? OFFSET ?"
        )
        params.extend([int(limit), int(offset)])
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = self._doc_from_row(r)
            d["version_count"] = int(r["version_count"] or 1)
            out.append(d)
        return out

    def count_docs(
        self,
        source_type: str | None = None,
        *,
        include_deleted: bool = False,
        active_only: bool = True,
        q: str | None = None,
        category: str | None = None,
    ) -> int:
        sql = "SELECT COUNT(*) FROM docs"
        clauses: list[str] = []
        params: list[Any] = []
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if active_only:
            clauses.append("is_active = 1")
        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)
        if category:
            clauses.append("category = ?")
            params.append(category)
        if q and q.strip():
            match_q = tk.build_match_query(q)
            if match_q:
                try:
                    with self._connect() as conn:
                        fts_rows = conn.execute(
                            f"SELECT doc_id FROM {_DOCS_FTS_TABLE} WHERE tokens MATCH ?",
                            (match_q,),
                        ).fetchall()
                    fts_ids = [str(r["doc_id"]) for r in fts_rows]
                except sqlite3.Error:
                    return 0
                if not fts_ids:
                    return 0
                clauses.append(f"doc_id IN ({','.join('?' * len(fts_ids))})")
                params.extend(fts_ids)
            else:
                return 0
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        with self._connect() as conn:
            return int(conn.execute(sql, params).fetchone()[0])

    def mark_all_stale(self) -> int:
        """模型/维度漂移时把所有就绪文档标为 stale（提示重建索引）。"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE docs SET status = 'stale', updated_at = ?"
                " WHERE status = 'ready' AND deleted_at IS NULL",
                (int(time.time()),),
            )
            conn.commit()
            return cur.rowcount or 0

    def reset_stale_docs(self) -> int:
        """重建完成后把 stale 文档恢复为 ready。"""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE docs SET status = 'ready', updated_at = ? WHERE status = 'stale'",
                (int(time.time()),),
            )
            conn.commit()
            return cur.rowcount or 0

    def soft_delete_doc(self, doc_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE docs SET deleted_at = ?, updated_at = ? WHERE doc_id = ? AND deleted_at IS NULL",
                (int(time.time()), int(time.time()), doc_id),
            )
            self._remove_docs_fts(conn, doc_id)
            conn.commit()
            return (cur.rowcount or 0) > 0

    def hard_delete_doc(
        self, doc_id: str, *, delete_file: bool = False, all_versions: bool = False
    ) -> bool:
        """级联硬删除：chunks(+FTS+vec) → parents → docs 行（可选删复制文件）。

        all_versions=True → 删同一逻辑资料(group)的全部版本；否则只删该版本，
        若删的是生效版本，删后自动把同组最新剩余版本设为生效（group 删空则无生效版本）。
        """
        gid = self.group_id_of(doc_id)
        if all_versions:
            with self._connect() as conn:
                ids = [
                    str(r[0])
                    for r in conn.execute(
                        "SELECT doc_id FROM docs WHERE group_id = ?", (gid,)
                    ).fetchall()
                ]
            deleted = False
            for did in ids:
                deleted = self._hard_delete_one(did, delete_file=delete_file) or deleted
            return deleted

        was_active = False
        with self._connect() as conn:
            row = conn.execute("SELECT is_active FROM docs WHERE doc_id = ?", (doc_id,)).fetchone()
            was_active = bool(row[0]) if row is not None else False
        deleted = self._hard_delete_one(doc_id, delete_file=delete_file)
        if deleted and was_active:
            with self._connect() as conn:
                nxt = conn.execute(
                    "SELECT doc_id FROM docs WHERE group_id = ? AND deleted_at IS NULL"
                    " ORDER BY version DESC LIMIT 1",
                    (gid,),
                ).fetchone()
            if nxt is not None:
                self.activate_version(str(nxt[0]))
        return deleted

    def _hard_delete_one(self, doc_id: str, *, delete_file: bool = False) -> bool:
        """硬删单个版本文档：chunks(+FTS+vec) → entities(+FTS) → parents → docs 行。"""
        self.delete_chunks_by_doc(doc_id)
        self.delete_entities_by_doc(doc_id)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT source_relpath FROM docs WHERE doc_id = ?", (doc_id,)
            ).fetchone()
            conn.execute("DELETE FROM parents WHERE doc_id = ?", (doc_id,))
            self._remove_docs_fts(conn, doc_id)
            cur = conn.execute("DELETE FROM docs WHERE doc_id = ?", (doc_id,))
            conn.commit()
        if delete_file and row and row[0]:
            try:
                from agent.knowledge.rag_config import kb_files_dir

                fp = kb_files_dir() / str(row[0])
                if fp.is_file():
                    fp.unlink()
            except OSError as exc:
                logger.warning("[KB storage] delete copied file failed: %s", exc)
        return (cur.rowcount or 0) > 0

    # ---- 版本管理（逻辑资料 group → 多版本；检索只命中生效版本）-------------

    def group_id_of(self, doc_id: str) -> str:
        """该文档所属逻辑资料 group_id；无记录/未回填时以自身 doc_id 为组。"""
        with self._connect() as conn:
            row = conn.execute("SELECT group_id FROM docs WHERE doc_id = ?", (doc_id,)).fetchone()
        if row is not None and row[0]:
            return str(row[0])
        return doc_id

    def next_version(self, group_id: str) -> int:
        """组内下一个版本号（max(version)+1；含已软删版本以避免号段回退复用）。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 FROM docs WHERE group_id = ?", (group_id,)
            ).fetchone()
        return int(row[0]) if row is not None else 1

    def version_count(self, group_id: str) -> int:
        """组内未删版本数。"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM docs WHERE group_id = ? AND deleted_at IS NULL", (group_id,)
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def list_group_versions(self, group_id: str) -> list[dict[str, Any]]:
        """组内全部未删版本（version 倒序）；供前端「版本历史」展示。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM docs WHERE group_id = ? AND deleted_at IS NULL"
                " ORDER BY version DESC",
                (group_id,),
            ).fetchall()
        return [self._doc_from_row(r) for r in rows]

    def activate_version(self, doc_id: str) -> bool:
        """把指定版本设为其 group 的唯一生效版本（同组其余未删版本置 is_active=0）。

        仅翻标志位：所有版本的分块/向量常驻库中，切换生效版本 O(1)、无需重建索引。
        """
        now = int(time.time())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT group_id FROM docs WHERE doc_id = ? AND deleted_at IS NULL", (doc_id,)
            ).fetchone()
            if row is None:
                return False
            gid = str(row[0] or doc_id)
            conn.execute(
                "UPDATE docs SET is_active = 0, updated_at = ?"
                " WHERE group_id = ? AND doc_id != ? AND deleted_at IS NULL",
                (now, gid, doc_id),
            )
            cur = conn.execute(
                "UPDATE docs SET is_active = 1, updated_at = ?"
                " WHERE doc_id = ? AND deleted_at IS NULL",
                (now, doc_id),
            )
            conn.commit()
            # 刷新文档搜索 FTS：新生效版本加入，同组其余版本移出
            self._sync_docs_fts(conn, doc_id)
            for r in conn.execute(
                "SELECT doc_id FROM docs WHERE group_id = ? AND doc_id != ? AND deleted_at IS NULL",
                (gid, doc_id),
            ).fetchall():
                self._remove_docs_fts(conn, str(r["doc_id"]))
            return (cur.rowcount or 0) > 0

    def set_category(self, doc_id: str, category: str) -> bool:
        """修改某文档所属逻辑资料（整组全部版本）的分类，同步刷新其子块 category。

        分类是「一份资料」的属性，故按 group 统一更新（含各版本 docs 行与其 chunks），
        切换生效版本后分类保持一致；chunks.category 供检索按类过滤。
        """
        gid = self.group_id_of(doc_id)
        cat = str(category or "")
        now = int(time.time())
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE docs SET category = ?, updated_at = ? WHERE group_id = ?",
                (cat, now, gid),
            )
            conn.execute(
                "UPDATE chunks SET category = ? WHERE doc_id IN"
                " (SELECT doc_id FROM docs WHERE group_id = ?)",
                (cat, gid),
            )
            conn.commit()
            return (cur.rowcount or 0) > 0

    # ---- 父块 ---------------------------------------------------------------

    def upsert_parents(self, doc_id: str, parents: list[Any]) -> dict[int, int]:
        """写入父块，返回 {parent_ord: parent_seq}（供子块关联）。"""
        mapping: dict[int, int] = {}
        if not parents:
            return mapping
        with self._connect() as conn:
            conn.execute("DELETE FROM parents WHERE doc_id = ?", (doc_id,))
            for p in parents:
                p_ord = int(_attr(p, "ord", 0) or 0)
                cur = conn.execute(
                    "INSERT INTO parents (doc_id, parent_ord, content, heading_path, page_no)"
                    " VALUES (?,?,?,?,?)",
                    (
                        doc_id,
                        p_ord,
                        str(_attr(p, "text", "") or _attr(p, "content", "") or ""),
                        str(_attr(p, "heading_path", "") or ""),
                        int(_attr(p, "page_no", 1) or 1),
                    ),
                )
                mapping[p_ord] = int(cur.lastrowid or 0)
            conn.commit()
        return mapping

    def get_parents_by_seq(self, seqs: list[int]) -> dict[int, dict[str, Any]]:
        if not seqs:
            return {}
        qmarks = ",".join("?" * len(seqs))
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM parents WHERE seq IN ({qmarks})", seqs).fetchall()
        return {
            int(r["seq"]): {
                "seq": int(r["seq"]),
                "doc_id": r["doc_id"],
                "parent_ord": r["parent_ord"],
                "content": r["content"],
                "heading_path": r["heading_path"],
                "page_no": r["page_no"],
            }
            for r in rows
        }

    # ---- 子块 + FTS5 + 向量 -------------------------------------------------

    def upsert_chunks(self, doc_id: str, chunks: list[Any]) -> None:
        """写入/覆盖一个文档的子块；同步维护 FTS5 token 串与 vec0 向量行。"""
        now = int(time.time())
        with self._connect() as conn:
            fts_ok = self._fts_ready(conn)
            for chunk in chunks:
                chunk_id = str(_attr(chunk, "id", "") or "")
                content = str(_attr(chunk, "content", "") or "")
                if not chunk_id or not content:
                    continue
                index_text = str(_attr(chunk, "index_text", None) or content)
                tokens = tk.tokens_to_fts(index_text)
                conn.execute(
                    "INSERT INTO chunks (chunk_id, doc_id, ord, parent_seq, content, tokens,"
                    " heading_path, page_no, category, source_type, token_count,"
                    " metadata_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(chunk_id) DO UPDATE SET"
                    "  doc_id=excluded.doc_id, ord=excluded.ord, parent_seq=excluded.parent_seq,"
                    "  content=excluded.content, tokens=excluded.tokens,"
                    "  heading_path=excluded.heading_path, page_no=excluded.page_no,"
                    "  category=excluded.category, source_type=excluded.source_type,"
                    "  token_count=excluded.token_count, metadata_json=excluded.metadata_json",
                    (
                        chunk_id,
                        doc_id,
                        int(_attr(chunk, "seq", 0) or _attr(chunk, "ord", 0) or 0),
                        _attr(chunk, "parent_seq", None),
                        content,
                        tokens,
                        str(_attr(chunk, "heading_path", "") or ""),
                        int(_attr(chunk, "page_no", 1) or 1),
                        str(_attr(chunk, "category", "") or ""),
                        str(_attr(chunk, "source_type", "markdown") or "markdown"),
                        int(_attr(chunk, "token_count", 0) or 0),
                        json.dumps(_attr(chunk, "metadata", {}) or {}, ensure_ascii=False),
                        now,
                    ),
                )
                seq = int(
                    conn.execute(
                        "SELECT seq FROM chunks WHERE chunk_id = ?", (chunk_id,)
                    ).fetchone()[0]
                )
                # FTS5：先删后插（覆盖旧 token）
                if fts_ok:
                    try:
                        conn.execute(f"DELETE FROM {_FTS_TABLE} WHERE rowid = ?", (seq,))
                        conn.execute(
                            f"INSERT INTO {_FTS_TABLE}(rowid, tokens) VALUES (?, ?)", (seq, tokens)
                        )
                    except sqlite3.Error as exc:
                        logger.debug("[KB storage] fts upsert failed: %s", exc)
                # 向量：先删后插（vec0 不支持 INSERT OR REPLACE）
                embedding = _attr(chunk, "embedding", None)
                if embedding and any(embedding):
                    vs.ensure_vec_table(conn, _CHUNK_VEC_TABLE, len(embedding))
                    vs.upsert(conn, _CHUNK_VEC_TABLE, seq, list(embedding))
            conn.commit()

    @staticmethod
    def _fts_ready(conn: sqlite3.Connection) -> bool:
        try:
            conn.execute(f"SELECT count(*) FROM {_FTS_TABLE}").fetchone()
            return True
        except sqlite3.Error:
            return False

    def _chunk_from_row(self, conn: sqlite3.Connection, row: sqlite3.Row) -> Any:
        from agent.knowledge.models import KnowledgeChunk

        embedding: list[float] | None = None
        if vs.vec_ready(conn):
            vec_row = conn.execute(
                f"SELECT embedding FROM {_CHUNK_VEC_TABLE} WHERE rowid = ?", (row["seq"],)
            ).fetchone()
            if vec_row is not None:
                embedding = vs.deserialize(vec_row[0])
        meta = json.loads(row["metadata_json"] or "{}")
        # 富字段回填到 metadata，供上层（parent_seq/page_no/heading）使用
        meta.setdefault("heading_path", row["heading_path"])
        meta.setdefault("page_no", row["page_no"])
        meta.setdefault("category", row["category"])
        meta.setdefault("source_type", row["source_type"])
        meta.setdefault("parent_seq", row["parent_seq"])
        meta.setdefault("seq", row["seq"])
        return KnowledgeChunk(
            id=row["chunk_id"],
            doc_id=row["doc_id"],
            seq=row["ord"],
            content=row["content"],
            token_count=row["token_count"],
            embedding=embedding,
            metadata=meta,
            created_at=row["created_at"],
        )

    def get_chunk(self, chunk_id: str) -> Any:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
            return self._chunk_from_row(conn, row) if row is not None else None

    def get_chunks_by_doc(self, doc_id: str) -> list[Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM chunks WHERE doc_id = ? ORDER BY ord, seq", (doc_id,)
            ).fetchall()
            return [self._chunk_from_row(conn, r) for r in rows]

    def get_chunks_by_seq(self, seqs: list[int]) -> dict[int, Any]:
        if not seqs:
            return {}
        qmarks = ",".join("?" * len(seqs))
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM chunks WHERE seq IN ({qmarks})", seqs).fetchall()
            return {int(r["seq"]): self._chunk_from_row(conn, r) for r in rows}

    def delete_chunks_by_doc(self, doc_id: str) -> int:
        """删除文档全部子块（含 FTS5 行与向量行）；返回删除条数。"""
        with self._connect() as conn:
            rows = conn.execute("SELECT seq FROM chunks WHERE doc_id = ?", (doc_id,)).fetchall()
            seqs = [int(r[0]) for r in rows]
            fts_ok = self._fts_ready(conn)
            if vs.vec_ready(conn):
                for s in seqs:
                    vs.delete(conn, _CHUNK_VEC_TABLE, s)
            if fts_ok:
                for s in seqs:
                    conn.execute(f"DELETE FROM {_FTS_TABLE} WHERE rowid = ?", (s,))
            cursor = conn.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
            conn.commit()
            return cursor.rowcount or 0

    # ---- 实体索引（知识图谱阶段一）----------------------------------------------

    def _ent_fts_ready(self, conn: sqlite3.Connection) -> bool:
        """实体 FTS5 虚表是否可用。"""
        try:
            conn.execute(f"SELECT count(*) FROM {_ENT_FTS_TABLE}").fetchone()
            return True
        except sqlite3.Error:
            return False

    def upsert_entities(
        self, doc_id: str, entities: list[dict[str, Any]], parent_seq: int
    ) -> int:
        """写入一个父块抽取到的实体列表；返回写入条数。

        每个 dict 需含 entity_name；可选 entity_type（缺省空串）。
        同步维护 entities_fts 内容外部表触发器（INSERT/DELETE 自动同步）。
        """
        if not entities:
            return 0
        now = int(time.time())
        inserted = 0
        with self._connect() as conn:
            for ent in entities:
                name = str(ent.get("entity_name", "")).strip()
                if not name:
                    continue
                etype = str(ent.get("entity_type", "") or "")
                conn.execute(
                    "INSERT INTO entities (entity_name, entity_type, doc_id, parent_seq, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (name, etype, doc_id, int(parent_seq), now),
                )
                inserted += 1
            conn.commit()
        return inserted

    def delete_entities_by_doc(self, doc_id: str) -> int:
        """删除文档全部实体（FTS 内容外部表触发器自动清理）；返回删除条数。"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM entities WHERE doc_id = ?", (doc_id,))
            conn.commit()
            return cur.rowcount or 0

    def search_entities(
        self,
        query_text: str,
        limit: int = 20,
        *,
        filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """FTS5 匹配实体名，返回 [{entity_name, entity_type, doc_id, parent_seq}]。

        filter 支持 entity_type / doc_id 过滤。FTS5 不可用 → 回退 LIKE。
        """
        text = (query_text or "").strip()
        if not text:
            return []
        filt = dict(filter or {})
        clauses: list[str] = []
        params: list[Any] = []
        et = filt.get("entity_type")
        if et:
            clauses.append("e.entity_type = ?")
            params.append(str(et))
        did = filt.get("doc_id")
        if did:
            clauses.append("e.doc_id = ?")
            params.append(str(did))
        # 过滤子句（AND 形式）：LIKE 兜底拼在主 WHERE 之后，不能再带 WHERE
        filter_sql = (" AND " + " AND ".join(clauses)) if clauses else ""

        with self._connect() as conn:
            # 优先 FTS5
            if self._ent_fts_ready(conn):
                try:
                    match_q = tk.build_match_query(text)
                    if match_q:
                        fts_sql = (
                            f"SELECT e.entity_name, e.entity_type, e.doc_id, e.parent_seq"
                            f" FROM {_ENT_FTS_TABLE} f"
                            f" JOIN entities e ON e.seq = f.rowid"
                            f" WHERE {_ENT_FTS_TABLE} MATCH ?"
                            + (f" AND {' AND '.join(clauses)}" if clauses else "")
                            + " LIMIT ?"
                        )
                        rows = conn.execute(
                            fts_sql, (match_q, *params, int(limit))
                        ).fetchall()
                        if rows:
                            return [
                                {
                                    "entity_name": str(r["entity_name"]),
                                    "entity_type": str(r["entity_type"]),
                                    "doc_id": str(r["doc_id"]),
                                    "parent_seq": int(r["parent_seq"]),
                                }
                                for r in rows
                            ]
                except sqlite3.Error as exc:
                    logger.debug("[KB storage] entity FTS match failed: %s", exc)
            # LIKE 兜底
            like_sql = (
                "SELECT entity_name, entity_type, doc_id, parent_seq FROM entities e"
                f" WHERE e.entity_name LIKE ?{filter_sql} LIMIT ?"
            )
            rows = conn.execute(
                like_sql, (f"%{text}%", *params, int(limit))
            ).fetchall()
            return [
                {
                    "entity_name": str(r["entity_name"]),
                    "entity_type": str(r["entity_type"]),
                    "doc_id": str(r["doc_id"]),
                    "parent_seq": int(r["parent_seq"]),
                }
                for r in rows
            ]

    def get_entities_by_doc(self, doc_id: str) -> list[dict[str, Any]]:
        """列出文档全部实体（按类型分组排序，供前端文档详情展示）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT entity_name, entity_type, parent_seq FROM entities"
                " WHERE doc_id = ? ORDER BY entity_type, entity_name",
                (doc_id,),
            ).fetchall()
        return [
            {
                "entity_name": str(r["entity_name"]),
                "entity_type": str(r["entity_type"]),
                "parent_seq": int(r["parent_seq"]),
            }
            for r in rows
        ]

    # ---- 检索通道 -----------------------------------------------------------

    @staticmethod
    def _filter_and(filter: dict[str, Any] | None, alias: str = "c") -> tuple[str, list[Any]]:
        """构造 ' AND ...' 过滤片段（category / source_type / doc_id）+ 参数。"""
        if not filter:
            return "", []
        clauses: list[str] = []
        params: list[Any] = []
        cat = filter.get("category")
        if cat:
            # 标量→等值；列表→IN(...)（用于「某分类 + 未分类('')通用」一并纳入）
            cat_vals = [str(v) for v in (cat if isinstance(cat, (list, tuple)) else [cat])]
            clauses.append(f"{alias}.category IN ({','.join('?' * len(cat_vals))})")
            params.extend(cat_vals)
        st = filter.get("source_type")
        if st:
            vals = list(st) if isinstance(st, (list, tuple)) else [st]
            clauses.append(f"{alias}.source_type IN ({','.join('?' * len(vals))})")
            params.extend(str(v) for v in vals)
        did = filter.get("doc_id")
        if did:
            vals = list(did) if isinstance(did, (list, tuple)) else [did]
            clauses.append(f"{alias}.doc_id IN ({','.join('?' * len(vals))})")
            params.extend(str(v) for v in vals)
        if not clauses:
            return "", []
        return " AND " + " AND ".join(clauses), params

    # 排除软删文档的子块（无 docs 行的孤儿子块仍保留，兼容既有测试/直调）
    _NOT_DELETED = (
        " AND NOT EXISTS (SELECT 1 FROM docs d WHERE d.doc_id = c.doc_id"
        " AND d.deleted_at IS NOT NULL)"
    )

    # 检索可命中：在排除软删的基础上再排除「非生效版本」(is_active=0)。
    # 所有版本的分块/向量常驻库中，但只有生效版本参与召回；切换生效版本仅翻标志位。
    # 注：reindex 走 _NOT_DELETED（覆盖全部版本重嵌入），保证切到任意旧版本向量都可用。
    _RETRIEVABLE = (
        " AND NOT EXISTS (SELECT 1 FROM docs d WHERE d.doc_id = c.doc_id"
        " AND (d.deleted_at IS NOT NULL OR d.is_active = 0))"
    )

    def search_by_vector(
        self,
        query_embedding: Any,
        top_k: int = 3,
        similarity_threshold: float = 0.0,
        source_type_filter: Any = None,
        *,
        filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """稠密通道：余弦相似度检索子块；返 [{'chunk', 'similarity', 'seq'}]（降序）。"""
        query = list(query_embedding or [])
        if not query or not any(query):
            return []
        filt = dict(filter or {})
        if source_type_filter and "source_type" not in filt:
            filt["source_type"] = source_type_filter
        with self._connect() as conn:
            if not vs.vec_ready(conn) or vs.table_dim(conn, _CHUNK_VEC_TABLE) != len(query):
                return []
            blob = vs.serialize(query)
            cosine = vs.cosine_expr("v.embedding")
            and_clause, params = self._filter_and(filt)
            rows = conn.execute(
                f"SELECT c.*, {cosine} AS sim FROM chunks c "
                f"JOIN {_CHUNK_VEC_TABLE} v ON v.rowid = c.seq "
                f"WHERE {cosine} >= ?{self._RETRIEVABLE}{and_clause} "
                f"ORDER BY sim DESC LIMIT ?",
                (blob, blob, float(similarity_threshold), *params, int(top_k)),
            ).fetchall()
            return [
                {
                    "chunk": self._chunk_from_row(conn, r),
                    "similarity": float(r["sim"]),
                    "seq": int(r["seq"]),
                }
                for r in rows
            ]

    def search_by_fts(
        self, query_text: str, limit: int = 10, *, filter: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """稀疏通道：FTS5 原生 bm25() 检索；返 [{'chunk', 'seq', 'rank'}]（bm25 升序=相关性降序）。

        bm25() 越负越相关 → ORDER BY rank ASC。MATCH 语法错误/无有效词 → 返 []。
        """
        match = tk.build_match_query(query_text)
        if not match:
            return []
        and_clause, params = self._filter_and(filter)
        with self._connect() as conn:
            if not self._fts_ready(conn):
                return []
            try:
                rows = conn.execute(
                    f"SELECT c.*, bm25({_FTS_TABLE}) AS rank FROM {_FTS_TABLE} "
                    f"JOIN chunks c ON c.seq = {_FTS_TABLE}.rowid "
                    f"WHERE {_FTS_TABLE} MATCH ?{self._RETRIEVABLE}{and_clause} "
                    f"ORDER BY rank ASC LIMIT ?",
                    (match, *params, int(limit)),
                ).fetchall()
            except sqlite3.Error as exc:
                logger.debug("[KB storage] fts match failed (%s): %s", match, exc)
                return []
            return [
                {
                    "chunk": self._chunk_from_row(conn, r),
                    "seq": int(r["seq"]),
                    "rank": float(r["rank"]),
                }
                for r in rows
            ]

    def search_by_text(
        self,
        query: str,
        limit: int = 10,
        source_type_filter: Any = None,
        *,
        filter: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """LIKE 兜底检索（FTS5 与向量都不可用时）；返回结构同 search_by_vector。"""
        text = (query or "").strip()
        if not text:
            return []
        filt = dict(filter or {})
        if source_type_filter and "source_type" not in filt:
            filt["source_type"] = source_type_filter
        and_clause, params = self._filter_and(filt)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT c.* FROM chunks c WHERE c.content LIKE ?{self._RETRIEVABLE}{and_clause} "
                f"ORDER BY c.seq LIMIT ?",
                (f"%{text}%", *params, int(limit)),
            ).fetchall()
            return [
                {"chunk": self._chunk_from_row(conn, r), "similarity": 0.0, "seq": int(r["seq"])}
                for r in rows
            ]

    # ---- 重建索引支持 -------------------------------------------------------

    def iter_chunks_for_reindex(self) -> list[dict[str, Any]]:
        """列出全部未软删子块的 (seq, index_text) —— 模型漂移后重嵌入的事实源。"""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT c.seq, c.content, c.heading_path FROM chunks c WHERE 1=1{self._NOT_DELETED}"
                " ORDER BY c.seq"
            ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            heading = r["heading_path"] or ""
            index_text = f"{heading}\n{r['content']}" if heading else r["content"]
            out.append({"seq": int(r["seq"]), "index_text": index_text})
        return out

    def update_chunk_vector(self, seq: int, embedding: list[float]) -> bool:
        if not embedding or not any(embedding):
            return False
        with self._connect() as conn:
            if not vs.vec_ready(conn):
                return False
            vs.ensure_vec_table(conn, _CHUNK_VEC_TABLE, len(embedding))
            ok = vs.upsert(conn, _CHUNK_VEC_TABLE, int(seq), list(embedding))
            conn.commit()
            return ok

    def log_search(
        self,
        query: Any,
        results_count: int,
        avg_similarity: float,
        latency_ms: float,
        user_id: Any = None,
        top_k: int = 3,
    ) -> None:
        return None

    def get_stats(self) -> Any:
        from agent.knowledge.models import KnowledgeStats

        with self._connect() as conn:
            total_chunks = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            total_docs = conn.execute(
                "SELECT COUNT(*) FROM docs WHERE deleted_at IS NULL AND is_active = 1"
            ).fetchone()[0]
            by_source: dict[str, int] = {}
            for r in conn.execute(
                "SELECT source_type, COUNT(*) FROM docs WHERE deleted_at IS NULL"
                " AND is_active = 1 GROUP BY source_type"
            ).fetchall():
                by_source[str(r[0])] = int(r[1])
        return KnowledgeStats(
            total_docs=int(total_docs),
            total_chunks=int(total_chunks),
            by_source_type=by_source,
        )

    def list_source_types(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT source_type FROM docs WHERE deleted_at IS NULL"
            ).fetchall()
        return [str(r[0]) for r in rows if r[0]]

    def has_chunks(self) -> bool:
        """库内是否有可检索子块（排除软删文档）——供检索前快速短路。"""
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT 1 FROM chunks c WHERE 1=1{self._RETRIEVABLE} LIMIT 1"
            ).fetchone()
        return row is not None


_default_storage: KnowledgeStorage | None = None


def get_default_storage() -> KnowledgeStorage:
    """默认存储单例：db 落 rag_kb_dir()/kb.db（迁移自包含；测试经 EAIDE_RAG_KB_DIR 隔离）。"""
    global _default_storage
    if _default_storage is None:
        from agent.knowledge.rag_config import kb_db_path

        db_path = kb_db_path()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _default_storage = KnowledgeStorage(db_path)
    return _default_storage


def reset_default_storage() -> None:
    global _default_storage
    _default_storage = None


def encode_embedding(vec: Any) -> bytes:
    """向量 -> BLOB（与 vector_store.serialize 同格式，保留旧符号兼容）。"""
    return vs.serialize(list(vec))


def decode_embedding(blob: bytes, dim: int) -> Any:
    """BLOB -> 向量（截到 dim 维，保留旧签名兼容）。"""
    return vs.deserialize(blob)[:dim]
