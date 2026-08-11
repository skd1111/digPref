"""sessions.knowledge_base —— Phase 6 V0 外部知识库适配器接口。

设计（来自 phase-6-session-mgmt.md §3.5 + 用户 2026-07-29 决策）：
    - **不实现本地知识库引擎**（Phase 4 SQLite-vec + RAG 不做）
    - **保留外部知识库接口**（`KnowledgeBaseAdapter`）+ 配置（`KBConfig`）
    - V0 提供 `MockKBAdapter`（占位 + 离线友好），V1 接入 Phase 4 / 第三方
      （Notion / Confluence / 公司 wiki / GraphRAG）时**只换 adapter 实现**，
      Phase 6 调用点（`build_kb_context`）零改动。

CLAUDE.md §2 红线：
    - 不读敏感上下文裸文：KB 查询结果经过 PII 脱敏（V1 接 Phase 4 redact.py；
      V0 Mock 返回固定脱敏示例）
    - KB 调用超时严格（防止外部 KB 慢响应阻塞会话）
    - KB 调用失败 → 返回空 context（best-effort，不阻塞 agent 决策）

不在 V0 内（V1 补）：
    - KB 查询重试 / 熔断（Phase 2C 路由机制可复用）
    - KB 缓存（Phase 4 SQLite-vec）
    - KB 权限校验（Phase 10 IAM）
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
    """外部知识库配置（V0 简化为 3 字段；V1 可扩展：auth / proxy / timeout）。

    环境变量覆盖（与 envconfig 同风格）：
      - `EAIDE_KB_BACKEND`: 'mock' / 'notion' / 'confluence' / 'wiki' / 'graphrag'
      - `EAIDE_KB_BASE_URL`: e.g. 'https://wiki.company.com/api/v1'
      - `EAIDE_KB_API_KEY_REF`: Keyring 占位符名（V1 接入 IAM 鉴权后填）
    """

    backend: str = "mock"
    base_url: str = ""
    api_key_ref: str = ""  # 占位符名（不存真值）
    timeout_s: float = 5.0
    # V0 默认仅 mock 模式；V1 扩展
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> KBConfig:
        """从环境变量读 KB 配置（与 envconfig.from_env 同风格）。"""
        return cls(
            backend=os.environ.get("EAIDE_KB_BACKEND", "mock"),
            base_url=os.environ.get("EAIDE_KB_BASE_URL", ""),
            api_key_ref=os.environ.get("EAIDE_KB_API_KEY_REF", ""),
            timeout_s=float(os.environ.get("EAIDE_KB_TIMEOUT_S", "5")),
        )


# ---- 适配器协议 ----------------------------------------------------------


@dataclass
class KBQueryResult:
    """单条 KB 检索结果（统一返回形态）。"""

    doc_id: str
    title: str
    snippet: str  # 已 PII 脱敏的文本片段
    score: float = 0.0  # 0-1 相关度
    source_url: str = ""
    metadata: dict = field(default_factory=dict)


@dataclass
class KBContext:
    """一次 KB 检索返回的 context（注入 LLM system prompt 前缀）。"""

    query: str
    results: list[KBQueryResult] = field(default_factory=list)
    backend: str = "mock"
    elapsed_ms: int = 0


@runtime_checkable
class KnowledgeBaseAdapter(Protocol):
    """所有外部 KB 适配器必须实现的接口。

    V0 实现：MockKBAdapter（本地占位，离线友好）。
    V1 实现：NotionKBAdapter / ConfluenceKBAdapter / GraphRAGKBAdapter 等。
    """

    def is_available(self) -> bool:
        """运行时探测（V0 mock 永远 True）。"""
        ...

    async def search(
        self,
        query: str,
        *,
        top_k: int = 3,
        timeout_s: float | None = None,
    ) -> KBContext:
        """检索 + 拼 KBContext；失败返空 context + logger.warning。"""
        ...

    @property
    def name(self) -> str:
        """适配器名（用于 audit 日志 / SSE event）。"""
        ...


# ---- V0 Mock 适配器 ------------------------------------------------------


class MockKBAdapter:
    """V0 Mock 适配器：返回固定示例 context + 短 sleep（模拟外部 KB 延迟）。

    用户（2026-07-29）决策：Phase 4 本地 KB 引擎跳过，本模块只保留接口。
    V0 用 mock 跑通 Phase 6 框架（build_kb_context / 注入 system prompt）。
    """

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
        # 模拟外部 KB 网络延迟（20-50ms）
        await asyncio.sleep(0.02)

        # 返回固定示例结果（V1 替换为真 KB 调用）
        sample_results = [
            KBQueryResult(
                doc_id=f"mock-doc-{self._call_count}-1",
                title="[MOCK] EAIDE 架构概览",
                snippet=f"用户问题「{query[:50]}」相关：EAIDE 是企业内网本地化 AI IDE Agent，"
                f"三层架构（Tauri 前端 / FastAPI + LangGraph / MCP 后端）。",
                score=0.85,
                source_url="https://wiki.company.com/eaide/architecture",
            ),
            KBQueryResult(
                doc_id=f"mock-doc-{self._call_count}-2",
                title="[MOCK] HITL 审批工作流",
                snippet="所有写操作必须经 hitl_gate 节点人工审批，决策走 Redis 或进程内 fallback。",
                score=0.62,
                source_url="https://wiki.company.com/eaide/hitl",
            ),
        ][:top_k]

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        logger.debug(
            "[KB.mock] query=%s results=%d elapsed=%dms",
            query[:50],
            len(sample_results),
            elapsed_ms,
        )
        return KBContext(
            query=query,
            results=sample_results,
            backend=self.name,
            elapsed_ms=elapsed_ms,
        )


# ---- 工厂 / 注册表 ------------------------------------------------------

_ADAPTERS: dict[str, type[KnowledgeBaseAdapter]] = {
    "mock": MockKBAdapter,
    # V1 扩展（Phase 4 / 第三方接入时填）：
    # "notion": NotionKBAdapter,
    # "confluence": ConfluenceKBAdapter,
    # "graphrag": GraphRAGKBAdapter,
}


def build_adapter(config: KBConfig | None = None) -> KnowledgeBaseAdapter:
    """根据 config.backend 构造 KB 适配器实例。

    未知 backend → fallback 到 MockKBAdapter（best-effort，V0 离线友好）。
    """
    cfg = config or KBConfig.from_env()
    cls = _ADAPTERS.get(cfg.backend)
    if cls is None:
        logger.warning("[KB] unknown backend=%s, fallback to MockKBAdapter", cfg.backend)
        return MockKBAdapter(cfg)
    return cls(cfg)


# ---- build_kb_context（Phase 6 调用点） ---------------------------------


async def build_kb_context(
    query: str,
    adapter: KnowledgeBaseAdapter | None = None,
    *,
    top_k: int = 3,
) -> KBContext:
    """统一调用入口：构造 adapter → 调 search → 返 KBContext。

    失败 / 超时 → 返空 KBContext（logger.warning，不抛错阻塞 agent 决策）。

    用法（V0 调用方示例）：
        ctx = await build_kb_context(user_prompt, top_k=3)
        # 拼到 system_prompt 前缀
        kb_section = "\\n".join(f"[{i}] {r.title}: {r.snippet}" for i, r in enumerate(ctx.results))
    """
    ad = adapter or build_adapter()
    if not ad.is_available():
        return KBContext(query=query, backend=ad.name)
    try:
        return await ad.search(query, top_k=top_k)
    except Exception as e:
        logger.warning("[KB] search failed backend=%s err=%s", ad.name, e)
        return KBContext(query=query, backend=ad.name)


def kb_context_to_prompt_snippet(ctx: KBContext, max_chars: int = 2000) -> str:
    """把 KBContext 拼成 system prompt 片段（与 chatStore.useFeatureContextPromptSnippet 风格一致）。

    截断 max_chars 防止 token 爆炸（Phase 4 / 6 通用：长 KB 结果分段 + 截断）。
    """
    if not ctx.results:
        return ""
    lines: list[str] = ["## 知识库参考（外部）"]
    for i, r in enumerate(ctx.results):
        lines.append(f"[{i}] {r.title}: {r.snippet}")
        lines.append(f"  来源: {r.source_url}")
    text = "\n".join(lines)
    return text[:max_chars]
