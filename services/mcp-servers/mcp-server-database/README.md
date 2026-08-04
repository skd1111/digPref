# mcp-server-database

> 企业级 SQL 数据库 MCP Server。基于 Model Context Protocol（stdio transport），
> 内置 **sqlglot AST 校验 + 多层高危操作拦截 + 行/字节双重截断 + HITL 强制审批**。

## 暴露的工具

| Tool | 写入? | 是否需要 HITL | 说明 |
|------|-------|--------------|------|
| `db.query` | ❌ | ❌ | 只读 SELECT，自动套 `READ ONLY` 会话 |
| `db.execute` | ✅ | ✅（必传 `approval_id`） | 写操作，事务包裹，自动回滚 |
| `db.schema` | ❌ | ❌ | `information_schema` 内省，返回表/列/类型 |

## 安全护栏（5 层防御）

```
┌──────────────────────────────────────────────────────────────┐
│ Layer 1 — 方言白名单  (safety/dialect_allowlist.py)         │
│   └─ 仅放行 ansi/postgres/mysql/sqlite/tsql/snowflake/…    │
├──────────────────────────────────────────────────────────────┤
│ Layer 2 — sqlglot AST 校验  (safety/sqlglot_validator.py)   │
│   ├─ 顶层白名单: SELECT / INSERT / UPDATE / DELETE / WITH  │
│   ├─ 多语句阻断（含注释/字符串内分号识别）                  │
│   ├─ 危险函数黑名单: xp_cmdshell / LOAD_FILE / pg_read_file│
│   └─ 危险命令黑名单: VACUUM / COPY / SET / 写型 EXPLAIN     │
├──────────────────────────────────────────────────────────────┤
│ Layer 3 — 高危操作硬拒  (safety/dangerous_ops.py)          │
│   ├─ DROP / TRUNCATE / GRANT / REVOKE / SHUTDOWN → 直接拒  │
│   ├─ UPDATE/DELETE 无 WHERE → 拒                            │
│   ├─ WHERE 1=1 / 常量真值 → 拒                              │
│   └─ CTE / 子查询内含 DDL → 拒                              │
├──────────────────────────────────────────────────────────────┤
│ Layer 4 — 只读会话强制  (safety/readonly_enforce.py)       │
│   ├─ Postgres → SET TRANSACTION READ ONLY 包裹              │
│   ├─ MySQL    → DSN 必须含 readonly=1，否则拒                │
│   └─ SQLite   → ?mode=ro URI                                │
├──────────────────────────────────────────────────────────────┤
│ Layer 5 — 调用侧 HITL 强校验  (tools/execute.py)            │
│   ├─ 缺 approval_id → ApprovalMissingError                  │
│   └─ approval_id 不在审计表 approve 行 → 拒                  │
└──────────────────────────────────────────────────────────────┘
```

## 响应结构（统一）

成功：
```json
{
  "ok": true,
  "tool": "db.query",
  "connection": "orders_pg",
  "dialect": "postgres",
  "columns": ["id", "name"],
  "rows": [[1, "alice"]],
  "truncated": false,
  "rows_returned": 1,
  "rows_dropped_by_row_cap": 0,
  "rows_dropped_by_byte_cap": 0,
  "duration_ms": 42
}
```

失败：
```json
{
  "ok": false,
  "tool": "db.query",
  "code": "UNSAFE_SQL",
  "message": "function not allowed: pg_read_file",
  "duration_ms": 3
}
```

错误码：

| Code | 含义 |
|------|------|
| `UNSAFE_SQL` | sqlglot 校验失败（语法/危险函数/危险命令） |
| `DESTRUCTIVE_OP` | 高危操作硬拒（即便 HITL 批准也不放行） |
| `READONLY_VIOLATION` | DSN 不带 readonly 标记 |
| `APPROVAL_MISSING` | 缺 `approval_id` 或未在审计表中找到 approve 记录 |
| `UNSUPPORTED_DIALECT` | 连接名推断出的方言不在白名单 |
| `DRIVER_ERROR` | 数据库驱动层错误（连接失败、SQL 错误） |
| `SCHEMA_ERROR` | `information_schema` 内省失败 |
| `TIMEOUT` | 超过 `EAIDE_DB_TOOL_TIMEOUT_SEC`（默认 10s） |
| `INTERNAL` | 未捕获的内部异常 |

