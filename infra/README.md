# infra — 运维与配置

```
infra/
├── docker/
│   ├── docker-compose.dev.yml   # 本地开发编排
│   ├── agent.Dockerfile         # FastAPI Agent 镜像
│   └── mcp.Dockerfile           # 通用 MCP 服务镜像
├── scripts/
│   ├── dev.sh                   # 本地一键启动
│   ├── build-tauri.sh           # 跨平台 Tauri 构建
│   └── seed-audit-db.py         # 初始化审计 schema
└── config/
    ├── agent.example.yaml       # 复制一份 → ~/.eaide/agent.yaml
    └── mcp.example.yaml         # 复制一份 → ~/.eaide/mcp.yaml
```

> ⚠️ 永远不要把真实密钥提交到 `infra/config/*.yaml`——它们只是模板。
> 所有凭证通过 `EAIDE_*` 环境变量 → Tauri Rust 端 → 系统 Keychain 解析。
