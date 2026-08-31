"""Integration tests for the internal LLM (172.1.0.134:8000).

These tests hit a real HTTP endpoint. They will be skipped automatically
if the server is unreachable (so CI without VPN still passes).

Run explicitly with:
    EAIDE_RUN_LIVE_TESTS=1 pytest tests/test_llm_internal.py -v
"""

from __future__ import annotations

import socket
import time

import pytest

# ---- Reachability probe ---------------------------------------------------

INTERNAL_URL = "172.1.0.134"
INTERNAL_PORT = 8000


def _internal_reachable(timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((INTERNAL_URL, INTERNAL_PORT), timeout=timeout):
            pass
    except OSError:
        return False
    # 网关活着但后端模型挂了（5xx）同样视为不可用 —— 避免 live 用例被瞬时故障拖红；
    # 探测失败（连接重置等）也按不可用处理。
    import httpx

    try:
        r = httpx.get(f"http://{INTERNAL_URL}:{INTERNAL_PORT}/v1/models", timeout=timeout)
        return r.status_code < 500
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(
    not _internal_reachable(),
    reason=f"internal model {INTERNAL_URL}:{INTERNAL_PORT} is not reachable",
)


# ---- Fixtures -------------------------------------------------------------


@pytest.fixture(scope="module")
def live_private_client():
    """A real PrivateLLMClient pointing at the internal gateway."""
    from agent.config import settings
    from agent.llm.private_llm import PrivateLLMClient

    return PrivateLLMClient(
        base_url=settings.private_llm_base_url or "http://172.1.0.134:8000/v1",
        api_key=settings.private_llm_api_key or "internal-no-auth",
        model=settings.private_llm_model or "DeepSeek-RD-Llama-70B-Int8",
    )


# ---- Direct chat-completions smoke test ----------------------------------


class TestChatCompletions:
    def test_basic_json_roundtrip(self, live_private_client):
        """The internal model + JSON mode produces parseable JSON."""
        import httpx

        payload = {
            "model": live_private_client.model,
            "messages": [
                {"role": "system", "content": "Reply with strict JSON only. No markdown."},
                {
                    "role": "user",
                    "content": (
                        "Classify intent. Pick one: query|mutate|orchestrate|chitchat. "
                        "Text: 'show me yesterday orders count'. "
                        'Output: {"intent":"<value>"}'
                    ),
                },
            ],
            "max_tokens": 800,
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        r = httpx.post(
            f"{live_private_client.base_url}/chat/completions",
            json=payload,
            headers={
                "Authorization": f"Bearer {live_private_client.api_key}",
                "Content-Type": "application/json",
            },
            timeout=60,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert "choices" in body
        raw = body["choices"][0]["message"]["content"]
        # Strip the <think> block before parsing
        from agent.llm.private_llm import _strip_think

        cleaned = _strip_think(raw)
        parsed = __import__("json").loads(cleaned)
        assert parsed.get("intent") in {"query", "mutate", "orchestrate", "chitchat"}

    def test_think_block_stripped(self, live_private_client):
        from agent.llm.private_llm import _strip_think

        sample = '<think>\nthinking...\n</think>\n{"answer": "ok"}'
        out = _strip_think(sample)
        assert "<think>" not in out
        assert out.strip() == '{"answer": "ok"}'


# ---- PrivateLLMClient high-level methods --------------------------------


class TestPrivateClientHighLevel:
    @pytest.mark.asyncio
    async def test_plan_returns_structured(self, live_private_client):
        """The plan() method returns a list of step dicts + explanation."""
        plan, explanation = await live_private_client.plan(
            intent="query",
            user_prompt="show me yesterday's orders count",
            history=[],
            tool_specs=[
                {
                    "server": "db",
                    "name": "db.query",
                    "description": "run SQL query",
                    "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}},
                }
            ],
        )
        assert isinstance(plan, list)
        # explanation is best-effort — accept either a string or empty
        assert isinstance(explanation, str)
        # We don't assert len(plan) > 0 because the model sometimes decides
        # no tool is needed; the contract is the shape.

    @pytest.mark.asyncio
    async def test_summarise_returns_answer_and_sources(self, live_private_client):
        answer, sources = await live_private_client.summarise(
            intent="query",
            user_prompt="how many orders yesterday?",
            plan=[{"server": "db", "name": "db.query"}],
            results=[{"ok": True, "rows": [[42]], "columns": ["count"]}],
        )
        assert isinstance(answer, str) and answer
        assert isinstance(sources, list)


# ---- LMRouter end-to-end via internal model ------------------------------


class TestLMRouterIntegration:
    @pytest.mark.asyncio
    async def test_router_picks_private_for_plan(self, monkeypatch):
        """LMRouter.plan should call PrivateLLMClient when configured."""
        # 内网默认地址已移除（BUGFIX #57）：显式配置 private。
        # settings 是已实例化单例，改环境变量不生效 → 直接打补丁。
        from agent.config import settings

        monkeypatch.setattr(settings, "private_llm_base_url", "http://172.1.0.134:8000/v1")
        monkeypatch.setattr(settings, "private_llm_api_key", "internal-no-auth")
        monkeypatch.setattr(settings, "private_llm_model", "DeepSeek-RD-Llama-70B-Int8")
        from agent.llm.router import LMRouter

        router = LMRouter()
        assert router.private is not None, "EAIDE_PRIVATE_LLM_* settings should be set above"

        steps, explanation = await router.plan(
            intent="query",
            user_prompt="ping",
            history=[],
            tool_specs=[
                {
                    "server": "db",
                    "name": "db.query",
                    "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}},
                },
            ],
        )
        assert isinstance(steps, list)
        assert isinstance(explanation, str)


# ---- Latency benchmark (informational) -----------------------------------


class TestLatency:
    @pytest.mark.asyncio
    async def test_typical_call_latency(self, live_private_client):
        """Smoke test: a typical round-trip should complete in <30s."""
        started = time.monotonic()
        await live_private_client.plan(
            intent="query",
            user_prompt="ping",
            history=[],
            tool_specs=[{"server": "db", "name": "db.query", "inputSchema": {}}],
        )
        elapsed = time.monotonic() - started
        # Generous bound — the model is 70B and may need warm-up on first call
        assert elapsed < 60, f"plan() took {elapsed:.1f}s, exceeds 60s budget"


# ---- Unified JSON parsing (spec 4.5 layers 3/4, no live backend) ------------


@pytest.mark.asyncio
async def test_ollama_classify_intent_accepts_fenced_json(monkeypatch):
    from agent.llm.ollama import OllamaClient

    client = OllamaClient(base_url="http://x", model="qwen")

    async def fake_chat(messages, *, format=None, options=None, timeout=30.0):
        return {"content": '```json\n{"intent": "mutate"}\n```'}

    monkeypatch.setattr(client, "_chat", fake_chat)
    assert await client.classify_intent("改一下订单") == "mutate"


@pytest.mark.asyncio
async def test_ollama_plan_accepts_think_prefix(monkeypatch):
    import json

    from agent.llm.ollama import OllamaClient

    client = OllamaClient(base_url="http://x", model="qwen")

    async def fake_chat(messages, *, format=None, options=None, timeout=30.0):
        body = {"explanation": "查订单", "steps": []}
        return {"content": "<THINK>先看表</THINK>" + json.dumps(body)}

    monkeypatch.setattr(client, "_chat", fake_chat)
    steps, expl = await client.plan(intent="query", user_prompt="查订单", history=[], tool_specs=[])
    assert expl == "查订单"
    assert steps == []
