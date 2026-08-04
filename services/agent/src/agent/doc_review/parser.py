"""文档解析：PDF / DOCX / TXT / MD → ParsedDocument（页 / 块 / 全局偏移）。"""

from __future__ import annotations

import re
from pathlib import Path

from agent.doc_review.models import Block, DocFormat, Page, ParsedDocument, generate_id


class DocParseError(Exception):
    pass


_SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md"}


def _normalize_block_text(text: str) -> str:
    return re.sub(r"[ \t\r\f\v]+", " ", text).strip()


def _split_blocks(text: str) -> list[str]:
    raw = re.split(r"\n\s*\n", text)
    return [b for b in (_normalize_block_text(x) for x in raw) if b]


def _assemble(
    blocks_by_page: list[tuple[int, list[str]]],
    *,
    file_name: str,
    file_path: str,
    fmt: DocFormat,
) -> ParsedDocument:
    pages: list[Page] = []
    all_text: list[str] = []
    offset = 0
    for page_no, texts in blocks_by_page:
        blocks: list[Block] = []
        for i, text in enumerate(texts):
            block_id = f"p{page_no}b{i + 1}"
            start = offset
            end = start + len(text)
            blocks.append(Block(block_id=block_id, text=text, start=start, end=end))
            all_text.append(text)
            offset = end + 1  # 块之间以单个 '\n' 分隔
        pages.append(Page(page_no=page_no, blocks=blocks))
    return ParsedDocument(
        doc_id=generate_id(),
        file_name=file_name,
        file_path=file_path,
        format=fmt,
        page_count=len(pages),
        pages=pages,
        full_text="\n".join(all_text),
    )


def _parse_pdf(path: Path) -> list[tuple[int, list[str]]]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path))
    except Exception as exc:
        raise DocParseError(f"PDF 解析失败: {exc}") from exc
    if not reader.pages:
        raise DocParseError("PDF 无页面")
    pages: list[tuple[int, list[str]]] = []
    for i, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        blocks = _split_blocks(text)
        if blocks:
            pages.append((i, blocks))
    return pages


def _parse_docx(path: Path) -> list[tuple[int, list[str]]]:
    import docx
    from docx.oxml.ns import qn
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = docx.Document(str(path))
    blocks: list[str] = []
    for child in document.element.body.iterchildren():
        if child.tag == qn("w:p"):
            text = Paragraph(child, document).text or ""
            if text.strip():
                blocks.append(_normalize_block_text(text))
        elif child.tag == qn("w:tbl"):
            table = Table(child, document)
            for row in table.rows:
                for cell in row.cells:
                    cell_text = _normalize_block_text(cell.text or "")
                    if cell_text:
                        blocks.append(cell_text)
    return [(1, blocks)]


def _parse_text(path: Path) -> list[tuple[int, list[str]]]:
    raw = path.read_bytes()
    for encoding in ("utf-8", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise DocParseError("文本编码不支持（支持 UTF-8 / GBK）")
    return [(1, _split_blocks(text))]


def parse_document(path: str | Path) -> ParsedDocument:
    p = Path(path)
    if not p.exists():
        raise DocParseError(f"文件不存在: {p}")
    suffix = p.suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise DocParseError(f"不支持的格式: {suffix}（支持 pdf/docx/txt/md）")
    fmt = DocFormat(suffix.lstrip("."))
    if fmt == DocFormat.PDF:
        by_page = _parse_pdf(p)
    elif fmt == DocFormat.DOCX:
        by_page = _parse_docx(p)
    else:
        by_page = _parse_text(p)
    if not by_page:
        raise DocParseError("未提取到文本（可能为扫描件；OCR 不在 V0 范围）")
    return _assemble(by_page, file_name=p.name, file_path=str(p.resolve()), fmt=fmt)
