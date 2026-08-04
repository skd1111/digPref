"""test_loganalysis —— Phase 2F+ V1 日志分析引擎测试。

覆盖：
- models.py 数据类 + Level 常量
- scrubber.py PII 脱敏（手机 / 身份证 / 银行卡 / IP / 邮箱 / 高熵 token / AWS / JWT）
- extractor.py ERROR 块提取 + 日志级别检测 + 去重
- storage.py SQLite DAO（3 张表 CRUD + 缓存命中 + 清理）
- router.py LLM 路由兜底（mock / classify 走 detect_level 正则）
- BLOB 编解码一致性（与 logviewer/storage.rs 对齐）
"""
from __future__ import annotations

import time
import zlib

import pytest

from agent.loganalysis.models import (
    AnalysisCacheEntry,
    ErrorBlock,
    LEVEL_DEBUG,
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_WARN,
    RootCauseRequest,
)
from agent.loganalysis import extractor, scrubber
from agent.loganalysis.storage import (
    LogAnalysisStorage,
    decode_u64_le,
    encode_u64_le,
    reset_default_storage,
)


# ---- models ---------------------------------------------------------------


def test_models_constants():
    from agent.loganalysis.models import ALL_LEVELS
    assert "ERROR" in ALL_LEVELS
    assert "INFO" in ALL_LEVELS


def test_error_block_new():
    b = ErrorBlock(
        start_line=10, end_line=20,
        header="ERROR foo", stack_trace=["ERROR foo", "  at bar()"],
        level=LEVEL_ERROR,
    )
    assert b.start_line == 10
    d = b.to_dict()
    assert d["start_line"] == 10
    assert d["line_count"] == 2


def test_analysis_cache_entry_new_and_expiry():
    e = AnalysisCacheEntry.new(
        cache_key="abc", file_path="/x.log", file_fingerprint="123:456",
        analysis_type="log_root_cause", payload_json="{}", ttl_sec=60,
    )
    assert e.is_expired() is False
    # 直接改 expires_at
    e.expires_at = int(time.time()) - 1
    assert e.is_expired() is True


# ---- BLOB 编解码 ----------------------------------------------------------


def test_encode_decode_u64_le_roundtrip():
    vals = [0, 1, 100, 2**32, 2**63 - 1, 9999999999]
    blob = encode_u64_le(vals)
    assert isinstance(blob, bytes)
    out = decode_u64_le(blob)
    assert out == vals


def test_encode_decode_empty():
    assert encode_u64_le([]) == b""
    assert decode_u64_le(b"") == []


def test_encode_decode_truncated_bytes():
    """bytes 长度不是 8 倍数 → 截断到最近 8 倍数（与 logviewer/storage.rs 对齐）。"""
    blob = b"\x01\x02\x03\x04"  # 4 bytes, not 8 multiple
    # 当前实现：n = len/8 = 0 → 返空 list
    assert decode_u64_le(blob) == []


# ---- scrubber -------------------------------------------------------------


def test_scrub_text_phone_cn():
    """11 位中国大陆手机号脱敏。"""
    out = scrubber.scrub_text("用户 13812345678 登录")
    assert "13812345678" not in out
    assert "[REDACTED:PHONE]" in out


def test_scrub_text_id_card():
    """18 位身份证脱敏（含 X）。"""
    out = scrubber.scrub_text("身份证 11010119900307881X 验证通过")
    assert "11010119900307881X" not in out
    assert "[REDACTED:ID_CARD]" in out


def test_scrub_text_bank_card():
    """银行卡脱敏（带空格 / 横线）。"""
    out = scrubber.scrub_text("卡号 6225 1234 5678 9012 信用卡")
    assert "6225" not in out
    assert "[REDACTED:BANK_CARD]" in out


def test_scrub_text_ipv4():
    out = scrubber.scrub_text("连接 192.168.1.1 失败")
    assert "192.168.1.1" not in out
    assert "[REDACTED:IPV4]" in out


def test_scrub_text_email():
    out = scrubber.scrub_text("发送至 user@example.com 成功")
    assert "user@example.com" not in out
    assert "[REDACTED:EMAIL]" in out


def test_scrub_text_aws_key():
    out = scrubber.scrub_text("AKIAIOSFODNN7EXAMPLE 是测试 key")
    assert "AKIAIOSFODNN7EXAMPLE" not in out
    assert "[REDACTED:AWS_KEY]" in out


def test_scrub_text_high_entropy_token():
    long_token = "a" * 40
    out = scrubber.scrub_text(f"token={long_token}")
    assert long_token not in out
    assert "[REDACTED:TOKEN]" in out


