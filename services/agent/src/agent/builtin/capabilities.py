"""Phase 1B V4 · 内部能力模块只读工具入口（2026-08-04）。

把低耦合、无 LLM 依赖的内部能力暴露给动态工具循环：

    - symbol_search      代码符号检索（codenav SQLite 符号库，< 50ms）
    - file_symbols       列出文件全部符号（codenav）
    - biznav_features    业务功能点查询（biznav SQLite）

重能力模块（dataexpert / loganalysis / image_processing）依赖 LLM / OCR /
外部连接，不适合 builtin 通道，仍走各自 FastAPI / MCP 入口。

所有函数懒加载依赖模块：索引库缺失 / 模块异常时返回友好错误而不是崩溃。
"""
from __future__ import annotations

import os
from typing import Any

from agent.builtin.models import ToolResult


def _codenav_db_path() -> str:
    if os.environ.get("EAIDE_WORKSPACE_INDEX_DB"):
        return os.environ["EAIDE_WORKSPACE_INDEX_DB"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "eaide", "workspace_index.db")
    return os.path.expanduser("~/.eaide/workspace_index.db")


def _index_missing_result() -> ToolResult:
    return ToolResult(
        ok=False,
        error="index_not_built",
        hint="代码索引库不存在，请先在 IDE 内构建工作区索引",
        risk_level="read",
    )


def builtin_symbol_search(
    *,
    name: str,
    kind: str | None = None,
    limit: int = 10,
) -> ToolResult:
    """代码符号检索（函数 / 类 / 变量；精确 + 模糊）。"""
    db_path = _codenav_db_path()
    if not os.path.exists(db_path):
        return _index_missing_result()
    try:
        from agent.codenav.query import SymbolQuery

        q = SymbolQuery(db_path)
        symbols = q.search(name, kind=kind, limit=max(1, min(int(limit), 50)))
        return ToolResult(
            ok=True,
            content=[s.__dict__ for s in symbols],
            meta={"count": len(symbols)},
            risk_level="read",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="read")


def builtin_file_symbols(*, file_path: str) -> ToolResult:
    """列出指定文件的全部符号。"""
    db_path = _codenav_db_path()
    if not os.path.exists(db_path):
        return _index_missing_result()
    try:
        from agent.codenav.query import SymbolQuery

        q = SymbolQuery(db_path)
        symbols = q.get_file_symbols(file_path)
        return ToolResult(
            ok=True,
            content=[s.__dict__ for s in symbols],
            meta={"count": len(symbols), "file": file_path},
            risk_level="read",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="read")


def _biznav_db_path() -> str:
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "eaide", "biznav.db")
    return os.path.expanduser("~/.eaide/biznav.db")


def _feature_to_dict(feature: Any) -> dict:
    try:
        return feature.model_dump(mode="json")
    except AttributeError:
        return dict(vars(feature))


def builtin_biznav_features(
    *,
    project_name: str,
    category: str | None = None,
) -> ToolResult:
    """业务功能点查询（按项目列出，可选按分类过滤）。"""
    db_path = os.environ.get("EAIDE_BIZNAV_DB", _biznav_db_path())
    if not os.path.exists(db_path):
        return ToolResult(
            ok=False,
            error="biznav_not_built",
            hint="业务导航索引库不存在，请先在 IDE 内提取业务功能点",
            risk_level="read",
        )
    try:
        from agent.biznav.storage import FeatureStorage

        storage = FeatureStorage(db_path)
        features = storage.list_by_project(project_name)
        rows = [
            _feature_to_dict(f)
            for f in features
            if category is None or getattr(f, "category", None) == category
        ]
        return ToolResult(
            ok=True,
            content=rows,
            meta={"count": len(rows), "project": project_name},
            risk_level="read",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.from_exception(exc, risk_level="read")


__all__: list[str] = [
    "builtin_symbol_search",
    "builtin_file_symbols",
    "builtin_biznav_features",
]
