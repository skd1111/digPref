"""knowledge.chunker —— 本地知识库分块器（标题感知 + 父子两层 + 上下文前缀）。

三段式设计（对应「索引期补救」，纯本地零 LLM 零新依赖）：
    - 标题感知切分：markdown # 标题 + 中文条款标题（第X章/第X条/一、）作分界，
      维护层级标题路径（heading_path），解决「碎片丢失全局上下文」；
    - 父子两层（small-to-big）：父块 ~parent_size 承载完整上下文，子块 ~chunk_size
      语义聚焦只做索引/检索；命中子块后回喂父块给 LLM（见 hybrid_rag）；
    - 上下文前缀（Contextual Retrieval 的无 LLM 形态）：把 heading_path 拼到子块
      索引文本开头，让 BM25/向量都知道「这段属于第三章 财务报销」。

页码：ingestion 从 ParsedDocument 传入 page_index（字符偏移 -> 页码），分块按 bisect
定位 page_no；无页信息（markdown/txt）时 page_no=1。
"""

from __future__ import annotations

import bisect
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# markdown 标题（# ~ ####）
_MD_HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*$")
# 中文法规/制度条款标题：第X章 / 第X节 / 第X条 / 一、二、…
_CN_HEADING_RE = re.compile(
    r"^(第[一二三四五六七八九十百零〇\d]+[章节条篇]|[一二三四五六七八九十]+[、.])\s*(.*)$"
)
# 句子/段落断点（size 切分时优先在此断，避免切碎语义）
_BREAK_CHARS = "。\n！？；!?;.…"