def test_scrub_text_empty():
    assert scrubber.scrub_text("") == ""
    assert scrubber.scrub_text("正常日志没有敏感信息") == "正常日志没有敏感信息"


def test_scrub_lines():
    out = scrubber.scrub_lines(["user 13812345678 ok", "no pii here"])
    assert out[0] == "user [REDACTED:PHONE] ok"
    assert out[1] == "no pii here"


def test_scrub_error_block_returns_new_block():
    b = ErrorBlock(
        start_line=1, end_line=2,
        header="ERROR at 13812345678",
        stack_trace=["ERROR at 13812345678", "  at com.example.X"],
    )
    s = scrubber.scrub_error_block(b)
    # 原 block 不变
    assert "13812345678" in b.header
    # 新 block 已脱敏
    assert "[REDACTED:PHONE]" in s.header
    assert s is not b


def test_scrub_error_blocks_recomputes_fingerprint():
    b = ErrorBlock(
        start_line=1, end_line=1, header="ERROR x",
        stack_trace=["ERROR x"],
    )
    blocks = scrubber.scrub_error_blocks([b])
    # fingerprint 应被重算
    expected = f"{zlib.adler32(b'ERROR x') & 0xffffffff:08x}"
    # 注：scrub 后内容相同（无 PII）→ fingerprint 同
    assert blocks[0].fingerprint == expected


# ---- extractor ------------------------------------------------------------


def test_detect_level_error():
    assert extractor.detect_level("2026-07-29 ERROR something failed") == LEVEL_ERROR
    assert extractor.detect_level("[FATAL] crash") == "FATAL" or extractor.detect_level("[FATAL] crash") == "ERROR"
    # 实际：FATAL 优先
    assert extractor.detect_level("FATAL System out of memory") == "FATAL"


def test_detect_level_warn_info_debug():
    assert extractor.detect_level("WARN connection slow") == LEVEL_WARN
    assert extractor.detect_level("INFO starting up") == LEVEL_INFO
    assert extractor.detect_level("DEBUG trace x") == LEVEL_DEBUG
    assert extractor.detect_level("random line") == LEVEL_INFO  # fallback


def test_detect_level_empty():
    assert extractor.detect_level("") == LEVEL_INFO


def test_assert_known_level():
    assert extractor.assert_known_level("ERROR") == "ERROR"
    assert extractor.assert_known_level("INVALID") == LEVEL_INFO  # fallback


def test_extract_error_blocks_basic():
    lines = [
        "INFO starting",
        "2026-07-29 ERROR connection refused",
        "  at java.net.Socket",
        "    at com.example.X",
        "INFO recovered",
        "ERROR another failure",
        "  at java.io.File",
    ]
    blocks = extractor.extract_error_blocks(lines, max_stack_lines=50, max_blocks=10)
    assert len(blocks) == 2
    assert blocks[0].start_line == 2
    assert blocks[0].header.startswith("2026-07-29 ERROR")
    assert "at java.net.Socket" in blocks[0].stack_trace[1]
    assert blocks[1].start_line == 6


def test_extract_error_blocks_dedup():
    """同 stack fingerprint 的块只保留首个。"""
    # 紧挨着两次相同 ERROR + 堆栈（无 INFO 分隔）—— 应去重
    lines = [
        "ERROR foo",
        "  at java.X",
        "ERROR foo",  # 同 header / 同 stack
        "  at java.X",
    ]
    blocks = extractor.extract_error_blocks(lines, max_stack_lines=50)
    assert len(blocks) == 1


def test_extract_error_blocks_separated_by_info():
    """INFO / DEBUG 等非堆栈行分隔相邻的相同 ERROR → 视为两个独立块（最终都因 fingerprint 重复 → 仅保留首个）。"""
    lines = [
        "ERROR foo",
        "  at java.X",
        "INFO middle",
        "ERROR foo",
        "  at java.X",
    ]
    blocks = extractor.extract_error_blocks(lines, max_stack_lines=50)
    # 两次 ERROR stack 内容相同 → fingerprint 相同 → dedup 保留首个
    assert len(blocks) == 1


def test_extract_error_blocks_truncates_long_stack():
    """超过 max_stack_lines 自动收尾。"""
    lines = ["ERROR start"]
    for _ in range(60):
        lines.append("    at deep.stack.frame")
    blocks = extractor.extract_error_blocks(lines, max_stack_lines=10, max_blocks=10)
    assert len(blocks) == 1
    assert len(blocks[0].stack_trace) <= 11  # 含 header


