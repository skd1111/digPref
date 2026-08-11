"""loganalysis.router —— Phase 2F+ V1 LLM 路由（dispatch 到 local / private / mock）。

设计：
- 两个 analysis_type：
    - log_root_cause    —— 走 private LLM（内网大模型）；PII 脱敏后送
    - log_level_classify —— 走 local_small（端侧 0.3B）；PII 不需要脱敏（只识别级别）
- 全部经过 LMRouter.pick() 选择后端；失败兜底按 LMRouter fallback 链
- 不直接 import private_llm / local_small —— 通过 LMRouter 解耦（CLAUDE.md §2 红线）

CLAUDE.md §2 `_LOCAL_ONLY_TASKS`：
- `log_level_classify` 加入（V1）—— 永远走 Ollama / local_small
- `log_root_cause` **不**加入 —— 内网 LLM；但强制走 PII 脱敏 + Top-N 截断
"""

from __future__ import annotations

import logging
import time

from agent.llm.mock import MockLLMClient
from agent.llm.prompts import load_prompt, render_prompt
from agent.llm.router import LMRouter
from agent.loganalysis.models import (
    AnalysisCacheEntry,
    ErrorBlock,
    LogLevelClassifyResponse,
    LogLevelResult,
    RootCauseRequest,
    RootCauseResponse,
)

logger = logging.getLogger(__name__)


# ---- 公开 API -------------------------------------------------------------


async def analyze_root_cause(
    req: RootCauseRequest,
    *,
    llm: LMRouter,
    scrubbed_blocks: list[ErrorBlock],
    cache_lookup: AnalysisCacheEntry | None = None,
) -> RootCauseResponse:
    """根因分析。

    流程：
        1. cache 命中 → 直接返（省 LLM 调用）
        2. 按 fingerprint 排序（频次优先）+ token 截断
        3. 拼 prompt → LMRouter.pick('plan') 走 private / ollama 链
        4. 包成 RootCauseResponse
    """
    started = time.monotonic()

    # 1. 缓存命中
    if cache_lookup and not cache_lookup.is_expired():
        try:
            import json

            data = json.loads(cache_lookup.payload_json)
            # 缓存里是 dict（之前 .to_dict() 序列化的）→ 构造响应
            return RootCauseResponse(
                summary=str(data.get("summary", "")),
                error_count=int(data.get("error_count", 0)),
                blocks_analyzed=int(data.get("blocks_analyzed", 0)),
                tokens_used=int(data.get("tokens_used", 0)),
                model_used=str(data.get("model_used", "")),
                elapsed_ms=0,
                backend="cache",
                blocks=scrubbed_blocks,
            )
        except Exception as e:
            logger.warning("cache payload deser failed, fall through: %s", e)

    # 2. 按 fingerprint 排序（频次优先）
    sorted_blocks = _sort_blocks_by_freq(scrubbed_blocks)
    selected_blocks, _truncated = _truncate_by_tokens(
        sorted_blocks,
        max_tokens=req.max_tokens,
    )

    # 3. 拼 prompt → 调 LLM（用传入的 llm，不重实例化 —— 测试可注入 fake）
    prompt = _build_root_cause_prompt(selected_blocks, req)
    backend = "private"
    model_used = ""
    tokens_used = 0
    summary = ""
    try:
        # 优先用 llm.pick("summarise") 拿到真实 client；fallback 到 private 客户端
        client = None
        try:
            if hasattr(llm, "pick"):
                client = llm.pick("summarise")
        except Exception:
            client = None
        if client is None:
            # 所有后端都不可用时降级 mock（不直接 import private_llm，遵守 CLAUDE.md §2）
            client = MockLLMClient()
        # 用 extract_chat 原始对话透传：根因分析是自由文本，
        # _chat_completion 会对正文 json.loads → 必然解析失败降级 mock（BUGFIX）。
        messages = [
            {"role": "system", "content": "你是日志根因分析专家。回答限制 800 字以内。"},
            {"role": "user", "content": prompt},
        ]
        if isinstance(client, MockLLMClient):
            summary = _mock_root_cause_summary(selected_blocks)
            backend = "mock"
            model_used = "mock"
            tokens_used = 0
        else:
            raw = await client.extract_chat(messages)
            summary = str(raw or "").strip()
            backend = "private"
            model_used = getattr(client, "model", "") or "private"
            tokens_used = len(prompt) // 4 + len(summary) // 4
    except Exception as e:
        logger.warning("private LLM failed for root_cause, fallback mock: %s", e)
        summary = _mock_root_cause_summary(selected_blocks)
        backend = "mock"
        model_used = "mock"
        tokens_used = 0

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return RootCauseResponse(
        summary=summary,
        error_count=len(scrubbed_blocks),
        blocks_analyzed=len(selected_blocks),
        tokens_used=tokens_used,
        model_used=model_used,
        elapsed_ms=elapsed_ms,
        backend=backend,
        blocks=selected_blocks,
    )


