"""WorkspaceIndexer — 全量/增量扫描 + AST 符号提取 + 写 SQLite。

三种语言的 query（基于 tree-sitter AST node type）：

Java:
  class_declaration       → class (name from child_by_field_name('name'))
    method_declaration    → method (parent_class=class.name)
  interface_declaration   → interface
  enum_declaration        → enum

Python:
  class_definition        → class (name from child_by_field_name('name'))
    function_definition   → method (parent_class=class.name)
  function_definition     → function (顶层)

TypeScript / JavaScript:
  class_declaration       → class
    method_definition     → method
    public_field_definition → field
  function_declaration    → function
  interface_declaration   → interface
  enum_declaration        → enum
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path

from tree_sitter import Node

from agent.codenav.language_registry import (
    get_parser_for_file,
    get_supported_extensions,
)
from agent.codenav.models import IndexStatus, Symbol

logger = logging.getLogger(__name__)

# 跳过目录（性能 + 噪音）
_IGNORE_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        "target",
        "dist",
        "build",
        ".venv",
        "venv",
        ".idea",
        ".vscode",
        ".next",
        ".gradle",
        ".pytest_cache",
    }
)

# 单文件大小上限（默认 1MB；超过认为是大文件不索引）
_MAX_FILE_BYTES = 1024 * 1024


def _read_schema_sql() -> str:
    schema_path = Path(__file__).parent / "schema.sql"
    return schema_path.read_text(encoding="utf-8")


class WorkspaceIndexer:
    """索引器：扫描目录 → AST 解析 → 提取符号 → 写 SQLite。

    单库单进程，不并发。watcher.py 在另一个进程中调 incremental_update。
    """

    def __init__(self, db_path: str | os.PathLike, root_paths: list[str | os.PathLike]):
        self._db_path = str(db_path)
        self._root_paths = [Path(p).resolve() for p in root_paths]
        self._init_db()
        self._is_scanning = False

    def _init_db(self) -> None:
        # 防 Windows 上「目录已存在为文件」的冲突：parent 存在但不是目录 → 删掉重建
        parent = Path(self._db_path).parent
        if parent.exists() and not parent.is_dir():
            parent.unlink()
        parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            try:
                conn.executescript(_read_schema_sql())
            except sqlite3.IntegrityError:
                # 旧库可能有重复行，先清洗再建索引
                conn.execute(
                    "DELETE FROM symbols WHERE id NOT IN ("
                    "  SELECT MIN(id) FROM symbols GROUP BY file_path, name, start_line"
                    ")"
                )
                conn.commit()
                conn.executescript(_read_schema_sql())

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    # ------------------------------------------------------------------ public

    async def full_scan(self) -> IndexStatus:
        """全量扫描 root_paths 下所有支持文件。"""
        if self._is_scanning:
            return self.get_status()
        self._is_scanning = True
        time.time()
        try:
            symbols: list[Symbol] = []
            files_scanned: set[str] = set()
            for root in self._root_paths:
                if not root.exists():
                    logger.warning("full_scan: root not found: %s", root)
                    continue
                for file_path in self._iter_files(root):
                    try:
                        file_symbols = self.extract_symbols(str(file_path))
                    except Exception as e:
                        logger.warning("extract_symbols failed %s: %s", file_path, e)
                        continue
                    symbols.extend(file_symbols)
                    files_scanned.add(str(file_path.resolve()))
            self._upsert_symbols(symbols)
            # 清理已删除的文件符号（本次未扫描到的文件 → 删除其所有符号）
            if files_scanned:
                with self._connect() as conn:
                    # 收集当前 DB 中所有 file_path
                    existing = {
                        r[0]
                        for r in conn.execute("SELECT DISTINCT file_path FROM symbols").fetchall()
                    }
                    stale = existing - files_scanned
                    for fp in stale:
                        conn.execute("DELETE FROM symbols WHERE file_path = ?", (fp,))
            return IndexStatus(
                total_files=len(files_scanned),
                total_symbols=len(symbols),
                last_full_scan=time.time(),
                last_incremental=None,
                is_scanning=False,
            )
        finally:
            self._is_scanning = False

    async def incremental_update(self, changed_files: list[str]) -> IndexStatus:
        """对变更文件做增量 AST 解析 + SQLite upsert。

        changed_files: 绝对路径列表（包含修改/新建）。
        """
        started = time.time()
        upserts: list[Symbol] = []
        for fp in changed_files:
            path = Path(fp)
            if not path.exists():
                # 文件被删：清理
                self.delete_file_symbols(fp)
                continue
            try:
                upserts.extend(self.extract_symbols(str(path)))
            except Exception as e:
                logger.warning("incremental_update extract failed %s: %s", fp, e)
                continue
        if upserts:
            self._upsert_symbols(upserts)
        return IndexStatus(
            total_files=self._count_distinct_files(),
            total_symbols=self._count_total_symbols(),
            last_full_scan=None,
            last_incremental=started,
            is_scanning=False,
        )

    def get_status(self) -> IndexStatus:
        """返回当前索引状态统计。"""
        return IndexStatus(
            total_files=self._count_distinct_files(),
            total_symbols=self._count_total_symbols(),
            last_full_scan=None,
            last_incremental=None,
            is_scanning=self._is_scanning,
        )

    def delete_file_symbols(self, file_path: str) -> None:
        """删除指定文件的所有符号（文件被删时调用）。"""
        with self._connect() as conn:
            conn.execute("DELETE FROM symbols WHERE file_path = ?", (file_path,))

    def extract_symbols(self, file_path: str, language: str | None = None) -> list[Symbol]:
        """对单个文件执行 AST 解析，提取所有符号。"""
        path = Path(file_path)
        if not path.exists():
            return []
        if path.stat().st_size > _MAX_FILE_BYTES:
            logger.debug("skip large file: %s (%d bytes)", file_path, path.stat().st_size)
            return []

        if language:
            # 测试用：直接给定语言
            from agent.codenav.language_registry import _LANGUAGE_BUILDERS, _build_parser

            ext = next((e for e, (_, lid) in _LANGUAGE_BUILDERS.items() if lid == language), None)
            if not ext:
                return []
            parser = _build_parser(ext)
            if not parser:
                return []
        else:
            result = get_parser_for_file(file_path)
            if not result:
                return []
            parser, language = result

        try:
            source = path.read_bytes()
        except (OSError, UnicodeDecodeError):
            return []
        tree = parser.parse(source)
        last_mtime = int(path.stat().st_mtime * 1000)
        return _extract_from_tree(tree.root_node, source, str(path.resolve()), language, last_mtime)

    # ------------------------------------------------------------------ private

    def _iter_files(self, root: Path):
        for dirpath, dirnames, filenames in os.walk(root):
            # 修剪忽略目录
            dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS]
            for name in filenames:
                if Path(name).suffix.lower() in get_supported_extensions():
                    yield Path(dirpath) / name

    def _upsert_symbols(self, symbols: list[Symbol]) -> None:
        """按 (file_path, name, start_line) upsert；同文件同名同位置视为同一符号。

        注意：Symbol dataclass 当前没有 last_modified 字段；用 file_path 的 mtime 计算。
        """
        if not symbols:
            return
        # 按 file_path 分组 → 取该文件 mtime
        file_mtime: dict[str, int] = {}
        for s in symbols:
            if s.file_path in file_mtime:
                continue
            try:
                file_mtime[s.file_path] = int(os.stat(s.file_path).st_mtime * 1000)
            except OSError:
                file_mtime[s.file_path] = 0
        with self._connect() as conn:
            for s in symbols:
                mtime = file_mtime.get(s.file_path, 0)
                conn.execute(
                    """
                    INSERT INTO symbols
                      (name, kind, file_path, start_line, end_line, signature, parent_class, language, last_modified)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(file_path, name, start_line) DO UPDATE SET
                      kind=excluded.kind,
                      end_line=excluded.end_line,
                      signature=excluded.signature,
                      parent_class=excluded.parent_class,
                      language=excluded.language,
                      last_modified=excluded.last_modified
                    """,
                    (
                        s.name,
                        s.kind,
                        s.file_path,
                        s.start_line,
                        s.end_line,
                        s.signature,
                        s.parent_class,
                        s.language,
                        mtime,
                    ),
                )

    def _count_distinct_files(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(DISTINCT file_path) FROM symbols").fetchone()
            return row[0] if row else 0

    def _count_total_symbols(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()
            return row[0] if row else 0


# ============================================================================
# Tree-sitter 节点 → Symbol
# ============================================================================


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _find_ancestor(node: Node, type_name: str) -> Node | None:
    cur = node.parent
    while cur:
        if cur.type == type_name:
            return cur
        cur = cur.parent
    return None


def _make_symbol(
    name: str,
    kind: str,
    node: Node,
    file_path: str,
    language: str,
    last_mtime: int,
    signature: str | None = None,
    parent_class: str | None = None,
) -> Symbol:
    return Symbol(
        name=name,
        kind=kind,
        file_path=file_path,
        start_line=node.start_point[0] + 1,  # 1-indexed
        end_line=node.end_point[0] + 1,
        signature=signature,
        parent_class=parent_class,
        language=language,
    )


def _extract_from_tree(
    root: Node, source: bytes, file_path: str, language: str, last_mtime: int
) -> list[Symbol]:
    """分发到具体语言的 extractor。"""
    if language == "java":
        return _extract_java(root, source, file_path, language, last_mtime)
    if language == "python":
        return _extract_python(root, source, file_path, language, last_mtime)
    if language in ("typescript", "javascript"):
        return _extract_typescript(root, source, file_path, language, last_mtime)
    return []


def _extract_java(
    root: Node, source: bytes, file_path: str, language: str, last_mtime: int
) -> list[Symbol]:
    out: list[Symbol] = []
    # 顶层 class/interface/enum
    for child in root.children:
        if child.type == "class_declaration":
            name_node = child.child_by_field_name("name")
            if not name_node:
                continue
            cls_name = _node_text(name_node, source)
            out.append(
                _make_symbol(
                    cls_name,
                    "class",
                    child,
                    file_path,
                    language,
                    last_mtime,
                    signature=_node_text(child, source).split("{")[0].strip(),
                )
            )
            # 方法
            body = child.child_by_field_name("body")
            if body:
                for member in body.children:
                    if member.type == "method_declaration":
                        mn = member.child_by_field_name("name")
                        if not mn:
                            continue
                        out.append(
                            _make_symbol(
                                _node_text(mn, source),
                                "method",
                                member,
                                file_path,
                                language,
                                last_mtime,
                                signature=_node_text(member, source).split("{")[0].strip(),
                                parent_class=cls_name,
                            )
                        )
                    elif member.type == "field_declaration":
                        # 字段可能有多个 declarator；取第一个名字
                        for sub in member.children:
                            if sub.type == "variable_declarator":
                                vn = sub.child_by_field_name("name")
                                if vn:
                                    out.append(
                                        _make_symbol(
                                            _node_text(vn, source),
                                            "field",
                                            sub,
                                            file_path,
                                            language,
                                            last_mtime,
                                            parent_class=cls_name,
                                        )
                                    )
                                    break
        elif child.type == "interface_declaration":
            name_node = child.child_by_field_name("name")
            if name_node:
                out.append(
                    _make_symbol(
                        _node_text(name_node, source),
                        "interface",
                        child,
                        file_path,
                        language,
                        last_mtime,
                        signature=_node_text(child, source).split("{")[0].strip(),
                    )
                )
        elif child.type == "enum_declaration":
            name_node = child.child_by_field_name("name")
            if name_node:
                out.append(
                    _make_symbol(
                        _node_text(name_node, source),
                        "enum",
                        child,
                        file_path,
                        language,
                        last_mtime,
                    )
                )
    return out


def _extract_python(
    root: Node, source: bytes, file_path: str, language: str, last_mtime: int
) -> list[Symbol]:
    out: list[Symbol] = []
    for child in root.children:
        if child.type == "class_definition":
            name_node = child.child_by_field_name("name")
            if not name_node:
                continue
            cls_name = _node_text(name_node, source)
            out.append(
                _make_symbol(
                    cls_name,
                    "class",
                    child,
                    file_path,
                    language,
                    last_mtime,
                    signature=_node_text(child, source).split(":")[0].strip(),
                )
            )
            body = child.child_by_field_name("body")
            if body:
                for member in body.children:
                    if member.type == "function_definition":
                        mn = member.child_by_field_name("name")
                        if not mn:
                            continue
                        out.append(
                            _make_symbol(
                                _node_text(mn, source),
                                "method",
                                member,
                                file_path,
                                language,
                                last_mtime,
                                signature=_node_text(member, source).split(":")[0].strip(),
                                parent_class=cls_name,
                            )
                        )
        elif child.type == "function_definition":
            name_node = child.child_by_field_name("name")
            if name_node:
                out.append(
                    _make_symbol(
                        _node_text(name_node, source),
                        "function",
                        child,
                        file_path,
                        language,
                        last_mtime,
                        signature=_node_text(child, source).split(":")[0].strip(),
                    )
                )
    return out


def _extract_typescript(
    root: Node, source: bytes, file_path: str, language: str, last_mtime: int
) -> list[Symbol]:
    out: list[Symbol] = []
    # 顶层 children；export_statement 包住 declaration 时穿透一层
    for child in root.children:
        # 解包 export / export_default
        inner = child
        if child.type in ("export_statement", "export_default_statement"):
            for sub in child.children:
                if sub.type in (
                    "class_declaration",
                    "function_declaration",
                    "interface_declaration",
                    "enum_declaration",
                ):
                    inner = sub
                    break
            else:
                continue  # 没识别到内部声明，跳过
        if inner.type == "class_declaration":
            name_node = inner.child_by_field_name("name")
            if not name_node:
                continue
            cls_name = _node_text(name_node, source)
            out.append(
                _make_symbol(
                    cls_name,
                    "class",
                    inner,
                    file_path,
                    language,
                    last_mtime,
                    signature=_node_text(inner, source).split("{")[0].strip(),
                )
            )
            body = inner.child_by_field_name("body")
            if body:
                for member in body.children:
                    if member.type == "method_definition":
                        mn = member.child_by_field_name("name")
                        if not mn:
                            continue
                        out.append(
                            _make_symbol(
                                _node_text(mn, source),
                                "method",
                                member,
                                file_path,
                                language,
                                last_mtime,
                                signature=_node_text(member, source).split("{")[0].strip(),
                                parent_class=cls_name,
                            )
                        )
                    elif member.type in ("public_field_definition", "field_declaration"):
                        mn = member.child_by_field_name("name")
                        if mn:
                            out.append(
                                _make_symbol(
                                    _node_text(mn, source),
                                    "field",
                                    member,
                                    file_path,
                                    language,
                                    last_mtime,
                                    parent_class=cls_name,
                                )
                            )
        elif inner.type == "function_declaration":
            name_node = inner.child_by_field_name("name")
            if name_node:
                out.append(
                    _make_symbol(
                        _node_text(name_node, source),
                        "function",
                        inner,
                        file_path,
                        language,
                        last_mtime,
                        signature=_node_text(inner, source).split("{")[0].strip(),
                    )
                )
        elif inner.type == "interface_declaration":
            name_node = inner.child_by_field_name("name")
            if name_node:
                out.append(
                    _make_symbol(
                        _node_text(name_node, source),
                        "interface",
                        inner,
                        file_path,
                        language,
                        last_mtime,
                    )
                )
        elif inner.type == "enum_declaration":
            name_node = inner.child_by_field_name("name")
            if name_node:
                out.append(
                    _make_symbol(
                        _node_text(name_node, source),
                        "enum",
                        inner,
                        file_path,
                        language,
                        last_mtime,
                    )
                )
    return out