def test_extract_error_blocks_max_blocks():
    lines = [f"ERROR msg-{i}\n  at frame" for i in range(5)]
    blocks = extractor.extract_error_blocks(lines, max_blocks=3)
    assert len(blocks) == 3


def test_extract_error_blocks_empty():
    assert extractor.extract_error_blocks([]) == []
    assert extractor.extract_error_blocks(["INFO normal log"] * 5) == []


def test_extract_error_blocks_iterable_input():
    """Iterable 输入也能正常处理（不是 list 也能迭代）。"""
    gen = (line for line in ["ERROR x", "  at y"])
    blocks = extractor.extract_error_blocks(gen)
    assert len(blocks) == 1


# ---- storage --------------------------------------------------------------


@pytest.fixture
def storage(tmp_path):
    db = tmp_path / "log.db"
    s = LogAnalysisStorage(str(db))
    yield s


def test_storage_ensure_schema(storage):
    """schema 自动建表。"""
    stats = storage.get_stats()
    assert stats["search_cache_rows"] == 0
    assert stats["tail_sessions_rows"] == 0
    assert stats["log_analysis_cache_rows"] == 0


def test_search_cache_crud(storage):
    storage.upsert_search_cache(
        "/x.log", "ERROR", "literal", "fp1",
        matched_lines=[10, 20, 30],
    )
    # 命中
    out = storage.get_search_cache("/x.log", "ERROR", "literal", "fp1")
    assert out is not None
    matched, count = out
    assert matched == [10, 20, 30]
    assert count == 3
    # fingerprint 不一致 → miss
    assert storage.get_search_cache("/x.log", "ERROR", "literal", "fp2") is None
    # pattern 不一致 → miss
    assert storage.get_search_cache("/x.log", "WARN", "literal", "fp1") is None


def test_search_cache_expiry(storage):
    storage.upsert_search_cache(
        "/x.log", "ERROR", "literal", "fp1", [1], ttl_sec=1,
    )
    assert storage.get_search_cache("/x.log", "ERROR", "literal", "fp1") is not None
    # 强制过期
    storage.cleanup_search_cache(now=int(time.time()) + 100)
    assert storage.get_search_cache("/x.log", "ERROR", "literal", "fp1") is None


def test_search_cache_upsert_replaces_old(storage):
    storage.upsert_search_cache("/x.log", "ERROR", "literal", "fp1", [1, 2])
    storage.upsert_search_cache("/x.log", "ERROR", "literal", "fp1", [3, 4, 5])
    matched, count = storage.get_search_cache("/x.log", "ERROR", "literal", "fp1")
    assert matched == [3, 4, 5]
    assert count == 3


def test_tail_session_crud(storage):
    sid = "abc-123"
    storage.create_tail_session(sid, "/x.log")
    s = storage.get_tail_session(sid)
    assert s is not None
    assert s["file_path"] == "/x.log"
    assert s["last_position"] == 0
    assert s["lines_emitted"] == 0

    # 更新位置 + emit 计数
    storage.update_tail_session(sid, last_position=100, lines_emitted_increment=5)
    s = storage.get_tail_session(sid)
    assert s["last_position"] == 100
    assert s["lines_emitted"] == 5

    # 再 emit 5
    storage.update_tail_session(sid, lines_emitted_increment=5)
    s = storage.get_tail_session(sid)
    assert s["lines_emitted"] == 10

    # 结束
    assert storage.end_tail_session(sid) is True
    s = storage.get_tail_session(sid)
    assert s["ended_at"] is not None
    # 二次结束返 False
    assert storage.end_tail_session(sid) is False


def test_tail_session_list_active(storage):
    storage.create_tail_session("s1", "/a.log")
    storage.create_tail_session("s2", "/b.log")
    storage.end_tail_session("s2")
    active = storage.list_active_tail_sessions()
    assert len(active) == 1
    assert active[0]["session_id"] == "s1"
    by_file = storage.list_active_tail_sessions(file_path="/a.log")
    assert len(by_file) == 1
    assert storage.list_active_tail_sessions(file_path="/b.log") == []


