"""evidence_text → 文档实际位置（防幻觉偏移）。"""
from __future__ import annotations

import unicodedata

from agent.doc_review.models import ParsedDocument, Position


def _normalize(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return "".join(ch for ch in s if not ch.isspace()).strip("，。；：,.;:、")


def _build_norm_map(full_text: str) -> tuple[str, list[int]]:
    """返回 (规范化文本, 每个字符在原文本的索引)。"""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(full_text):
        nch = unicodedata.normalize("NFKC", ch)
        if not nch.isspace():
            out.append(nch)
            idx.append(i)
    return "".join(out), idx


def _find_block(blocks_with_page: list[tuple[int, object]], orig_start: int) -> tuple[int, str] | None:
    for page_no, block in blocks_with_page:
        if block.start <= orig_start < block.end:
            return page_no, block.block_id
    return None


def locate_positions(parsed: ParsedDocument, evidence_text: str, *, max_hits: int = 20) -> list[Position]:
    evidence = _normalize(evidence_text)
    if not evidence:
        return []
    norm, idx = _build_norm_map(parsed.full_text)
    blocks_with_page = [
        (page.page_no, block) for page in parsed.pages for block in page.blocks
    ]
    hits: list[Position] = []
    search = evidence
    while search:
        start = 0
        while len(hits) < max_hits:
            pos = norm.find(search, start)
            if pos < 0:
                break
            orig_start = idx[pos]
            orig_end = idx[pos + len(search) - 1] + 1
            located = _find_block(blocks_with_page, orig_start)
            if located is not None:
                page_no, block_id = located
                hits.append(Position(page_no=page_no, block_id=block_id, start=orig_start, end=orig_end))
            start = pos + 1
        if hits:
            break
        if len(search) <= 20:
            break
        search = search[:-1]  # 模糊兜底：逐字缩短前缀
    return hits
