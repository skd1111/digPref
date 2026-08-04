/**
 * Shared protocol — single source of truth for all wire types
 * exchanged between Tauri (TS) ⇄ Rust ⇄ FastAPI (Py) ⇄ MCP servers.
 */
export * from './events';
export * from './tools';
export * from './agent';
export * from './approval';
export * from './audit';
export * from './mcp';
export * from './dspark';
export * from './doc-review';
export * from './codenav';
export * from './sub_agent';
export * from './thinking';
