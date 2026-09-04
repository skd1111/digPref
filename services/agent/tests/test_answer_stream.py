"""回答逐字流式输出（answer_delta，2026-09-03）回归。

覆盖：
  1. ThinkBlockFilter 增量过滤（跨 delta 边界 / 大小写 / 围栏 / 未闭合 / flush 放行）
  2. OllamaClient.chat_stream NDJSON 帧解析
  3. PrivateLLMClient.chat_stream OpenAI SSE 帧解析
  4. LMRouter.summarise_stream：流式候选中选 / 首 delta 前失败切下级 /
     已发 delta 后失败回退非流式 / mock 单帧 / L1 缓存命中单帧
  5. responder._summarise_maybe_stream：emit answer_delta + 开关关闭/替身不支持时走原链路
  6. stream.py：answer_delta 种子化 final_answer_msg_id → 终答 message 复用同一 id
"""

from __future__ import annotations

import json

import pytest
from agent.builtin.events import (
    consume_builtin_events,
    emit_answer_delta,
    flush_builtin_events,
)
from agent.llm.stream_utils import ThinkBlockFilter

pytestmark = pytest.mark.usefixtures("_isolate")


# ---- 1. ThinkBlockFilter -----------------------------------------------------


class TestThinkBlockFilter:
    def test_plain_text_passthrough(self):
        f = ThinkBlockFilter()
        assert f.feed("你好，世界") == "你好，世界"
        assert f.flush() == ""

    def test_think_block_suppressed_across_deltas(self):
        f = ThinkBlockFilter()
        out1 = f.feed("前半<th")
        out2 = f.feed("ink>秘密内容</th")
        out3 = f.feed("ink>后半")
        joined = out1 + out2 + out3 + f.flush()
        assert joined == "前半后半"
        assert "秘密" not in joined

    def test_uppercase_think_variant(self):
        f = ThinkBlockFilter()
        assert f.feed("<THINK>独白</THINK>答案") == "答案"

    def test_fence_block_suppressed(self):
        f = ThinkBlockFilter()
        out = f.feed("```think\n内心戏\n```正文")
        assert out == "正文"

    def test_unclosed_block_dropped(self):
        f = ThinkBlockFilter()
        assert f.feed("<think>没说完") == ""
        assert f.flush() == ""

    def test_flush_releases_holdback(self):
        f = ThinkBlockFilter()
        assert f.feed("结尾<") == "结尾"
        assert f.flush() == "<"


# ---- 流式 HTTP 假件 -----------------------------------------------------------


class _FakeStreamResponse:
    def __init__(self, lines: list[str], status: int = 200):
        self._lines = lines
        self.status_code = status

    def raise_for_status(self):
        return self

    async def aread(self) -> bytes:
        return b""

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _FakeAsyncClient:
    """替 httpx.AsyncClient：stream() 返回预置行序列。"""

    def __init__(self, lines: list[str], *a, **k):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url, **kwargs):
        return _FakeStreamCtx(_FakeStreamResponse(self._lines))


def _fake_client_factory(lines: list[str]):
    """monkeypatch 用工厂：httpx.AsyncClient(timeout=...) → 预置行序列假客户端。"""

    def factory(*a, **k):
        return _FakeAsyncClient(lines)

    return factory


# ---- 2. OllamaClient.chat_stream ---------------------------------------------


class TestOllamaChatStream:
    async def test_ndjson_frames_yield_deltas(self, monkeypatch):
        import agent.llm.ollama as ollama_mod

        monkeypatch.setattr(
            ollama_mod.httpx,
            "AsyncClient",
            _fake_client_factory(
                [
                    '{"message":{"content":"你"},"done":false}',
                    "",  # 空行噪声
                    '{"message":{"content":"好"},"done":false}',
                    '{"message":{"content":"！"},"done":true,"eval_count":3}',
                ]
            ),
        )
        client = ollama_mod.OllamaClient(base_url="http://ollama-stream.test", model="m")
        deltas = [d async for d in client.chat_stream([{"role": "user", "content": "hi"}])]
        assert "".join(deltas) == "你好！"

    async def test_think_blocks_filtered(self, monkeypatch):
        import agent.llm.ollama as ollama_mod

        monkeypatch.setattr(
            ollama_mod.httpx,
            "AsyncClient",
            _fake_client_factory(
                [
                    '{"message":{"content":"<think>"},"done":false}',
                    '{"message":{"content":"内心戏"},"done":false}',
                    '{"message":{"content":"</think>"},"done":false}',
                    '{"message":{"content":"答案"},"done":true}',
                ]
            ),
        )
        client = ollama_mod.OllamaClient(base_url="http://ollama-think.test", model="m")
        deltas = [d async for d in client.chat_stream([{"role": "user", "content": "hi"}])]
        joined = "".join(deltas)
        assert joined == "答案"
        assert "内心戏" not in joined

    async def test_disabled_client_raises(self):
        from agent.llm.ollama import OllamaClient, OllamaUnavailableError

        client = OllamaClient(base_url="http://x", model="m", enabled=False)
        with pytest.raises(OllamaUnavailableError):
            async for _ in client.chat_stream([{"role": "user", "content": "hi"}]):
                pass