def estimate_tokens(text: str) -> int:
    """粗略 token 估计（CJK 约 1 字 1 token，英文约 4 字符 1 token 的折中）。"""
    return max(1, len(text or "") // 2)


@dataclass
class ParentBlock:
    """父块（大块，承载完整上下文；检索命中子块后回喂给 LLM）。"""

    ord: int
    text: str
    heading_path: str = ""
    page_no: int = 1


@dataclass
class TextChunk:
    """子块（碎块，语义聚焦；只做 BM25/向量索引与检索）。"""

    text: str
    ord: int
    parent_ord: int
    heading_path: str = ""
    page_no: int = 1
    token_count: int = 0
    metadata: dict[str, object] = field(default_factory=dict)

    def index_text(self, *, contextual_prefix: bool = True) -> str:
        """用于 embedding / FTS5 索引的文本（可选拼接标题上下文前缀）。"""
        if contextual_prefix and self.heading_path:
            return f"{self.heading_path}\n{self.text}"
        return self.text


@dataclass
class ChunkingResult:
    parents: list[ParentBlock] = field(default_factory=list)
    children: list[TextChunk] = field(default_factory=list)


# ---- 标题感知切分 -----------------------------------------------------------


@dataclass
class _Section:
    heading_path: str
    text: str
    start: int  # 在原文中的字符起始偏移（页码定位用）


def _split_sections(text: str) -> list[_Section]:
    """按 markdown / 中文条款标题切分，维护层级标题路径。

    无任何标题时整篇作一段（heading_path=""）。start 记录每段在原文的偏移，
    供 page_index 定位页码。
    """
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []  # (level, title)
    buf: list[str] = []
    buf_start = 0
    offset = 0

    def flush(end: int) -> None:
        body = "\n".join(buf).strip()
        if body:
            heading = " > ".join(t for _, t in stack)
            sections.append(_Section(heading_path=heading, text=body, start=buf_start))
        buf.clear()

    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        level = 0
        title = ""
        m = _MD_HEADING_RE.match(stripped)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
        else:
            m2 = _CN_HEADING_RE.match(stripped)
            if m2 and len(stripped) <= 40:
                # 中文条款标题统一视作 level 2（章/条同级路径）
                level, title = 2, (m2.group(1) + (" " + m2.group(2) if m2.group(2) else "")).strip()
        if level:
            flush(offset)
            if level == 1:
                stack.clear()
            else:
                while stack and stack[-1][0] >= level:
                    stack.pop()
            stack.append((level, title))
            buf_start = offset + len(line)
        else:
            if not buf:
                buf_start = offset
            buf.append(stripped)
        offset += len(line)
    flush(offset)
    return sections or [_Section(heading_path="", text=text.strip(), start=0)]


def _size_split(text: str, size: int, overlap: float) -> list[tuple[int, str]]:
    """把一段文本按 size（字符）滑窗切分，返回 (段内偏移, 子串)。

    优先在句末/段落断点收尾（回看不超过 25%），避免切碎语义；overlap 控制重叠。
    """
    text = text.strip()
    n = len(text)
    if n <= size:
        return [(0, text)] if text else []
    step = max(1, int(size * (1.0 - max(0.0, min(overlap, 0.9)))))
    out: list[tuple[int, str]] = []
    start = 0
    while start < n:
        end = min(start + size, n)
        if end < n:
            # 在尾部 25% 区间内回找断点
            look = max(start + 1, end - size // 4)
            cut = -1
            for i in range(end - 1, look - 1, -1):
                if text[i] in _BREAK_CHARS:
                    cut = i + 1
                    break
            if cut > start:
                end = cut
        piece = text[start:end].strip()
        if piece:
            out.append((start, piece))
        if end >= n:
            break
        start = max(end - int(size * max(0.0, min(overlap, 0.9))), end - step)
        if start <= (out[-1][0] if out else 0):
            start = end  # 防死循环：重叠把 start 拉回时强制前进
    return out


def _page_of(page_index: list[tuple[int, int]] | None, offset: int) -> int:
    """按字符偏移在 page_index（[(start_offset, page_no), ...] 升序）中定位页码。"""
    if not page_index:
        return 1
    starts = [s for s, _ in page_index]
    i = bisect.bisect_right(starts, offset) - 1
    if i < 0:
        i = 0
    return int(page_index[i][1])


# ---- 主入口 ----------------------------------------------------------------


def chunk_text(
    text: str,
    *,
    chunk_size: int = 512,
    overlap: float = 0.1,
    parent_size: int = 2000,
    contextual_prefix: bool = True,
    page_index: list[tuple[int, int]] | None = None,
    base_metadata: dict[str, object] | None = None,
) -> ChunkingResult:
    """标题感知 + 父子两层分块。

    父块：把 section 按 parent_size 聚合/切分（保留 heading_path 与页码）。
    子块：把父块按 chunk_size + overlap 切分，继承 heading_path / parent_ord / page_no。
    contextual_prefix 只影响 index_text()（embedding/FTS 用），不改变存储原文。
    """
    result = ChunkingResult()
    if not (text or "").strip():
        return result
    base_meta = dict(base_metadata or {})
    sections = _split_sections(text)

    parent_ord = 0
    child_ord = 0
    for sec in sections:
        # section 可能超过 parent_size → 再按 parent_size 切成多个父块
        for p_off, p_text in _size_split(sec.text, parent_size, 0.0):
            p_page = _page_of(page_index, sec.start + p_off)
            parent = ParentBlock(
                ord=parent_ord, text=p_text, heading_path=sec.heading_path, page_no=p_page
            )
            result.parents.append(parent)
            # 父块内切子块（带重叠）
            for c_off, c_text in _size_split(p_text, chunk_size, overlap):
                child = TextChunk(
                    text=c_text,
                    ord=child_ord,
                    parent_ord=parent_ord,
                    heading_path=sec.heading_path,
                    page_no=_page_of(page_index, sec.start + p_off + c_off),
                    token_count=estimate_tokens(c_text),
                    metadata={**base_meta, "heading_path": sec.heading_path},
                )
                result.children.append(child)
                child_ord += 1
            parent_ord += 1
    logger.debug(
        "chunk_text: sections=%d parents=%d children=%d (prefix=%s)",
        len(sections),
        len(result.parents),
        len(result.children),
        contextual_prefix,
    )
    return result


# ---- 兼容既有导出符号（按来源类型分派到 chunk_text）-------------------------


def _chunks_from_payload(payload: str, **kwargs: object) -> list[TextChunk]:
    return chunk_text(payload, **kwargs).children  # type: ignore[arg-type]


def chunk_markdown(content: str, **kwargs: object) -> list[TextChunk]:
    """markdown 文档分块（标题感知天然契合）。"""
    return _chunks_from_payload(content or "", **kwargs)


def chunk_swagger(swagger_json: object, **kwargs: object) -> list[TextChunk]:
    """swagger/OpenAPI JSON 分块（序列化为文本后按大小切）。"""
    import json

    try:
        text = json.dumps(swagger_json, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(swagger_json)
    return _chunks_from_payload(text, **kwargs)


def chunk_conversation(messages: object, **kwargs: object) -> list[TextChunk]:
    """对话记录分块（拼成 role: text 文本后切）。"""
    lines: list[str] = []
    for m in messages if isinstance(messages, (list, tuple)) else []:
        role = getattr(m, "role", None) or (m.get("role") if isinstance(m, dict) else "")
        content = getattr(m, "content", None) or (m.get("content") if isinstance(m, dict) else "")
        lines.append(f"{role}: {content}")
    return _chunks_from_payload("\n".join(lines), **kwargs)


def chunk_business_rules(features: object, **kwargs: object) -> list[TextChunk]:
    """业务规则/功能点分块。"""
    if isinstance(features, str):
        return _chunks_from_payload(features, **kwargs)
    import json

    return _chunks_from_payload(json.dumps(features, ensure_ascii=False, indent=2), **kwargs)


def chunk_code_symbols(symbols: object, **kwargs: object) -> list[TextChunk]:
    """代码符号分块。"""
    if isinstance(symbols, str):
        return _chunks_from_payload(symbols, **kwargs)
    import json

    return _chunks_from_payload(json.dumps(symbols, ensure_ascii=False, indent=2), **kwargs)


def chunk_by_source(source_type: str, payload: object, **kwargs: object) -> list[TextChunk]:
    """按来源类型分派到对应分块函数。"""
    dispatch: dict[str, Callable[..., list[TextChunk]]] = {
        "markdown": chunk_markdown,
        "swagger": chunk_swagger,
        "conversation": chunk_conversation,
        "business_rule": chunk_business_rules,
        "code_symbol": chunk_code_symbols,
        "pdf": chunk_markdown,
    }
    fn = dispatch.get(source_type, chunk_markdown)
    return fn(payload, **kwargs)
