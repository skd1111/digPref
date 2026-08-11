# services/agent — Python Control Layer

> FastAPI + LangGraph 大脑。所有 MCP 调用从这里发出；写操作必经 HITL gate；
> 工具错误自动 Auto-Repair；模型路由支持本地 Ollama 与企业内部 LLM。

---

## 1. 状态机拓扑

```
                     ┌────────────────────────────────────────┐
                     │              LangGraph                 │
                     │                                        │
   prompt  ─────►    │   ┌───────┐    ┌─────────┐              │
                     │   │intent │───▶│ planner │              │
                     │   └───────┘    └────┬────┘              │
                     │                    │                   │
                     │              empty │ have plan         │
                     │                    ▼                   ▼
                     │               ┌──────────┐    ┌────────────┐
                     │               │ responder│◄───┤tool_runner │
                     │               └────▲─────┘    └─────┬──────┘
                     │                    │                 │
                     │          ┌─────────┴──────────┐  error│
                     │          ▼                    ▼       │
                     │    ┌──────────┐          ┌────────┐    │
                     │    │ responder│          │ repair │    │
                     │    └──────────┘          └────┬───┘    │
                     │          ▲                   │        │
                     │          │            retry_count≥2? │
                     │          │                no ↓ yes    │
                     │   ┌──────┴───────┐     (retry)│  (give │
                     │   │  hitl_gate   │◄───────────┘    up)  │
                     │   └──────┬───────┘                   │
                     │          │                           │
                     │    approve / reject / timeout         │
                     │          │                           │
                     │          ▼                           ▼
                     │   ┌──────────────────────────────────┐
                     │   │           responder              │
                     │   └──────────────────────────────────┘
                     │                                        │
                     └────────────────────────────────────────┘
```

每个节点都是 `async def node(state, deps) -> dict`，返回 LangGraph 的 partial state update。

## 2. HITL 合约

```
[Agent graph]   ─── interrupt(approval_id, plan) ──►  [Redis eaide:approval:pending:{id}]
                                                          │
                              ┌───────────────────────────┘
                              ▼
                       [Desktop UI 渲染 <ApprovalCard />]
                              │
                              ▼  user clicks Approve/Reject
                       POST /approval/{id}  body={decision}
                              │
                              ▼
                       [Agent graph] resume_run() 写 Redis
                              │
                              ▼
                       interrupt() 返回 decision → hitl_gate_node
```

详见 [`graph/interrupt.py`](src/agent/graph/interrupt.py)。Redis 不可用时自动降级为进程内 `dict`。

## 3. Auto-Repair 契约

| 场景 | 行为 |
|------|------|
| 工具调用报错 → `repair_node` 用错误原文喂给 LLM 重生 args |
| `retry_count < 2` → 替换 plan[idx]，清空 `tool_error`，回到 `tool_runner` |
| `retry_count ≥ 2` → 保持 `tool_error`，交给 `responder_node` 写出失败消息 |
| 瞬态错误（timeout / 5xx / 连接 reset）→ `tool_runner` 内部自动重试 1 次 |

## 4. 模型路由

| 任务 | 默认后端 | 切换依据 |
|------|---------|----------|
| `classify_intent` | Ollama | 必须本地 — 可能接收含敏感数据的请求 |
| `repair_call` | Ollama | 同上 |
| `plan` | Private LLM | 复杂推理；Ollama 兜底 |
| `summarise` | Private LLM | 同上 |

强制本地任务（intent / repair）**永远不**走 Private LLM — 防止敏感 SQL 结果被外发。

### 4.1 内部企业模型

默认为空 —— 是否启用内网后端只看「模型管理」（`router.db.llm_backends`）里有没有
启用的 `private` 后端；也可用环境变量注入：

```
EAIDE_PRIVATE_LLM_BASE_URL = "http://你的内网网关/v1"
EAIDE_PRIVATE_LLM_MODEL    = "模型名"
EAIDE_PRIVATE_LLM_API_KEY  = "密钥"
```

注：不要内置占位网关默认地址 —— 不可达的默认地址会让每条消息白等 TCP 连接超时（BUGFIX #57）。

