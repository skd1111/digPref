"""测试 max_context 在 LLM 客户端中的传递。

覆盖：
    - OllamaClient._chat 把 max_context 注入 options.num_ctx
    - PrivateLLMClient._truncate_history 按 max_context 截断（保留 system + 最近轮）
    - router.LMRouter 从 router.db 同步读 max_context
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

# ---- OllamaClient -----------------------------------------------------------


def test_ollama_injects_num_ctx():
    """max_context 设置后，_chat payload 应含 options.num_ctx。"""
    from agent.llm.ollama import OllamaClient

    cli = OllamaClient(base_url="http://x", model="m", max_context=32000)

    async def fake_post(url, json, **kw):
        class R:
            def raise_for_status(self):
                pass

            def json(self):
                return {"message": {"content": "{}"}}

        return R()

    async def run():
        with patch("httpx.AsyncClient") as mc:
            client = mc.return_value.__aenter__.return_value
            client.post = AsyncMock(side_effect=fake_post)
            await cli._chat([{"role": "user", "content": "hi"}])

            # 验证调用 post 时 payload 包含 options.num_ctx=32000
            call_kwargs = client.post.call_args.kwargs
            payload = call_kwargs["json"]
            assert payload["options"]["num_ctx"] == 32000

    asyncio.run(run())


def test_ollama_no_num_ctx_when_unset():
    """max_context 为 None 时，payload 不带 options.num_ctx。"""
    from agent.llm.ollama import OllamaClient

    cli = OllamaClient(base_url="http://x", model="m", max_context=None)

    async def run():
        with patch("httpx.AsyncClient") as mc:
            client = mc.return_value.__aenter__.return_value
            client.post = AsyncMock()
            client.post.return_value = type(
                "R",
                (),
                {
                    "raise_for_status": lambda self: None,
                    "json": lambda self: {"message": {"content": "{}"}},
                },
            )()
            await cli._chat([{"role": "user", "content": "hi"}])

            payload = client.post.call_args.kwargs["json"]
            # 没设 max_context 就不该出现 num_ctx
            assert "options" not in payload or "num_ctx" not in payload.get("options", {})

    asyncio.run(run())


# ---- PrivateLLMClient truncate ---------------------------------------------


def test_private_keeps_system_and_recent_turns():
    """max_context 触发裁剪时，system 永远保留 + 最近的 user/assistant 优先。"""
    from agent.llm.private_llm import PrivateLLMClient

    # max_context=512 → budget_tokens=max(256, 512-1024)=256 → budget_chars=1024
    cli = PrivateLLMClient(
        base_url="http://x",
        api_key="k",
        model="m",
        max_context=512,
    )

    # system 占 14 chars；non-system 4 条 × 400 chars = 1600 chars → 必然超 budget
    msgs = [
        {"role": "system", "content": "SYSTEM_PROMPT_X"},  # 永远保留
        {"role": "user", "content": "X" * 400},  # 旧消息（应被裁）
        {"role": "assistant", "content": "Y" * 400},
        {"role": "user", "content": "Z" * 400},
        {"role": "assistant", "content": "W" * 400},
        {"role": "user", "content": "RECENT_MSG"},  # 最新一条应保留
    ]

    truncated = cli._truncate_history(msgs)

    # system 必须保留
    assert any(m["role"] == "system" and m["content"] == "SYSTEM_PROMPT_X" for m in truncated)
    # 最新一条必须保留（user RECENT_MSG）
    assert any(m.get("content") == "RECENT_MSG" for m in truncated)
    # 旧消息应被裁掉至少一条
    assert len(truncated) < len(msgs)
    # 第一条旧 user（X*400）必须被裁掉
    assert not any(m.get("content") == "X" * 400 for m in truncated)


def test_private_no_truncate_when_unset():
    """max_context 为 None 时不裁剪，原样返回。"""
    from agent.llm.private_llm import PrivateLLMClient

    cli = PrivateLLMClient(base_url="http://x", api_key="k", model="m", max_context=None)
    msgs = [{"role": "user", "content": "x" * 10000}]
    assert cli._truncate_history(msgs) == msgs


def test_private_truncates_oversized_system():
    """system 自己超长时被截断（保留至少 256 chars）。"""
    from agent.llm.private_llm import PrivateLLMClient

    cli = PrivateLLMClient(
        base_url="http://x",
        api_key="k",
        model="m",
        max_context=512,
    )
    huge_system = "X" * 5000
    msgs = [{"role": "system", "content": huge_system}]
    truncated = cli._truncate_history(msgs)
    assert len(truncated) == 1
    # 截断后长度小于原长（实际为 keep_chars=512）
    assert len(truncated[0]["content"]) < 5000


# ---- LMRouter DB sync read -------------------------------------------------


def test_router_loads_max_context_from_db(tmp_path, monkeypatch):
    """LMRouter.__init__ 应从 router.db 读 max_context 并传给 client。"""
    import sqlite3

    from agent.llm.router import _load_max_context_from_db

    # 准备 router.db + 写一行 enabled=1 的 local ollama
    db_path = tmp_path / "router.db"
    schema_path = __import__("pathlib").Path(__file__).parent.parent / "src/agent/llm/schema.sql"
    conn = sqlite3.connect(db_path, timeout=5)
    conn.executescript(schema_path.read_text(encoding="utf-8"))
    conn.execute(
        "INSERT INTO llm_backends (name, type, base_url, model_name, max_context, enabled, data_residency, role) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("ollama", "local", "http://x", "qwen2.5:14b", 200000, 1, "local", "execution"),
    )
    conn.commit()
    conn.close()

    # monkeypatch settings 指向 tmp db
    from agent.config import settings

    monkeypatch.setattr(settings, "llm_router_db_path", str(db_path))
    monkeypatch.setattr(settings, "ollama_model", "qwen2.5:14b")

    ollama_ctx, private_ctx = _load_max_context_from_db()
    assert ollama_ctx == 200000
    # private 没匹配行 → None
    assert private_ctx is None


def test_router_handles_missing_db_gracefully(tmp_path, monkeypatch):
    """router.db 不存在时 _load_max_context_from_db 返回 (None, None) 不抛异常。"""
    from agent.config import settings
    from agent.llm.router import _load_max_context_from_db

    monkeypatch.setattr(settings, "llm_router_db_path", str(tmp_path / "nonexistent.db"))
    ollama_ctx, private_ctx = _load_max_context_from_db()
    assert ollama_ctx is None
    assert private_ctx is None