def test_analysis_cache_crud(storage):
    e = AnalysisCacheEntry.new(
        cache_key="k1",
        file_path="/x.log",
        file_fingerprint="fp1",
        analysis_type="log_root_cause",
        payload_json='{"summary":"test"}',
    )
    storage.upsert_analysis_cache(e)
    got = storage.get_analysis_cache("k1")
    assert got is not None
    assert got.payload_json == '{"summary":"test"}'
    assert got.analysis_type == "log_root_cause"

    # 过期
    e2 = AnalysisCacheEntry.new(
        cache_key="k2", file_path="/y.log", file_fingerprint="fp2",
        analysis_type="log_level_classify", payload_json="{}",
        ttl_sec=1,
    )
    storage.upsert_analysis_cache(e2)
    storage.cleanup_analysis_cache(now=int(time.time()) + 100)
    assert storage.get_analysis_cache("k2") is None
    # k1 还在
    assert storage.get_analysis_cache("k1") is not None


def test_get_stats(storage):
    storage.upsert_search_cache("/x.log", "ERROR", "literal", "fp1", [1])
    storage.create_tail_session("s1", "/x.log")
    e = AnalysisCacheEntry.new(
        cache_key="k1", file_path="/x.log", file_fingerprint="fp1",
        analysis_type="log_root_cause", payload_json="{}",
    )
    storage.upsert_analysis_cache(e)

    stats = storage.get_stats()
    assert stats["search_cache_rows"] == 1
    assert stats["tail_sessions_rows"] == 1
    assert stats["tail_sessions_active"] == 1
    assert stats["log_analysis_cache_rows"] == 1


# ---- router.py LLM 调度（mock 路径）----------------------------------------


class _FakeLLMRouter:
    """模拟 LMRouter（不需要真 LLM）。"""
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls: list[str] = []

    def pick(self, kind: str):
        """路由选 client —— 测试返 self（自身有 _chat_completion）。"""
        return self

    async def summarise(self, prompt: str, max_tokens: int = 800) -> str:
        if self.fail:
            raise RuntimeError("LLM unavailable")
        self.calls.append(prompt)
        return "private: 错误模式是连接超时，根因是 Redis 不可达。"

    async def _chat_completion(self, messages: list[dict], **kwargs) -> dict:
        """router.py 调用 _chat_completion 直接拿 OpenAI 兼容响应。"""
        if self.fail:
            raise RuntimeError("LLM unavailable")
        return {
            "choices": [
                {"message": {"role": "assistant", "content": "private: 错误模式是连接超时，根因是 Redis 不可达。"}}
            ],
            "usage": {"total_tokens": 100},
        }

    async def classify_log_levels(self, lines: list[str]) -> list[dict]:
        if self.fail:
            raise RuntimeError("LLM unavailable")
        return [{"level": "ERROR" if "ERROR" in l else "INFO", "confidence": 0.9} for l in lines]


@pytest.mark.asyncio
async def test_analyze_root_cause_private_llm():
    """私有 LLM 调用成功路径。"""
    from agent.loganalysis import router as llm_router

    blocks = [
        ErrorBlock(1, 5, "ERROR connection refused", ["ERROR connection refused", "  at java.X"], LEVEL_ERROR),
        ErrorBlock(10, 15, "ERROR timeout", ["ERROR timeout", "  at java.Y"], LEVEL_ERROR),
    ]
    req = RootCauseRequest(file_path="/x.log", error_blocks=blocks, max_tokens=3000)
    llm = _FakeLLMRouter()
    resp = await llm_router.analyze_root_cause(req, llm=llm, scrubbed_blocks=blocks)
    assert "private" in resp.backend
    assert "根因" in resp.summary or "连接" in resp.summary
    assert resp.error_count == 2
    assert resp.tokens_used > 0


@pytest.mark.asyncio
async def test_analyze_root_cause_fallback_to_mock():
    """私有 LLM 失败 → mock 兜底。"""
    from agent.loganalysis import router as llm_router

    blocks = [
        ErrorBlock(1, 2, "ERROR foo", ["ERROR foo"], LEVEL_ERROR),
    ]
    req = RootCauseRequest(file_path="/x.log", error_blocks=blocks)
    resp = await llm_router.analyze_root_cause(
        req, llm=_FakeLLMRouter(fail=True), scrubbed_blocks=blocks,
    )
    assert resp.backend == "mock"
    assert "Mock 摘要" in resp.summary


