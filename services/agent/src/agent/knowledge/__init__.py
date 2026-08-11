"""agent.knowledge —— Phase 4 本地知识库引擎（V0 外部适配器 + V1 本地引擎）。

公开 API：
- 外部 KB 适配器（V0）：build_adapter / build_kb_context / KBConfig / KBContext
- 本地知识库 DAO（V1）：KnowledgeStorage / KnowledgeIngestion / RAGRetriever
- 数据类（V1）：KnowledgeDoc / KnowledgeChunk / RetrievalResult / RAGContext / KnowledgeStats
- 分块器（V1）：chunk_markdown / chunk_swagger / chunk_conversation /
  chunk_business_rules / chunk_code_symbols / chunk_by_source
- 单例工厂（V1）：get_default_storage / get_default_retriever / build_default_ingestion
"""

from __future__ import annotations

# V0 外部适配器（保留；不被 V1 替代）
from agent.knowledge.adapter import (
    KBConfig,
    KBContext,
    KBQueryResult,
    KnowledgeBaseAdapter,
    MockKBAdapter,
    build_adapter,
    build_kb_context,
    kb_context_to_prompt_snippet,
)
from agent.knowledge.chunker import (
    chunk_business_rules,
    chunk_by_source,
    chunk_code_symbols,
    chunk_conversation,
    chunk_markdown,
    chunk_swagger,
    estimate_tokens,
)
from agent.knowledge.ingestion import (
    IngestionError,
    KnowledgeIngestion,
    build_default_ingestion,
)

# V1 数据类
from agent.knowledge.models import (
    ALL_SOURCE_TYPES,
    SOURCE_BUSINESS_RULE,
    SOURCE_CODE_SYMBOL,
    SOURCE_CONVERSATION,
    SOURCE_MARKDOWN,
    SOURCE_PDF,
    SOURCE_SWAGGER,
    KnowledgeChunk,
    KnowledgeDoc,
    KnowledgeStats,
    RAGContext,
    RetrievalResult,
)
from agent.knowledge.retriever import (
    EmbeddingClientProto,
    RAGRetriever,
    build_default_embedding_client,
    get_default_retriever,
    reset_default_retriever,
)

# V1 DAO + 导入编排 + 检索器
from agent.knowledge.storage import (
    KnowledgeStorage,
    decode_embedding,
    encode_embedding,
    get_default_storage,
    reset_default_storage,
)

__all__ = [
    "ALL_SOURCE_TYPES",
    "SOURCE_BUSINESS_RULE",
    "SOURCE_CODE_SYMBOL",
    "SOURCE_CONVERSATION",
    "SOURCE_MARKDOWN",
    "SOURCE_PDF",
    "SOURCE_SWAGGER",
    # V1 Retriever
    "EmbeddingClientProto",
    # V1 Ingestion
    "IngestionError",
    # V0 外部适配器（保持向后兼容）
    "KBConfig",
    "KBContext",
    "KBQueryResult",
    "KnowledgeBaseAdapter",
    # V1 数据类
    "KnowledgeChunk",
    "KnowledgeDoc",
    "KnowledgeIngestion",
    "KnowledgeStats",
    # V1 DAO
    "KnowledgeStorage",
    "MockKBAdapter",
    "RAGContext",
    "RAGRetriever",
    "RetrievalResult",
    "build_adapter",
    "build_default_embedding_client",
    "build_default_ingestion",
    "build_kb_context",
    # V1 Chunker
    "chunk_business_rules",
    "chunk_by_source",
    "chunk_code_symbols",
    "chunk_conversation",
    "chunk_markdown",
    "chunk_swagger",
    "decode_embedding",
    "encode_embedding",
    "estimate_tokens",
    "get_default_retriever",
    "get_default_storage",
    "kb_context_to_prompt_snippet",
    "reset_default_retriever",
    "reset_default_storage",
]
