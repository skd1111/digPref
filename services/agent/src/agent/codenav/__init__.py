"""Phase 2F 代码导航模块。

提供 tree-sitter AST 索引 + SQLite 符号库 + AI 跳转降级。

子模块：
- models: 数据类（Symbol / JumpResult / IndexStatus）
- language_registry: 文件后缀 → tree-sitter 语言映射
- indexer: 全量扫描 + AST 提取 + 写 SQLite
- watcher: watchfiles 增量监听
- query: SQLite 查询接口
- mcp_tools: MCP 工具注册
- api: FastAPI 路由
"""

from __future__ import annotations

__all__ = ["api", "indexer", "language_registry", "mcp_tools", "models", "query", "watcher"]