@pytest.mark.asyncio
async def test_analyze_root_cause_cache_hit():
    """缓存命中 → 跳过 LLM 调用。"""
    from agent.loganalysis import router as llm_router

    blocks = [ErrorBlock(1, 2, "ERROR x", ["ERROR x"], LEVEL_ERROR)]
    req = RootCauseRequest(file_path="/x.log", error_blocks=blocks)
    llm = _FakeLLMRouter()
    # 构造一个"过期"的 cache_lookup（payload 是 RootCauseResponse dict）
    import json
    payload = json.dumps({
        "summary": "[cached] hello",
        "error_count": 1,
        "blocks_analyzed": 1,
        "tokens_used": 0,
        "model_used": "cached",
        "elapsed_ms": 0,
        "backend": "cache",
        "blocks": [],
    })
    cache_entry = AnalysisCacheEntry(
        cache_key="k", file_path="/x.log", file_fingerprint="fp",
        analysis_type="log_root_cause", payload_json=payload,
        created_at=int(time.time()), expires_at=int(time.time()) + 600,
    )
    resp = await llm_router.analyze_root_cause(
        req, llm=llm, scrubbed_blocks=blocks, cache_lookup=cache_entry,
    )
    assert resp.backend == "cache"
    assert "[cached]" in resp.summary
    # LLM 未被调
    assert len(llm.calls) == 0


@pytest.mark.asyncio
async def test_analyze_root_cause_token_truncation():
    """超 max_tokens → 截断 + truncated。"""
    from agent.loganalysis import router as llm_router

    # 100 个块，每块 100 行（每行 50 字符 → ~1250 token/块）→ 远超 3000
    blocks = []
    for i in range(20):
        stack = [f"ERROR big stack {i} {j}" + "x" * 50 for j in range(80)]
        blocks.append(ErrorBlock(i * 100, i * 100 + 80, f"ERROR #{i}", stack, LEVEL_ERROR))
    req = RootCauseRequest(file_path="/x.log", error_blocks=blocks, max_tokens=2000)
    resp = await llm_router.analyze_root_cause(
        req, llm=_FakeLLMRouter(), scrubbed_blocks=blocks,
    )
    # 只分析前 N 块（受 max_tokens 约束）
    assert resp.blocks_analyzed < 20


@pytest.mark.asyncio
async def test_classify_log_levels_private():
    """通过 fake llm 的 classify_log_levels 方法识别日志级别。"""
    from agent.loganalysis import router as llm_router

    lines = ["INFO starting", "ERROR connection failed", "WARN slow"]
    resp = await llm_router.classify_log_levels(lines, llm=_FakeLLMRouter())
    # router 优先调 llm.classify_log_levels → 命中 → backend='llm'
    assert resp.backend == "llm"
    assert len(resp.results) == 3


@pytest.mark.asyncio
async def test_classify_log_levels_fallback_to_mock():
    """无 LLM（local_small + ollama 都不可用）→ mock 正则兜底。"""
    from agent.loganalysis import router as llm_router

    class _NoLLM:
        async def classify_log_levels(self, lines):
            raise RuntimeError("nope")

    # 先 patch local_small 和 ollama 不可用
    lines = ["INFO a", "ERROR b", "WARN c"]
    # _FakeLLMRouter 模拟无 classify_log_levels
    class _NoClassifyLLM:
        async def classify_log_levels(self, lines):
            raise RuntimeError("nope")
        pass
    resp = await llm_router.classify_log_levels(lines, llm=_NoClassifyLLM())
    # 兜底走 detect_level
    assert resp.backend == "mock"
    assert len(resp.results) == 3


# ---- 集成：scrubber + extractor 联动 ---------------------------------------


def test_extract_then_scrub():
    """提取 + 脱敏完整链路。"""
    lines = [
        "INFO starting",
        "ERROR 用户 13812345678 失败",
        "  at java.X",
        "ERROR 用户 13888888888 失败",
        "  at java.Y",
    ]
    blocks = extractor.extract_error_blocks(lines)
    assert len(blocks) == 2
    scrubbed = scrubber.scrub_error_blocks(blocks)
    # 每个块的 header（错误行）必须脱敏；堆栈行不含敏感信息（仅 java.X/Y）
    for s in scrubbed:
        assert "[REDACTED:PHONE]" in s.header
        # 没有任何 stack_trace 行包含原手机号
        for line in s.stack_trace:
            assert "13812345678" not in line
            assert "13888888888" not in line


# ---- 配置 / 单例 -----------------------------------------------------------


def test_get_default_storage(monkeypatch, tmp_path):
    """单例默认 db 路径含 'log_analysis.db'。"""
    from agent.loganalysis import storage as storage_mod
    storage_mod.reset_default_storage()
    s = storage_mod.get_default_storage()
    # 默认路径以 log_analysis.db 结尾（settings.log_analysis_db_path 或 ~/.eaide/log_analysis.db）
    assert s.db_path.endswith("log_analysis.db")
    storage_mod.reset_default_storage()