**已知特性**：该模型会在 JSON 之前输出 `<think>...</think>` 推理块。
[`private_llm.py`](src/agent/llm/private_llm.py) 用正则 `_strip_think()` 在解析前剥离。

## 5. 模块索引

| 路径 | 职责 |
|------|------|
| `api/` | FastAPI 路由：`/chat`, `/approval`, `/health`, `/ws` |
| `graph/state.py` | `AgentState` TypedDict + `empty_state()` |
| `graph/nodes/` | `intent / planner / tool_runner / hitl_gate / repair / responder` |
| `graph/edges.py` | 4 个条件路由函数 |
| `graph/compile.py` | `Runtime` dataclass + `compile_graph()` |
| `graph/stream.py` | `astream → SSE event` 适配器 |
| `graph/interrupt.py` | Redis 协调的 HITL 暂停/恢复 |
| `llm/router.py` | `LMRouter` — Ollama / Private LLM 路由 |
| `llm/ollama.py` | Ollama HTTP 客户端 |
| `llm/private_llm.py` | OpenAI 兼容客户端 + `<think>` 剥离 |
| `llm/prompts/` | `intent / planner / repair / summarise` Markdown 模板 |
| `llm/prompts.py` | `load_prompt(name)` loader |
| `mcp/` | 多 MCP server 聚合 + ToolCall / ToolResult 模型 |
| `safety/write_detector.py` | 写操作检测（name tokens + SQL 关键字 + risk_level） |
| `safety/policy.py` | `policy_for(call) → PolicyDecision` |
| `tools/context_trim.py` | 上下文窗口内的截断工具 |
| `tools/retry.py` | tenacity 异步重试装饰器 |
| `audit/store.py` | SQLite 审计写入 |
| `audit/schema.sql` | 共享审计表（与 Rust 端镜像） |

## 6. 启动

```bash
# 安装
uv sync --package agent

# 启动 Agent (dev)
cd services/agent
EAIDE_REQUIRE_HITL_FOR_WRITE=true \
  EAIDE_OLLAMA_BASE_URL=http://127.0.0.1:11434 \
  uv run uvicorn agent.main:app --reload --port 8765

# 通过 docker-compose
docker compose -f infra/docker/docker-compose.dev.yml up agent
```

## 7. 测试

```bash
# 离线测试 (54 个)
uv run pytest -q

# 离线 + 内部模型集成测试 (60 个)
# 自动跳过内部模型测试如果 172.1.0.134:8000 不可达
uv run pytest -q
```

测试覆盖：

| 文件 | 用例 | 覆盖 |
|------|------|------|
| `tests/test_state.py` | 8 | `AgentState` 形状、helpers |
| `tests/test_safety.py` | 11 | write_detector + policy_for |
| `tests/test_nodes.py` | 17 | 6 个节点各自的成功/失败分支 |
| `tests/test_edges.py` | 9 | 4 个条件路由 |
| `tests/test_interrupt.py` | 4 | HITL 暂停 / approve / reject / timeout |
| `tests/test_e2e.py` | 5 | **完整状态机闭环** + Auto-Repair + HITL |
| `tests/test_llm_internal.py` | 6 | **真实内部模型**连通性 + think-strip + plan/summarise |

## 8. 添加新节点

1. 在 `graph/nodes/` 加 `your_node.py`，实现 `async def your_node(state, deps) -> dict`
2. 在 `graph/compile.py::build_graph` 注册：`g.add_node("your_node", _your_node)`
3. 在 `graph/edges.py` 加路由函数并通过 `g.add_conditional_edges` 连接
4. 在 `graph/state.py::AgentState` 添加所需字段（TypedDict）
5. 在 `tests/test_nodes.py` 和 `tests/test_e2e.py` 加测试

## 9. 切换模型

```yaml
# infra/config/agent.yaml
llm:
  private:
    base_url: http://YOUR-LLM-GW/v1
    api_key: sk-...
    model: your-model-name
```

或者 env：`EAIDE_PRIVATE_LLM_BASE_URL=...` 重启 Agent 即可，无需改代码。