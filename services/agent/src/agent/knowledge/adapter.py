"""knowledge.adapter —— Phase 4 V0 外部知识库适配器接口。

设计：
    - **不实现本地知识库引擎**（SQLite-vec + RAG 跳过；用户 2026-07-29 决策）
    - **保留外部知识库接口**（KnowledgeBaseAdapter Protocol）+ 配置（KBConfig）
    - V0 提供 MockKBAdapter（占位）；V1 接入 Notion / Confluence / 公司 wiki / GraphRAG
    - 与 sessions/knowledge_base.py 共享相同的 Protocol 定义

CLAUDE.md §2 红线：
    - KB 调用失败 → 返回空 context（best-effort，不阻塞 agent 决策）
    - KB 调用超时严格（默认 5s）
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---- 配置 ------------------------------------------------------------------


@dataclass
class KBConfig:
    """外部知识库配置。

    环境变量覆盖：
      - EAIDE_KB_BACKEND: 'mock' / 'notion' / 'confluence' / 'wiki' / 'graphrag'
      - EAIDE_KB_BASE_URL: e.g. 'https://wiki.company.com/api/v1'
      - EAIDE_KB_API_KEY_REF: Keyring 占位符名
    """

    backend: str = "mock"
    base_url: str = ""
    api_key_ref: str = ""
    timeout_s: float = 5.0
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> KBConfig:
        return cls(
            backend=os.environ.get("EAIDE_KB_BACKEND", "mock"),
            base_url=os.environ.get("EAIDE_KB_BASE_URL", ""),
            api_key_ref=os.environ.get("EAIDE_KB_API_KEY_REF", ""),
            timeout_s=float(os.environ.get("EAIDE_KB_TIMEOUT_S", "5")),
        )


# ---- 数据类 ---------------------------------------------------------------


@dataclass
class KBQueryResult:
    """单条 KB 检索结果。"""

    doc_id: str
    title: str
    snippet: str
    score: float = 0.0
    source_url: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class KBContext:
    """一次 KB 检索返回的 context。"""

    query: str
    results: list[KBQueryResult] = field(default_factory=list)
    backend: str = "mock"
    elapsed_ms: int = 0


# ---- 适配器协议 ----------------------------------------------------------


@runtime_checkable
class KnowledgeBaseAdapter(Protocol):
    """所有外部 KB 适配器必须实现的接口。"""

    def is_available(self) -> bool: ...

    async def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        timeout_s: float | None = None,
    ) -> KBContext: ...

    @property
    def name(self) -> str: ...


# ---- Mock 适配器 ----------------------------------------------------------


class MockKBAdapter:
    """V0 Mock 适配器：返回固定示例 context。"""

    name = "mock"

    def __init__(self, config: KBConfig | None = None):
        self._config = config or KBConfig.from_env()
        self._call_count = 0

    def is_available(self) -> bool:
        return True

    async def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        timeout_s: float | None = None,
    ) -> KBContext:
        import time

        self._call_count += 1
        t0 = time.monotonic()
        await asyncio.sleep(0.02)

        sample_results = [
            KBQueryResult(
                doc_id=f"kb-mock-{self._call_count}-1",
                title="[MOCK] 知识库示例文档",
                snippet=f"与「{query[:40]}」相关的内容片段（mock）。",
                score=0.85,
                source_url="https://kb.company.local/doc/example",
            ),
        ][:top_k]

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return KBContext(
            query=query,
            results=sample_results,
            backend=self.name,
            elapsed_ms=elapsed_ms,
        )


# ---- 工厂 -----------------------------------------------------------------

_ADAPTERS: dict[str, type] = {
    "mock": MockKBAdapter,
}


def build_adapter(config: KBConfig | None = None) -> KnowledgeBaseAdapter:
    """根据 config.backend 构造 KB 适配器实例。"""
    cfg = config or KBConfig.from_env()
    cls = _ADAPTERS.get(cfg.backend)
    if cls is None:
        logger.warning("[KB] unknown backend=%s, fallback to MockKBAdapter", cfg.backend)
        return MockKBAdapter(cfg)
    return cls(cfg)


# ---- 统一入口 -------------------------------------------------------------


async def build_kb_context(
    query: str,
    adapter: KnowledgeBaseAdapter | None = None,
    *,
    top_k: int = 3,
) -> KBContext:
    """统一调用入口：构造 adapter → 调 search → 返 KBContext。"""
    ad = adapter or build_adapter()
    if not ad.is_available():
        return KBContext(query=query, backend=ad.name)
    try:
        return await ad.search(query, top_k=top_k)
    except Exception as e:
        logger.warning("[KB] search failed backend=%s err=%s", ad.name, e)
        return KBContext(query=query, backend=ad.name)


def kb_context_to_prompt_snippet(ctx: KBContext, max_chars: int = 2000) -> str:
    """把 KBContext 拼成 system prompt 片段。"""
    if not ctx.results:
        return ""
    lines: list[str] = ["## 知识库参考（外部）"]
    for i, r in enumerate(ctx.results):
        lines.append(f"[{i}] {r.title}: {r.snippet}")
        lines.append(f"  来源: {r.source_url}")
    text = "\n".join(lines)
    return text[:max_chars]
