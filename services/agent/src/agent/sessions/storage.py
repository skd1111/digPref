"""sessions.storage —— Phase 6 V0 + V1.5 会话 SQLite 存储层。

设计（与 codenav/biznav storage 同模式：sync sqlite3 + per-call connection）：
    - 会话元数据 / 消息 / checkpoint 引用三个表
    - 会话主键 UUID；消息自增；checkpoint 引用（thread_id, checkpoint_id）
      唯一约束防重复写入
    - 进程级单例（lazy init）

Phase 6 V1.5 扩展（CLAUDE.md §6 物理隔离 sessions.db）：
    - FTS5 全文搜索（sessions_fts 虚拟表 + 5 触发器，标题/消息/工具全索引）
    - 分支：parent_session_id / branch_from_checkpoint_id / branch_label
    - 共享权限：share_tokens_json + permissions_json（owner/read/write）
    - SessionEvent 哈希链：SHA-256(prev_hash + payload) 防篡改
    - 迁移：现有 V0 DB 自动 ALTER TABLE 加新列（无破坏）
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import uuid
from pathlib import Path

from .models import (
    BranchInfo,
    Message,
    MessageRole,
    Session,
    SessionCheckpoint,
    SessionEvent,
    SessionEventType,
    SessionStatus,
    SharePermission,
    ShareToken,
)


def _default_db_path() -> Path:
    """V0：跟随 envconfig 约定（%APPDATA%\\eaide\\sessions.db）。"""
    appdata = os.environ.get("APPDATA", str(Path.home()))
    return Path(appdata) / "eaide" / "sessions.db"


SESSIONS_DB = _default_db_path()


def now_ms() -> int:
    return int(time.time() * 1000)


def _read_schema_sql() -> str:
    here = Path(__file__).resolve().parent
    return (here / "schema.sql").read_text(encoding="utf-8")


class SessionStorage:
    """Phase 6 V0 + V1.5 会话 SQLite 存储层。"""

    def __init__(self, db_path: str | Path = SESSIONS_DB):
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # 一次性建表（多 connection OK）
        with self._connect() as conn:
            conn.executescript(_read_schema_sql())
        # V1.5 迁移：现有 V0 DB（无新列）走 ALTER TABLE 增量加列；新 DB 走 CREATE TABLE 默认值。
        # ALTER 失败（duplicate column）静默忽略：新 DB 列已存在。
        self._migrate_v15()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _migrate_v15(self) -> None:
        """V1.5 迁移：sessions 表加分支 / 共享字段（幂等，duplicate column 静默吞）。

        旧 V0 DB 走 ALTER TABLE 加列；新 DB 已经在 CREATE TABLE 中含新列 → ALTER
        抛 duplicate column → catch 后跳过。
        """
        additions = [
            "ALTER TABLE sessions ADD COLUMN parent_session_id TEXT DEFAULT NULL",
            "ALTER TABLE sessions ADD COLUMN branch_from_checkpoint_id TEXT DEFAULT NULL",
            "ALTER TABLE sessions ADD COLUMN branch_label TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE sessions ADD COLUMN share_tokens_json TEXT NOT NULL DEFAULT '[]'",
            "ALTER TABLE sessions ADD COLUMN permissions_json TEXT NOT NULL DEFAULT '{}'",
            "ALTER TABLE sessions ADD COLUMN shared_at INTEGER NOT NULL DEFAULT 0",
            "CREATE INDEX IF NOT EXISTS idx_sessions_parent ON sessions(parent_session_id)",
        ]
        with self._connect() as conn:
            for sql in additions:
                try:
                    conn.execute(sql)
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "duplicate column" in msg or "already exists" in msg:
                        continue
                    raise

    # ---- Session CRUD ----------------------------------------------------

    def create_session(
        self,
        title: str,
        owner: str = "default",
        project_name: str = "default",
        metadata: dict | None = None,
    ) -> Session:
        """创建会话 + 自动分配 thread_id（= session_id）+ 哈希链 'created' 事件。"""
        sid = str(uuid.uuid4())
        ts = now_ms()
        metadata = metadata or {}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(id, title, owner, project_name, status,
                                     created_at, updated_at, thread_id, metadata_json)
                VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (sid, title, owner, project_name, ts, ts, sid, json.dumps(metadata, ensure_ascii=False)),
            )
        # V1.5 哈希链：created 事件（best-effort，不阻塞主流程）
        try:
            self._append_event(
                sid, "created",
                {"title": title, "owner": owner, "project_name": project_name},
                actor=owner,
            )
        except Exception:
            pass
        return Session(
            id=sid, title=title, owner=owner, project_name=project_name,
            status="active", created_at=ts, updated_at=ts,
            thread_id=sid, metadata=metadata,
        )

    def get_session(self, session_id: str) -> Session | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(
        self,
        status: SessionStatus | None = "active",
        project_name: str | None = None,
        limit: int = 50,
    ) -> list[Session]:
        """列出会话（默认仅 active，按 updated_at DESC）。

        project_name 过滤可选；limit 防全表扫。
        """
        clauses: list[str] = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if project_name is not None:
            clauses.append("project_name = ?")
            params.append(project_name)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        sql = f"SELECT * FROM sessions{where} ORDER BY updated_at DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_session(r) for r in rows]

    def update_session(
        self,
        session_id: str,
        title: str | None = None,
        status: SessionStatus | None = None,
    ) -> bool:
        """更新 title / status；updated_at 自动刷新。"""
        sets: list[str] = []
        params: list = []
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if not sets:
            return False
        sets.append("updated_at = ?")
        params.append(now_ms())
        params.append(session_id)
        with self._connect() as conn:
            cur = conn.execute(
                f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?",
                params,
            )
            return cur.rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        """硬删除（CASCADE 删 messages + checkpoints）。"""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cur.rowcount > 0

    # ---- Messages --------------------------------------------------------

    def append_message(
        self,
        session_id: str,
        role: MessageRole,
        content: str,
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        tool_args: dict | None = None,
        tool_result: str | None = None,
        metadata: dict | None = None,
    ) -> Message:
        ts = now_ms()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        tool_args_json = (
            json.dumps(tool_args, ensure_ascii=False) if tool_args else None
        )
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO session_messages(
                    session_id, role, content, created_at,
                    tool_call_id, tool_name, tool_args_json, tool_result,
                    metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, role, content, ts,
                 tool_call_id, tool_name, tool_args_json, tool_result,
                 metadata_json),
            )
            msg_id = cur.lastrowid
            # 顺手刷 session.updated_at
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (ts, session_id),
            )
        # V1.5 哈希链：message_appended 事件（content 前 200 字避免超大 payload）
        try:
            self._append_event(
                session_id, "message_appended",
                {
                    "message_id": int(msg_id),
                    "role": role,
                    "content_preview": (content or "")[:200],
                    "tool_name": tool_name,
                },
                actor=role or "system",
            )
        except Exception:
            pass
        return Message(
            id=msg_id, session_id=session_id, role=role, content=content,
            created_at=ts, tool_call_id=tool_call_id, tool_name=tool_name,
            tool_args=tool_args, tool_result=tool_result, metadata=metadata or {},
        )

    def list_messages(
        self,
        session_id: str,
        limit: int = 200,
    ) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM session_messages WHERE session_id = ? "
                "ORDER BY created_at ASC, id ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [self._row_to_message(r) for r in rows]

    # ---- Checkpoint 引用 ----------------------------------------------

    def record_checkpoint(
        self,
        session_id: str,
        thread_id: str,
        checkpoint_id: str,
        label: str = "",
        description: str = "",
        metadata: dict | None = None,
    ) -> SessionCheckpoint:
        """记录一次 LangGraph checkpoint 引用（实际状态存在 LangGraph 表）。"""
        ts = now_ms()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO session_checkpoints(
                    session_id, thread_id, checkpoint_id, label, description,
                    created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, thread_id, checkpoint_id, label, description,
                 ts, metadata_json),
            )
            cid = cur.lastrowid
        return SessionCheckpoint(
            id=cid, session_id=session_id, thread_id=thread_id,
            checkpoint_id=checkpoint_id, label=label, description=description,
            created_at=ts, metadata=metadata or {},
        )

    def list_checkpoints(self, session_id: str) -> list[SessionCheckpoint]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM session_checkpoints WHERE session_id = ? "
                "ORDER BY created_at DESC",
                (session_id,),
            ).fetchall()
        return [self._row_to_checkpoint(r) for r in rows]

    # ---- private --------------------------------------------------------

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        # V1.5 字段：旧 DB 行可能没这些列 → row["xxx"] 会抛 KeyError，try/except 容错。
        try:
            parent_id = row["parent_session_id"]
        except (KeyError, IndexError):
            parent_id = None
        try:
            branch_cp = row["branch_from_checkpoint_id"]
        except (KeyError, IndexError):
            branch_cp = None
        try:
            branch_label = row["branch_label"]
        except (KeyError, IndexError):
            branch_label = ""
        try:
            share_tokens = json.loads(row["share_tokens_json"] or "[]")
        except (KeyError, IndexError):
            share_tokens = []
        try:
            permissions = json.loads(row["permissions_json"] or "{}")
        except (KeyError, IndexError):
            permissions = {}
        try:
            shared_at = int(row["shared_at"])
        except (KeyError, IndexError, TypeError):
            shared_at = 0
        return Session(
            id=row["id"],
            title=row["title"],
            owner=row["owner"],
            project_name=row["project_name"],
            status=row["status"],
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            thread_id=row["thread_id"],
            metadata=metadata,
            parent_session_id=parent_id,
            branch_from_checkpoint_id=branch_cp,
            branch_label=branch_label,
            share_tokens=share_tokens,
            permissions=permissions,
            shared_at=shared_at,
        )

    def _row_to_message(self, row: sqlite3.Row) -> Message:
        tool_args = (
            json.loads(row["tool_args_json"]) if row["tool_args_json"] else None
        )
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        return Message(
            id=int(row["id"]),
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            created_at=int(row["created_at"]),
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            tool_args=tool_args,
            tool_result=row["tool_result"],
            metadata=metadata,
        )

    def _row_to_checkpoint(self, row: sqlite3.Row) -> SessionCheckpoint:
        metadata = json.loads(row["metadata_json"]) if row["metadata_json"] else {}
        return SessionCheckpoint(
            id=int(row["id"]),
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            checkpoint_id=row["checkpoint_id"],
            label=row["label"],
            description=row["description"],
            created_at=int(row["created_at"]),
            metadata=metadata,
        )

    # ====================================================================
    # Phase 6 V1 MACC 扩展（3 张新表 + compression_log）
    # ====================================================================

    # ---- semantic_rules CRUD ----------------------------------------------

    def upsert_semantic_rule(
        self,
        pattern: str,
        rule_text: str,
        *,
        session_id: str = "default",
        confidence: float = 0.0,
        rule_id: str | None = None,
        source_event_ids: list[str] | None = None,
    ) -> str:
        """插 / 更新语义规则。

        同 pattern + 同 rule_text → 视为同一规则，confidence 累加；
        否则新插一行。
        Returns: rule id
        """
        rule_id = rule_id or str(uuid.uuid4())
        now = now_ms()
        source_json = json.dumps(list(source_event_ids or []), ensure_ascii=False)
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id, confidence FROM semantic_rules "
                "WHERE pattern = ? AND rule_text = ? LIMIT 1",
                (pattern, rule_text),
            ).fetchone()
            if existing:
                # 同 pattern + 同 rule_text → 累加 confidence（封顶 1.0）
                new_conf = min(1.0, float(existing["confidence"]) + confidence)
                conn.execute(
                    "UPDATE semantic_rules "
                    "SET session_id = ?, confidence = ?, last_updated = ?, source_event_ids_json = ? "
                    "WHERE id = ?",
                    (session_id, new_conf, now, source_json, existing["id"]),
                )
                return str(existing["id"])
            # 新插入
            conn.execute(
                """
                INSERT INTO semantic_rules
                  (id, session_id, pattern, rule_text, confidence, last_updated, source_event_ids_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (rule_id, session_id, pattern, rule_text,
                 float(confidence), now, source_json),
            )
        return rule_id

    def list_semantic_rules(
        self,
        *,
        pattern: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 50,
    ) -> list[dict]:
        """查询语义规则（按 pattern 过滤 + confidence 阈值）。"""
        sql = "SELECT * FROM semantic_rules WHERE confidence >= ?"
        params: list = [float(min_confidence)]
        if pattern:
            sql += " AND pattern LIKE ?"
            params.append(f"%{pattern}%")
        sql += " ORDER BY confidence DESC, last_updated DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "pattern": r["pattern"],
                "rule_text": r["rule_text"],
                "confidence": float(r["confidence"]),
                "last_updated": int(r["last_updated"]),
                "source_event_ids": json.loads(r["source_event_ids_json"] or "[]"),
            }
            for r in rows
        ]

    def delete_semantic_rule(self, rule_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM semantic_rules WHERE id = ?", (rule_id,),
            )
        return cur.rowcount > 0

    # ---- event_graph_nodes CRUD -------------------------------------------

    def insert_event_node(
        self,
        session_id: str,
        entity: str,
        action: str,
        *,
        result: str = "",
        status: str = "ok",
        metadata: dict | None = None,
        node_id: str | None = None,
    ) -> str:
        """插事件节点；返回 node id。"""
        node_id = node_id or str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO event_graph_nodes
                  (id, session_id, entity, action, result, status, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id, session_id, entity, action, result, status,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now_ms(),
                ),
            )
        return node_id

    def insert_event_edge(
        self,
        session_id: str,
        from_node: str,
        to_node: str,
        *,
        relation: str = "next",
        metadata: dict | None = None,
    ) -> int:
        """插事件边；返回 edge id。"""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO event_graph_edges
                  (session_id, from_node, to_node, relation, metadata_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    session_id, from_node, to_node, relation,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
        return int(cur.lastrowid or 0)

    def list_event_nodes(
        self,
        session_id: str,
        *,
        entity: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        sql = "SELECT * FROM event_graph_nodes WHERE session_id = ?"
        params: list = [session_id]
        if entity:
            sql += " AND entity = ?"
            params.append(entity)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "entity": r["entity"],
                "action": r["action"],
                "result": r["result"],
                "status": r["status"],
                "metadata": json.loads(r["metadata_json"] or "{}"),
                "created_at": int(r["created_at"]),
            }
            for r in rows
        ]

    def list_event_edges(self, session_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM event_graph_edges WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "from_node": r["from_node"],
                "to_node": r["to_node"],
                "relation": r["relation"],
                "metadata": json.loads(r["metadata_json"] or "{}"),
            }
            for r in rows
        ]

    # ---- BFS 路径扩展（设计 §2.2 recall_episode）-----------------------

    def bfs_recall_episode(
        self,
        session_id: str,
        *,
        seed_entities: list[str],
        max_hops: int = 2,
        max_nodes: int = 10,
    ) -> list[dict]:
        """从 seed_entities 出发 BFS 扩展节点（最多 max_hops 跳）。

        步骤：
            1. 找 session 内 entity IN seed_entities 的节点作为种子
            2. 沿 outgoing / incoming edges 各扩展 1 跳；重复直到 max_hops
            3. 返回排序后的节点列表（按 created_at DESC）
        """
        if not seed_entities:
            return []
        # 1. 种子节点
        placeholders = ",".join("?" for _ in seed_entities)
        with self._connect() as conn:
            seed_rows = conn.execute(
                f"SELECT * FROM event_graph_nodes WHERE session_id = ? "
                f"AND entity IN ({placeholders}) ORDER BY created_at DESC",
                [session_id, *seed_entities],
            ).fetchall()
        if not seed_rows:
            return []
        visited: set[str] = set()
        nodes: dict[str, dict] = {}
        for r in seed_rows:
            nid = str(r["id"])
            if nid in visited:
                continue
            visited.add(nid)
            nodes[nid] = {
                "id": nid,
                "entity": r["entity"],
                "action": r["action"],
                "result": r["result"],
                "status": r["status"],
                "metadata": json.loads(r["metadata_json"] or "{}"),
                "created_at": int(r["created_at"]),
                "hops": 0,
            }
        # 2. BFS 扩展
        frontier = [str(r["id"]) for r in seed_rows]
        with self._connect() as conn:
            for hop in range(1, max_hops + 1):
                if not frontier or len(nodes) >= max_nodes:
                    break
                next_frontier: list[str] = []
                ph = ",".join("?" for _ in frontier)
                # outgoing edges
                out_rows = conn.execute(
                    f"SELECT to_node FROM event_graph_edges "
                    f"WHERE session_id = ? AND from_node IN ({ph})",
                    [session_id, *frontier],
                ).fetchall()
                next_frontier.extend(r["to_node"] for r in out_rows)
                # incoming edges
                in_rows = conn.execute(
                    f"SELECT from_node FROM event_graph_edges "
                    f"WHERE session_id = ? AND to_node IN ({ph})",
                    [session_id, *frontier],
                ).fetchall()
                next_frontier.extend(r["from_node"] for r in in_rows)
                # 去重
                next_frontier = list(dict.fromkeys(next_frontier))
                for nid in next_frontier:
                    if nid in visited or len(nodes) >= max_nodes:
                        continue
                    r = conn.execute(
                        "SELECT * FROM event_graph_nodes WHERE id = ?",
                        (nid,),
                    ).fetchone()
                    if r is None:
                        continue
                    visited.add(nid)
                    nodes[nid] = {
                        "id": nid,
                        "entity": r["entity"],
                        "action": r["action"],
                        "result": r["result"],
                        "status": r["status"],
                        "metadata": json.loads(r["metadata_json"] or "{}"),
                        "created_at": int(r["created_at"]),
                        "hops": hop,
                    }
                frontier = next_frontier
        # 按 hops 升序 + created_at 降序
        return sorted(
            nodes.values(), key=lambda n: (n["hops"], -n["created_at"]),
        )[:max_nodes]

    # ---- compression_log CRUD ---------------------------------------------

    def log_compression(
        self,
        session_id: str,
        strategy: str,
        before_tokens: int,
        after_tokens: int,
        layers_used: list[str],
        elapsed_ms: int,
    ) -> int:
        ratio = (after_tokens / max(1, before_tokens)) if before_tokens > 0 else 1.0
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO compression_log
                  (session_id, strategy, before_tokens, after_tokens,
                   compression_ratio, layers_used_json, elapsed_ms, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id, strategy, int(before_tokens), int(after_tokens),
                    float(ratio), json.dumps(layers_used, ensure_ascii=False),
                    int(elapsed_ms), now_ms(),
                ),
            )
        return int(cur.lastrowid or 0)

    def list_compression_log(self, session_id: str, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM compression_log WHERE session_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (session_id, int(limit)),
            ).fetchall()
        return [
            {
                "id": int(r["id"]),
                "strategy": r["strategy"],
                "before_tokens": int(r["before_tokens"]),
                "after_tokens": int(r["after_tokens"]),
                "compression_ratio": float(r["compression_ratio"]),
                "layers_used": json.loads(r["layers_used_json"] or "[]"),
                "elapsed_ms": int(r["elapsed_ms"]),
                "created_at": int(r["created_at"]),
            }
            for r in rows
        ]

    # ====================================================================
    # Phase 6 V1.5：FTS5 全文搜索 + 分支 + 共享权限 + SessionEvent 哈希链
    # ====================================================================

    # ---- FTS5 全文搜索 -----------------------------------------------------

    def fts_search(
        self,
        query: str,
        *,
        project_name: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """FTS5 全文搜索：标题 + 消息内容 + 工具名 + 工具结果。

        Args:
            query: FTS5 MATCH 表达式（支持双引号短语 + AND/OR/NOT）
            project_name: 可选项目过滤
            limit: 返回条数上限

        Returns:
            按 bm25 相关度排序的命中列表（含 session_id + created_at + snippet）
        """
        query = (query or "").strip()
        if not query:
            return []
        # FTS5 MATCH 表达式：包一层双引号防注入（用户提供的词作为字面 token）
        # 注：unicode61 tokenize 已经过滤多数特殊字符
        fts_query = '"' + query.replace('"', '""') + '"'
        sql = (
            "SELECT fts.session_id, fts.created_at, fts.title, fts.content, "
            "       fts.tool_name, fts.tool_result, "
            "       snippet(sessions_fts, 2, '[', ']', '...', 16) AS content_snippet, "
            "       bm25(sessions_fts) AS relevance "
            "FROM sessions_fts fts "
            "LEFT JOIN sessions s ON s.id = fts.session_id "
            "WHERE sessions_fts MATCH ? "
        )
        params: list = [fts_query]
        if project_name:
            sql += "AND s.project_name = ? "
            params.append(project_name)
        sql += "ORDER BY relevance LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                # FTS5 syntax 错误（罕见）→ 返空
                return []
        return [
            {
                "session_id": r["session_id"],
                "created_at": int(r["created_at"]),
                "title": r["title"],
                "content_snippet": r["content_snippet"] or "",
                "tool_name": r["tool_name"] or "",
                "tool_result": r["tool_result"] or "",
                "relevance": float(r["relevance"]) if r["relevance"] is not None else 0.0,
            }
            for r in rows
        ]

    # ---- 分支 -------------------------------------------------------------

    def create_branch(
        self,
        parent_session_id: str,
        branch_label: str,
        from_checkpoint_id: str | None = None,
        title_suffix: str = " (分支)",
    ) -> Session:
        """从父会话创建分支会话。

        分支会话与父会话共享 messages 不可（物理隔离）；
        通过 parent_session_id 字段表达派生关系，UI 时间线渲染。

        Args:
            parent_session_id: 父会话 UUID
            branch_label: 分支标签（"bugfix-order-amount"）
            from_checkpoint_id: 从父会话哪个 checkpoint 派生；None = 末尾
            title_suffix: 分支标题后缀

        Returns:
            新创建的 Session（status='active', parent_session_id=<父>）
        """
        parent = self.get_session(parent_session_id)
        if parent is None:
            raise ValueError(f"parent session {parent_session_id} not found")
        new_id = str(uuid.uuid4())
        ts = now_ms()
        branch_title = parent.title + title_suffix
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions(
                    id, title, owner, project_name, status,
                    created_at, updated_at, thread_id, metadata_json,
                    parent_session_id, branch_from_checkpoint_id, branch_label
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id, branch_title, parent.owner, parent.project_name,
                    ts, ts, new_id, json.dumps(parent.metadata, ensure_ascii=False),
                    parent_session_id, from_checkpoint_id, branch_label,
                ),
            )
        # V1.5 哈希链：branched 事件
        try:
            self._append_event(
                new_id, "branched",
                {
                    "parent_session_id": parent_session_id,
                    "from_checkpoint_id": from_checkpoint_id,
                    "branch_label": branch_label,
                },
                actor="system",
            )
        except Exception:
            pass  # 哈希链失败不阻塞主流程
        return Session(
            id=new_id, title=branch_title, owner=parent.owner,
            project_name=parent.project_name, status="active",
            created_at=ts, updated_at=ts, thread_id=new_id,
            metadata=parent.metadata,
            parent_session_id=parent_session_id,
            branch_from_checkpoint_id=from_checkpoint_id,
            branch_label=branch_label,
        )

    def list_branches(self, parent_session_id: str) -> list[Session]:
        """列出某父会话的所有分支会话。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE parent_session_id = ? "
                "ORDER BY created_at DESC",
                (parent_session_id,),
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    # ---- 共享权限矩阵（owner / read / write）------------------------------

    def add_share_token(
        self,
        session_id: str,
        permission: SharePermission = "read",
        expires_at: int | None = None,
        actor: str = "system",
    ) -> ShareToken:
        """为会话新增一个分享令牌（写入 share_tokens_json 数组）。

        Returns:
            新创建的 ShareToken（带 token uuid hex 字符串）
        """
        sess = self.get_session(session_id)
        if sess is None:
            raise ValueError(f"session {session_id} not found")
        token = uuid.uuid4().hex
        ts = now_ms()
        entry = {
            "token": token,
            "permission": permission,
            "created_at": ts,
        }
        if expires_at is not None and int(expires_at) > 0:
            entry["expires_at"] = int(expires_at)
        tokens = list(sess.share_tokens)
        tokens.append(entry)
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET share_tokens_json = ?, shared_at = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    json.dumps(tokens, ensure_ascii=False),
                    ts,
                    ts,
                    session_id,
                ),
            )
        # V1.5 哈希链：shared 事件
        try:
            self._append_event(
                session_id, "shared",
                {
                    "permission": permission,
                    "expires_at": expires_at,
                    "token_prefix": token[:8],  # 仅记前缀，不记完整 token
                },
                actor=actor,
            )
        except Exception:
            pass
        return ShareToken(
            token=token, permission=permission,
            created_at=ts, expires_at=expires_at,
        )

    def revoke_share_token(
        self,
        session_id: str,
        token: str,
        actor: str = "system",
    ) -> bool:
        """撤销分享令牌（从 share_tokens_json 数组中移除）。"""
        sess = self.get_session(session_id)
        if sess is None:
            return False
        tokens = [t for t in sess.share_tokens if t.get("token") != token]
        if len(tokens) == len(sess.share_tokens):
            return False  # 没找到
        ts = now_ms()
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET share_tokens_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(tokens, ensure_ascii=False), ts, session_id),
            )
        try:
            self._append_event(
                session_id, "shared",
                {"action": "revoked", "token_prefix": token[:8]},
                actor=actor,
            )
        except Exception:
            pass
        return True

    def check_access(
        self,
        session_id: str,
        actor: str,
        required: SharePermission,
    ) -> bool:
        """检查 actor 对 session 是否拥有 required 权限。

        规则：
            1. owner 字段 == actor → True（全权，含 write）
            2. permissions_json[actor] == required 或更高级（write > read）→ True
            3. 否则 → False

        Args:
            session_id: 会话 UUID
            actor: 用户名（V0 简化为字符串；V1 接 Phase 10 IAM）
            required: 'read' 或 'write'
        """
        sess = self.get_session(session_id)
        if sess is None:
            return False
        if sess.owner == actor:
            return True
        actor_perm = sess.permissions.get(actor)
        if actor_perm is None:
            return False
        if required == "read":
            return actor_perm in ("read", "write")
        if required == "write":
            return actor_perm == "write"
        return False

    def grant_permission(
        self,
        session_id: str,
        actor: str,
        permission: SharePermission,
        granter: str = "system",
    ) -> bool:
        """授予 actor 对 session 的权限（写入 permissions_json）。

        注意：granter 必须是 session.owner，否则 False（防越权）。
        """
        sess = self.get_session(session_id)
        if sess is None:
            return False
        if sess.owner != granter:
            return False  # 只有 owner 能授权
        perms = dict(sess.permissions)
        perms[actor] = permission
        ts = now_ms()
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET permissions_json = ?, updated_at = ? WHERE id = ?",
                (json.dumps(perms, ensure_ascii=False), ts, session_id),
            )
        try:
            self._append_event(
                session_id, "shared",
                {"action": "granted", "actor": actor, "permission": permission},
                actor=granter,
            )
        except Exception:
            pass
        return True

    # ---- SessionEvent 哈希链（SHA-256 链式防篡改）------------------------

    @staticmethod
    def _compute_event_hash(prev_hash: str, event_type: str, payload_json: str, created_at: int) -> str:
        """计算单条事件的 SHA-256 hash。

        hash = SHA256(prev_hash + '|' + event_type + '|' + payload_json + '|' + created_at)
        任意字段被篡改 → hash 与链上记录不符 → verify_chain 返回 False。
        """
        raw = f"{prev_hash}|{event_type}|{payload_json}|{created_at}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _append_event(
        self,
        session_id: str,
        event_type: SessionEventType,
        payload: dict,
        *,
        actor: str = "system",
    ) -> int:
        """内部：追加一条 SessionEvent，自动计算 prev_hash + hash。"""
        ts = now_ms()
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            # 1. 取上一条 hash（按 id DESC）
            row = conn.execute(
                "SELECT hash FROM session_event_chain WHERE session_id = ? "
                "ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            prev_hash = row["hash"] if row else ("0" * 64)
            # 2. 算新 hash
            new_hash = self._compute_event_hash(prev_hash, event_type, payload_json, ts)
            # 3. 插入
            cur = conn.execute(
                """
                INSERT INTO session_event_chain
                  (session_id, event_type, payload_json, prev_hash, hash, actor, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, event_type, payload_json, prev_hash, new_hash, actor, ts),
            )
        return int(cur.lastrowid or 0)

    def append_event(
        self,
        session_id: str,
        event_type: SessionEventType,
        payload: dict,
        *,
        actor: str = "system",
    ) -> SessionEvent:
        """公开 API：追加一条 SessionEvent，返回事件对象。

        哈希链断点：session_id 不存在 → ValueError。
        """
        sess = self.get_session(session_id)
        if sess is None:
            raise ValueError(f"session {session_id} not found")
        event_id = self._append_event(session_id, event_type, payload, actor=actor)
        return SessionEvent(
            id=event_id, session_id=session_id,
            event_type=event_type, payload=payload,
            prev_hash="", hash="",  # 内部不返回 hash → 调用 verify_chain 验证
            actor=actor, created_at=now_ms(),
        )

    def list_event_chain(self, session_id: str, limit: int = 200) -> list[SessionEvent]:
        """列出会话的全部 SessionEvent（按 id ASC）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM session_event_chain WHERE session_id = ? "
                "ORDER BY id ASC LIMIT ?",
                (session_id, int(limit)),
            ).fetchall()
        return [
            SessionEvent(
                id=int(r["id"]),
                session_id=r["session_id"],
                event_type=r["event_type"],
                payload=json.loads(r["payload_json"] or "{}"),
                prev_hash=r["prev_hash"],
                hash=r["hash"],
                actor=r["actor"],
                created_at=int(r["created_at"]),
            )
            for r in rows
        ]

    def verify_event_chain(self, session_id: str) -> dict:
        """验证会话的 SessionEvent 哈希链完整性。

        Returns:
            {'valid': bool, 'total': int, 'broken_at_id': int | None,
             'broken_reason': str | None}
        """
        events = self.list_event_chain(session_id, limit=10_000)
        if not events:
            return {"valid": True, "total": 0, "broken_at_id": None, "broken_reason": None}
        expected_prev = "0" * 64
        for ev in events:
            # 1. prev_hash 与上一条对齐
            if ev.prev_hash != expected_prev:
                return {
                    "valid": False,
                    "total": len(events),
                    "broken_at_id": ev.id,
                    "broken_reason": f"prev_hash mismatch (expected {expected_prev[:8]}..., got {ev.prev_hash[:8]}...)",
                }
            # 2. 重算 hash 与记录的 hash 对齐
            payload_json = json.dumps(ev.payload, ensure_ascii=False, sort_keys=True)
            recomputed = self._compute_event_hash(
                ev.prev_hash, ev.event_type, payload_json, ev.created_at,
            )
            if recomputed != ev.hash:
                return {
                    "valid": False,
                    "total": len(events),
                    "broken_at_id": ev.id,
                    "broken_reason": f"hash mismatch (recomputed {recomputed[:8]}... vs stored {ev.hash[:8]}...)",
                }
            expected_prev = ev.hash
        return {
            "valid": True,
            "total": len(events),
            "broken_at_id": None,
            "broken_reason": None,
        }

    # ---- 会话级统计 -------------------------------------------------------

    def get_session_stats(self, session_id: str) -> dict:
        """返回会话统计快照（消息数 + checkpoint 数 + 事件链长度 + 压缩日志数 + 分支数）。

        前端 SessionsPanel 卡片 / AuditDashboard 联动展示。
        """
        sess = self.get_session(session_id)
        if sess is None:
            raise ValueError(f"session {session_id} not found")
        with self._connect() as conn:
            message_count = conn.execute(
                "SELECT COUNT(*) AS c FROM session_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()["c"]
            checkpoint_count = conn.execute(
                "SELECT COUNT(*) AS c FROM session_checkpoints WHERE session_id = ?",
                (session_id,),
            ).fetchone()["c"]
            event_count = conn.execute(
                "SELECT COUNT(*) AS c FROM session_event_chain WHERE session_id = ?",
                (session_id,),
            ).fetchone()["c"]
            compression_count = conn.execute(
                "SELECT COUNT(*) AS c FROM compression_log WHERE session_id = ?",
                (session_id,),
            ).fetchone()["c"]
            branch_count = conn.execute(
                "SELECT COUNT(*) AS c FROM sessions WHERE parent_session_id = ?",
                (session_id,),
            ).fetchone()["c"]
        return {
            "session_id": session_id,
            "title": sess.title,
            "owner": sess.owner,
            "status": sess.status,
            "is_branch": sess.parent_session_id is not None,
            "parent_session_id": sess.parent_session_id,
            "branch_label": sess.branch_label,
            "message_count": int(message_count),
            "checkpoint_count": int(checkpoint_count),
            "event_chain_count": int(event_count),
            "compression_count": int(compression_count),
            "branch_count": int(branch_count),
            "created_at": sess.created_at,
            "updated_at": sess.updated_at,
        }

    # ---- 启动恢复扫描 -----------------------------------------------------

    def find_resumable_sessions(self, *, idle_threshold_ms: int = 300_000) -> list[Session]:
        """扫描可恢复会话（updated_at 在阈值外 + 仍有最近消息 + 非分支/archived）。

        用途：app 启动时检测中断会话 → RecoveryPanel 弹窗提示恢复。

        Args:
            idle_threshold_ms: 多久未更新算"中断"。默认 5 分钟。
        """
        cutoff = now_ms() - int(idle_threshold_ms)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT s.* FROM sessions s "
                "WHERE s.status = 'active' "
                "  AND s.parent_session_id IS NULL "  # 仅根会话（非分支）
                "  AND s.updated_at < ? "             # 已超过空闲阈值
                "  AND EXISTS (SELECT 1 FROM session_messages m "
                "              WHERE m.session_id = s.id) "  # 至少 1 条消息
                "ORDER BY s.updated_at DESC LIMIT 50",
                (cutoff,),
            ).fetchall()
        return [self._row_to_session(r) for r in rows]