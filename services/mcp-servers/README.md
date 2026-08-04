# services/mcp-servers — MCP Server 矩阵

> 全部基于 **Model Context Protocol** 标准，通过 stdio transport 与 Agent 通信。
> 每个 server 内部都自带 `safety/` 子模块做白名单/拦截，外加 `audit/` 写共享审计。

## 服务清单

| Server | 关键能力 | 安全护栏 |
|--------|---------|----------|
| `mcp-server-database` | MySQL / Postgres / SQLite 查询与执行 | sqlglot 语法校验 + 高危操作硬拒 + 只读账号强制 + 行数截断 |
| `mcp-server-rest` | HTTP 调用 + OpenAPI -> Tool 自动生成 | 主机白名单 + 每主机方法策略 + Body 截断 |
| `mcp-server-ssh` | Linux/Unix 远程命令执行 + SFTP 上传 | 主机白名单 + 命令黑名单 (rm -rf /, mkfs, dd, …) |
| `mcp-server-rpa` | Playwright 无头浏览器 (点击 / 提取) | 域名白名单 + 文本长度截断 |

## 共享骨架

```
mcp-server-XXX/
├── pyproject.toml
└── src/mcp_server_XXX/
    ├── server.py           # MCP stdio 入口
    ├── config.py           # pydantic-settings
    ├── client.py           # 第三方客户端封装
    ├── safety/             # ★ 所有安全护栏
    │   ├── __init__.py
    │   ├── *_blacklist.py
    │   ├── *_whitelist.py
    │   └── *_enforce.py
    ├── tools/              # 暴露给 Agent 的工具
    ├── limit/              # 超时 / 大小限制
    └── audit/              # 审计发射器
```

## 开发

```bash
# 单测
uv run --project services/mcp-servers/mcp-server-database pytest

# 单独启动 stdio server (供 inspector)
uv run --project services/mcp-servers/mcp-server-database mcp-server-database
```

## 添加新 Server 的最小步骤

1. 拷贝 `mcp-server-database/` 改名为 `mcp-server-foo/`
2. 替换 `safety/` 与 `tools/` 中的业务实现
3. 在 `infra/docker/docker-compose.dev.yml` 增加 service
4. 在 `services/agent/mcp.yaml` 注册新 server