# ---- 3. PrivateLLMClient.chat_stream -----------------------------------------


class TestPrivateChatStream:
    async def test_sse_frames_yield_deltas(self, monkeypatch):
        import agent.llm.private_llm as private_mod

        monkeypatch.setattr(
            private_mod.httpx,
            "AsyncClient",
            _fake_client_factory(
                [
                    'data: {"choices":[{"delta":{"content":"你"}}]}',
                    'data: {"choices":[{"delta":{"content":"好"}}]}',
                    "data: [DONE]",
                ]
            ),
        )
        client = private_mod.PrivateLLMClient(
            base_url="http://private-stream.test/v1", api_key="", model="m"
        )
        deltas = [d async for d in client.chat_stream([{"role": "user", "content": "hi"}])]
        assert "".join(deltas) == "你好"


# ---- 4. LMRouter.summarise_stream --------------------------------------------


class _StreamBackend:
    """具备 chat_stream 的假后端。"""

    def __init__(self, base_url: str, chunks: list[str], fail_after: int | None = None):
        self.base_url = base_url
        self._chunks = chunks
        self._fail_after = fail_after

    async def chat_stream(self, messages):
        for i, chunk in enumerate(self._chunks):
            if self._fail_after is not None and i >= self._fail_after:
                raise RuntimeError("stream broken mid-way")
            yield chunk


class _NoStreamBackend:
    base_url = "http://no-stream.test"


def _make_router(monkeypatch):
    import agent.llm.router as router_mod
    from agent.llm.router import LMRouter

    # L1 响应缓存是模块级全局：同 prompt 的用例会互相短路，默认关掉，
    # 缓存专项用例自己再打开。
    monkeypatch.setattr(router_mod, "_L1_ENABLED", False)
    r = LMRouter()
    r._mock_mode = False
    return r


class TestSummariseStream:
    async def test_first_streaming_backend_wins(self, monkeypatch):
        r = _make_router(monkeypatch)
        r.private = _StreamBackend("http://p1.test", ["流式", "终答"])
        r.ollama = _NoStreamBackend()
        got: list[str] = []

        async def on_delta(d):
            got.append(d)

        answer, sources = await r.summarise_stream(
            intent="query", user_prompt="问题", plan=[], results=[], on_delta=on_delta
        )
        assert answer == "流式终答"
        assert sources == []
        assert "".join(got) == "流式终答"

    async def test_failure_before_delta_falls_to_next_candidate(self, monkeypatch):
        r = _make_router(monkeypatch)
        # 首 delta 发出前就断流 → 异常路径切下一个流式候选
        r.private = _StreamBackend("http://p2.test", ["不该发出"], fail_after=0)
        r.ollama = _StreamBackend("http://o2.test", ["本地兜底"])
        got: list[str] = []

        async def on_delta(d):
            got.append(d)

        answer, _ = await r.summarise_stream(
            intent="query", user_prompt="问题", plan=[], results=[], on_delta=on_delta
        )
        assert answer == "本地兜底"
        assert "".join(got) == "本地兜底"

    async def test_failure_after_delta_falls_back_to_non_stream(self, monkeypatch):
        r = _make_router(monkeypatch)
        # 首个 delta 后断流：不得再换流式后端拼第二段草稿，直接非流式兜底
        r.private = _StreamBackend("http://p3.test", ["草稿", "后续"], fail_after=1)
        r.ollama = _StreamBackend("http://o3.test", ["不该被用到"])

        async def fallback_summarise(**kwargs):
            return "非流式终答", ["src"]

        monkeypatch.setattr(r, "summarise", fallback_summarise)
        got: list[str] = []

        async def on_delta(d):
            got.append(d)

        answer, sources = await r.summarise_stream(
            intent="query", user_prompt="问题", plan=[], results=[], on_delta=on_delta
        )
        assert answer == "非流式终答"
        assert sources == ["src"]
        assert got == ["草稿"]  # 仅断流前的草稿被推送，终稿由 message 事件覆盖

    async def test_mock_mode_emits_single_delta(self, monkeypatch):
        r = _make_router(monkeypatch)
        r._mock_mode = True

        class _Mock:
            async def summarise(self, **kwargs):
                return "mock终答", []

        r.mock = _Mock()
        got: list[str] = []

        async def on_delta(d):
            got.append(d)

        answer, _ = await r.summarise_stream(
            intent="query", user_prompt="问题", plan=[], results=[], on_delta=on_delta
        )
        assert answer == "mock终答"
        assert got == ["mock终答"]

    async def test_l1_cache_hit_emits_single_delta(self, monkeypatch):
        import agent.llm.router as router_mod

        r = _make_router(monkeypatch)
        r.private = _StreamBackend("http://p4.test", ["不该被用到"])

        class _FakeCache:
            def __init__(self):
                self.store: dict[str, str] = {}

            def get(self, key):
                return self.store.get(key)

            def put(self, key, value):
                self.store[key] = value

        cache = _FakeCache()
        monkeypatch.setattr(router_mod, "_L1_ENABLED", True)
        monkeypatch.setattr(router_mod, "_L1_RESPONSE_CACHE", cache)
        # 预热：先用流式后端跑一遍写入缓存
        answer1, _ = await r.summarise_stream(
            intent="query", user_prompt="缓存问题", plan=[], results=[]
        )
        assert answer1 == "不该被用到"
        # 二跑：命中缓存单帧直出（把后端换成必炸对象证明没走网络）
        r.private = _NoStreamBackend()
        got: list[str] = []

        async def on_delta(d):
            got.append(d)

        answer2, _ = await r.summarise_stream(
            intent="query", user_prompt="缓存问题", plan=[], results=[], on_delta=on_delta
        )
        assert answer2 == "不该被用到"
        assert got == ["不该被用到"]


