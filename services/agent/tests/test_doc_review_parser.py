# services/agent/tests/test_doc_review_parser.py
from pathlib import Path

import pytest
from agent.doc_review.parser import DocParseError, parse_document


def _write_txt(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "sample.txt"
    p.write_text(text, encoding="utf-8")
    return p


def test_parse_txt_blocks_and_offsets(tmp_path):
    p = _write_txt(tmp_path, "标题\n\n第一段内容。\n\n第二段。")
    doc = parse_document(p)
    assert doc.format.value == "txt"
    assert doc.page_count == 1
    blocks = doc.pages[0].blocks
    assert [b.text for b in blocks] == ["标题", "第一段内容。", "第二段。"]
    assert blocks[1].start == len(blocks[0].text) + 1
    assert doc.full_text == "标题\n第一段内容。\n第二段。"


def test_parse_txt_gbk_fallback(tmp_path):
    p = tmp_path / "gbk.txt"
    p.write_bytes("合同金额：100万".encode("gbk"))
    doc = parse_document(p)
    assert "合同金额" in doc.full_text


def test_unsupported_format(tmp_path):
    p = tmp_path / "a.xls"
    p.write_bytes(b"x")
    with pytest.raises(DocParseError, match="不支持"):
        parse_document(p)


def test_parse_pdf(tmp_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfgen import canvas

    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    p = tmp_path / "sample.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.setFont("STSong-Light", 14)
    c.drawString(72, 720, "采购合同")
    c.showPage()
    c.save()
    doc = parse_document(p)
    assert doc.format.value == "pdf"
    assert doc.page_count >= 1
    assert "采购合同" in doc.full_text


def test_parse_docx(tmp_path):
    import docx

    p = tmp_path / "sample.docx"
    d = docx.Document()
    d.add_paragraph("保密协议")
    d.add_paragraph("双方应保守商业秘密")
    d.save(str(p))
    doc = parse_document(p)
    assert doc.format.value == "docx"
    assert "保密协议" in doc.full_text
    assert "商业秘密" in doc.full_text


def test_empty_pdf_raises(tmp_path):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    p = tmp_path / "empty.pdf"
    c = canvas.Canvas(str(p), pagesize=A4)
    c.showPage()
    c.save()
    with pytest.raises(DocParseError, match="未提取到文本"):
        parse_document(p)
