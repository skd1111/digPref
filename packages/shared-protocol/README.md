# packages/shared-protocol — 跨语言协议

> 唯一真相：所有跨进程类型（TS ⇄ Rust ⇄ Python ⇄ MCP）的 schema 都来自这里。

## 原则

1. **TS 文件先写** —— 前端是最大的消费方，先确定 TS 形态。
2. **Python Pydantic 镜像** —— `src/protocol/*.py` 与 `src/ts/*.ts` 一一对应。
3. **JSON 字段用 camelCase** —— 通用 web 习惯；Pydantic 用 `Field(alias=...)` 兼容 snake_case 内部命名。
4. **不携带运行时** —— 只放类型与常量，不放业务逻辑。

## 目录

```
src/
├── ts/                  # apps/desktop 消费
│   ├── index.ts         # barrel
│   ├── events.ts        # AgentStreamEvent 判别联合
│   ├── tools.ts
│   ├── agent.ts
│   ├── approval.ts
│   ├── audit.ts
│   └── mcp.ts
└── protocol/            # services/* 消费
    ├── __init__.py
    ├── events.py
    ├── tools.py
    ├── agent.py
    ├── approval.py
    ├── audit.py
    └── mcp.py
```
