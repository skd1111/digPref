"""共享 pytest fixtures 及路径引导。

测试隔离策略：
    - autouse _isolate fixture 把工作目录切换到 tmp_path
    - 环境变量名必须与 pydantic-settings 字段名精确匹配（EAIDE_ 前缀）
    - 所有测试不依赖用户级配置，保证 CI 可重现
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from agent.config import settings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """隔离每个测试：切换到临时目录，注入干净的环境变量。

    重要：环境变量名必须与 config.py Settings 类的字段名一致。
    pydantic-settings 将 EAIDE_ 前缀剥离后做蛇形→驼峰映射。
    例如字段 `audit_db_path` 对应环境变量 `EAIDE_AUDIT_DB_PATH`。
    """
    monkeypatch.chdir(tmp_path)
    # 审计
    monkeypatch.setenv("EAIDE_AUDIT_DB_PATH", str(tmp_path / "audit.sqlite"))
    # Agent 服务
    monkeypatch.setenv("EAIDE_HOST", "127.0.0.1")
    monkeypatch.setenv("EAIDE_PORT", "8765")
    monkeypatch.setenv("EAIDE_LOG_LEVEL", "warning")
    # 超时与限制
    monkeypatch.setenv("EAIDE_TOOL_TIMEOUT_SEC", "5")
    monkeypatch.setenv("EAIDE_ROW_LIMIT", "10")
    # HITL
    monkeypatch.setenv("EAIDE_REQUIRE_HITL_FOR_WRITE", "true")
    monkeypatch.setenv("EAIDE_APPROVAL_TIMEOUT_SEC", "5")
    # 动态工具循环：测试默认走既有 planner → tool_runner 路径
    # （loop 相关测试用 monkeypatch.setattr(settings, "tool_loop_enabled", True) 单独开启）
    monkeypatch.setattr(settings, "tool_loop_enabled", False)
    # 语义路由（2026-08-31 默认开启）：测试默认关闭，避免真实进程内向量模型
    # 干扰断言；专项测试自行 setattr(True) + 注入伪 embedding。
    monkeypatch.setattr(settings, "semantic_route_enabled", False)
    try:
        from agent.graph.semantic_route import reset_semantic_router
        from agent.llm.onnx_embedding import reset_onnx_embedding_client

        reset_semantic_router()
        reset_onnx_embedding_client()
    except Exception:
        pass
    # 禁用 Redis —— 测试走进程内 dict fallback
    monkeypatch.delenv("EAIDE_REDIS_URL", raising=False)
    # 禁用内网 LLM —— 测试只用 mock
    monkeypatch.delenv("EAIDE_PRIVATE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("EAIDE_PRIVATE_LLM_API_KEY", raising=False)
    # 清空 planner 模块级工具目录缓存 —— 防止跨用例（不同 MCP mock）泄漏
    try:
        from agent.graph.nodes.planner import reset_tool_specs_cache

        reset_tool_specs_cache()
    except Exception:
        pass
    yield


@pytest.fixture
def mock_llm():
    """Stub LMRouter —— 返回预置响应，不调用真实 LLM。"""

    class _Mock:
        async def classify_intent(self, text):
            return "query"

        async def plan(self, *, intent, user_prompt, history, tool_specs):
            return [
                {
                    "server": "db",
                    "name": "db.query",
                    "args": {"sql": "SELECT 1"},
                    "risk_level": "read",
                    "rationale": "mock",
                }
            ], "mock plan"

        async def repair_call(self, *, original, error, history):
            return {**original, "args": {"sql": "SELECT 2"}}

        async def summarise(self, *, intent, user_prompt, plan, results, history=None):
            return "Mock final answer.", ["db"]

    return _Mock()


@pytest.fixture
def mock_mcp():
    """Stub McpClient —— 返回预置数据，不连接真实 MCP 服务器。"""

    class _Mock:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def list_tools(self):
            return [
                {
                    "server": "db",
                    "name": "db.query",
                    "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}},
                },
                {
                    "server": "db",
                    "name": "db.execute",
                    "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}},
                },
            ]

        async def invoke(self, call, *, timeout_sec, row_limit):
            if call.get("name") == "db.query":
                return {
                    "ok": True,
                    "columns": ["x"],
                    "rows": [[42]],
                    "truncated": False,
                    "rows_returned": 1,
                    "rows_dropped_by_row_cap": 0,
                    "rows_dropped_by_byte_cap": 0,
                }
            if call.get("name") == "db.execute":
                return {"ok": True, "rows_affected": 1}
            return {"ok": False, "error": "unknown"}

    return _Mock()