# ---- 5. responder 流式接入 ----------------------------------------------------


class _StreamLLM:
    def __init__(self, chunks: tuple[str, ...] = ("流式", "终答")):
        self.chunks = chunks
        self.summarise_calls = 0
        self.stream_calls = 0

    async def summarise_stream(self, *, on_delta=None, **kwargs):
        self.stream_calls += 1
        for c in self.chunks:
            if on_delta:
                await on_delta(c)
        return "".join(self.chunks), []

    async def summarise(self, **kwargs):
        self.summarise_calls += 1
        return "非流式终答", []


def _main_agent_state(prompt: str = "介绍一下你自己") -> dict:
    from agent.graph.state import empty_state

    st = empty_state(prompt)
    st["run_id"] = "run-answer-stream"
    st["intent"] = "query"
    st["decompose_decision"] = {"decision": {"mode": "MAIN_AGENT", "clarifying_questions": []}}
    return st


class TestResponderStreaming:
    async def test_deltas_emitted_and_final_answer_returned(self):
        from agent.graph.nodes.responder import responder_node

        await flush_builtin_events()
        llm = _StreamLLM()
        out = await responder_node(_main_agent_state(), llm)

        assert out["final_answer"] == "流式终答"
        assert llm.stream_calls == 1
        events = await consume_builtin_events()
        deltas = [p for k, p in events if k == "answer_delta"]
        assert [d["delta"] for d in deltas] == ["流式", "终答"]
        assert deltas[0]["runId"] == "run-answer-stream"
        # 所有 delta 共享同一 msgId（终答 message 事件据此原地覆盖）
        assert len({d["msgId"] for d in deltas}) == 1
        await flush_builtin_events()

    async def test_toggle_off_uses_plain_summarise(self, monkeypatch):
        from agent.config import settings
        from agent.graph.nodes.responder import responder_node

        monkeypatch.setattr(settings, "answer_stream_enabled", False)
        llm = _StreamLLM()
        out = await responder_node(_main_agent_state(), llm)

        assert out["final_answer"] == "非流式终答"
        assert llm.summarise_calls == 1
        assert llm.stream_calls == 0

    async def test_llm_without_stream_support_uses_plain_summarise(self):
        from agent.graph.nodes.responder import responder_node

        class _PlainLLM:
            async def summarise(self, **kwargs):
                return "普通终答", []

        out = await responder_node(_main_agent_state(), _PlainLLM())
        assert out["final_answer"] == "普通终答"


# ---- 6. stream.py msgId 种子化 ------------------------------------------------


class _ValuesGraph:
    """单 values 块假图：直接给出 final_answer。"""

    async def astream(self, initial_state, cfg, stream_mode=None):
        yield ("values", {"final_answer": "流式终答全文"})


class TestStreamMsgIdSeeding:
    async def test_message_event_reuses_delta_msg_id(self):
        from agent.graph.stream import stream_graph_events

        await flush_builtin_events()
        await emit_answer_delta(run_id="run-seed", msg_id="seed-msg-id", delta="流式")

        events = [e async for e in stream_graph_events(_ValuesGraph(), "run-seed", "ping")]
        await flush_builtin_events()

        # answer_delta 被转发到 SSE
        delta_events = [e for e in events if e["event"] == "agent://answer_delta"]
        assert delta_events, "answer_delta 应经 builtin 队列 drain 进 SSE 流"
        assert json.loads(delta_events[0]["data"])["msgId"] == "seed-msg-id"

        # 终答 message 复用 delta 的 msgId（#142 同 id 原地覆盖）
        msg_events = [e for e in events if e["event"] == "message"]
        assert msg_events
        message = json.loads(msg_events[0]["data"])["message"]
        assert message["id"] == "seed-msg-id"
        assert message["content"] == "流式终答全文"