## 配置

| Env Var | 默认 | 说明 |
|---------|------|------|
| `EAIDE_DB_TOOL_TIMEOUT_SEC` | 10 | 每次工具调用硬超时 |
| `EAIDE_DB_DEFAULT_ROW_LIMIT` | 50 | 默认返回行数上限 |
| `EAIDE_DB_ENFORCE_READONLY_ACCOUNT` | true | 强制只读账号 |
| `EAIDE_AUDIT_DB` | `audit.sqlite` | 共享审计 SQLite |
| `EAIDE_AUDIT_JSONL` | `audit.sqlite.jsonl` | 审计 JSONL sidecar |
| `EAIDE_APPROVAL_DRY_RUN` | `0` | 开发模式：跳过 approval_id 校验 |

DSN 通过 env 注入（由 Rust 凭证保险箱从 OS Keychain 读取后写入）：
```
EAIDE_DB_DSN_ORDERS_PG  = postgresql://readonly@db-1:5432/orders?readonly=1
EAIDE_DB_DSN_BILLING_MY = mysql://readonly@db-2:3306/billing?readonly=1
EAIDE_DB_DSN_LOCAL_SQ   = sqlite:////var/lib/eaide/local.sqlite
```

## HITL 契约

写操作调用必须携带上游 LangGraph `hitl_gate_node` 签发的 `approval_id`：

```python
# 1. 上游 Agent 暂停
await interrupt(approval_id="appr_abc", plan={...})

# 2. 前端用户点 Approve
#    → Rust POST /approval/appr_abc  with {"decision": "approve"}
#    → Agent 写 audit: action="approval.decision", payload={approval_id, decision}

# 3. Agent resume 后调用 db.execute
await mcp.call_tool("db.execute", {
    "connection": "orders_pg",
    "sql": "UPDATE orders SET status='shipped' WHERE id = $1",
    "params": [42],
    "approval_id": "appr_abc",   # ← 必须
})
```

`execute.run` 会**查审计表**确认存在 `approval.decision=approve` 行，否则 `ApprovalMissingError`。
生产环境请设置 `EAIDE_APPROVAL_DRY_RUN=0`。

## 开发与测试

```bash
# 安装
uv pip install -e .[dev]

# 跑测试
uv run pytest -q

# 单独启动 stdio server
uv run mcp-server-database

# 用 MCP Inspector 调试
npx @modelcontextprotocol/inspector uv run mcp-server-database
```

测试覆盖：
- `tests/test_safety_validator.py` — 22 用例（happy path + 多语句 + DDL + 函数 + 命令）
- `tests/test_safety_dangerous_ops.py` — 15 用例（token 拦截 + WHERE 缺失 + CTE 写）
- `tests/test_limit_byte_size.py` — 11 用例（per-cell + per-result + 整批截断）
- `tests/test_query_tool.py` — 6 用例（SQLite E2E + 截断 + 拒恶意 SQL）
- `tests/test_execute_tool.py` — 6 用例（HITL gate + 审计 + 拒 destructive）
- `tests/test_schema_tool.py` — 2 用例（内省正确性）
- `tests/test_server_error_mapping.py` — 6 用例（错误码映射）

## 添加新方言

1. 在 `safety/dialect_allowlist.py` 的 `ALLOWED_DIALECTS` 加 dialect id。
2. 在 `safety/dialect_allowlist.py` 的 `CONNECTION_SUFFIX_TO_DIALECT` 加后缀映射。
3. 在 `tools/query.py::_execute` 加 driver 分支。
4. 在 `tools/execute.py::_execute` 加 driver 分支。
5. 在 `tools/schema.py::_introspect` 加 introspection 查询。
6. 在 `tests/` 加 driver 特定测试。