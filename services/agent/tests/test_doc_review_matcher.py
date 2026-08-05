# services/agent/tests/test_doc_review_matcher.py
from agent.doc_review.matcher import locate_positions
from agent.doc_review.models import ParsedDocument


def _doc(texts: list[list[str]]) -> ParsedDocument:
    pages = []
    all_text = []
    offset = 0
    for pi, blocks in enumerate(texts, start=1):
        items = []
        for bi, text in enumerate(blocks, start=1):
            items.append(
                {
                    "block_id": f"p{pi}b{bi}",
                    "text": text,
                    "start": offset,
                    "end": offset + len(text),
                }
            )
            all_text.append(text)
            offset += len(text) + 1
        pages.append({"page_no": pi, "blocks": items})
    return ParsedDocument(
        doc_id="d",
        file_name="f",
        file_path="f",
        format="txt",
        page_count=len(pages),
        pages=pages,
        full_text="\n".join(all_text),
    )


def test_exact_match_single():
    doc = _doc([["标题", "乙方应于 30 日内付款"]])
    hits = locate_positions(doc, "乙方应于 30 日内付款")
    assert len(hits) == 1
    assert hits[0].block_id == "p1b2"
    assert hits[0].start >= 0 and hits[0].end > hits[0].start


def test_whitespace_normalization():
    doc = _doc([["甲 方 应 当  保密"]])
    hits = locate_positions(doc, "甲方应当保密")
    assert len(hits) == 1


def test_fullwidth_normalization():
    doc = _doc([["违约金：10％"]])
    hits = locate_positions(doc, "违约金：10%")
    assert len(hits) == 1


def test_no_match_returns_empty():
    doc = _doc([["无关内容"]])
    assert locate_positions(doc, "完全不存在的内容") == []


def test_fuzzy_prefix_fallback():
    doc = _doc([["若乙方未能按期履行合同义务，应承担违约责任"]])
    hits = locate_positions(doc, "若乙方未能按期履行合同义务，应承担违约责任且赔偿全部损失")
    assert len(hits) == 1