async def classify_log_levels(
    lines: list[str],
    *,
    llm: LMRouter,
) -> LogLevelClassifyResponse:
    """批量识别日志级别（端侧模型）。

    设计：
        - 优先用传入的 llm（fake or real）—— 测试可注入
        - 全失败 → detect_level() 正则兜底（mock）
    """
    started = time.monotonic()
    results: list[LogLevelResult] = []
    backend = "mock"

    if not lines:
        return LogLevelClassifyResponse(results=[], elapsed_ms=0, backend=backend)

    # 1. 尝试传入 llm 的 classify_log_levels
    try:
        if hasattr(llm, "classify_log_levels"):
            lls = await llm.classify_log_levels(lines)
            results = [
                LogLevelResult(
                    line=line,
                    predicted_level=lv.get("level", "INFO"),
                    confidence=float(lv.get("confidence", 0.0)),
                )
                for line, lv in zip(lines, lls)
            ]
            backend = "llm"
    except Exception as e:
        logger.debug("llm.classify_log_levels failed: %s", e)

    # 2. 尝试 llm.local_small.classify_log_levels
    if not results:
        try:
            local_small = getattr(llm, "local_small", None)
            if local_small is not None and hasattr(local_small, "classify_log_levels"):
                lls = await local_small.classify_log_levels(lines)
                results = [
                    LogLevelResult(
                        line=line,
                        predicted_level=lv.get("level", "INFO"),
                        confidence=float(lv.get("confidence", 0.0)),
                    )
                    for line, lv in zip(lines, lls)
                ]
                backend = "local_small"
        except Exception as e:
            logger.debug("llm.local_small.classify_log_levels failed: %s", e)

    # 3. 全失败 → 正则 detect_level 兜底
    if not results:
        from agent.loganalysis.extractor import detect_level

        results = [
            LogLevelResult(line=line, predicted_level=detect_level(line), confidence=0.5)
            for line in lines
        ]
        backend = "mock"

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return LogLevelClassifyResponse(
        results=results,
        elapsed_ms=elapsed_ms,
        backend=backend,
    )


# ---- 内部工具 -------------------------------------------------------------


def _sort_blocks_by_freq(blocks: list[ErrorBlock]) -> list[ErrorBlock]:
    """按 fingerprint 出现频次降序（同类错误优先）。"""
    freq: dict[str, int] = {}
    for b in blocks:
        freq[b.fingerprint] = freq.get(b.fingerprint, 0) + 1
    return sorted(blocks, key=lambda b: (-freq[b.fingerprint], b.start_line))


def _truncate_by_tokens(
    blocks: list[ErrorBlock],
    *,
    max_tokens: int,
) -> tuple[list[ErrorBlock], bool]:
    """按 token 上限截断（粗估 4 字符 ≈ 1 token）。"""
    selected: list[ErrorBlock] = []
    used = 0
    for b in blocks:
        block_tokens = sum(max(1, len(line) // 4) for line in b.stack_trace)
        if used + block_tokens > max_tokens:
            return selected, True
        selected.append(b)
        used += block_tokens
    return selected, False


def _build_root_cause_prompt(blocks: list[ErrorBlock], req: RootCauseRequest) -> str:
    """拼 LLM prompt（架构师约束：800 字以内回答），模板见 loganalysis/root_cause.md。"""
    block_lines: list[str] = []
    for i, b in enumerate(blocks, 1):
        block_lines.append(f"### 错误块 {i}（行 {b.start_line}-{b.end_line}）")
        block_lines.extend(b.stack_trace)
        block_lines.append("")

    context_section = ""
    if req.context_window_lines:
        context_section = (
            f"\n时序上下文（最近 {len(req.context_window_lines)} 行）：\n"
            + "\n".join(req.context_window_lines[-req.context_window :])
        )

    return render_prompt(
        load_prompt("loganalysis/root_cause"),
        FILE_PATH=req.file_path,
        BLOCK_COUNT=str(len(blocks)),
        CONTEXT=context_section,
        BLOCKS="\n".join(block_lines),
    )


def _mock_root_cause_summary(blocks: list[ErrorBlock]) -> str:
    """无 LLM 时的兜底摘要（仅给错误模式统计）。"""
    if not blocks:
        return "未检测到 ERROR 块；日志看起来正常。"
    freq: dict[str, int] = {}
    for b in blocks:
        # 截取 header 第一行前 60 字符做"模式"
        pattern = b.header[:60].strip()
        freq[pattern] = freq.get(pattern, 0) + 1
    top = sorted(freq.items(), key=lambda t: -t[1])[:3]
    parts = [f"  - {p} ×{c}" for p, c in top]
    return (
        "[Mock 摘要 —— 无可用 LLM]\n"
        f"检测到 {len(blocks)} 个 ERROR 块（去重后）；主要模式：\n" + "\n".join(parts)
    )
