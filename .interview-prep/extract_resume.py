# -*- coding: utf-8 -*-
"""Dump the full logical content of a DOCX (paragraphs + tables) as plain text."""
import sys
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.oxml.ns import qn


def iter_block_items(parent):
    body = parent.element.body
    for child in body.iterchildren():
        if child.tag == qn('w:p'):
            yield Paragraph(child, parent)
        elif child.tag == qn('w:tbl'):
            yield Table(child, parent)


def main(path):
    out = open(sys.argv[2] if len(sys.argv) > 2 else "resume.txt", "w", encoding="utf-8")
    global print
    _p = print
    def print(*a, **k):  # noqa: A001
        k.pop("flush", None)
        _p(*a, **k, file=out, flush=True)
    doc = Document(path)
    print("=" * 70)
    print("SECTIONS:", len(doc.sections))
    for i, s in enumerate(doc.sections):
        print(f"  [{i}] page={s.page_width}x{s.page_height} margins L{s.left_margin} R{s.right_margin}")
    print("=" * 70)
    for block in iter_block_items(doc):
        if isinstance(block, Paragraph):
            txt = block.text
            style = block.style.name if block.style is not None else "?"
            if txt.strip() == "":
                continue
            print(f"<P style={style}> {txt}")
        else:
            print(f"<TABLE rows={len(block.rows)} cols={len(block.columns)}>")
            for r in block.rows:
                cells = [c.text.replace("\n", " / ") for c in r.cells]
                print("   | " + " | ".join(cells))
            print("</TABLE>")
    # headers/footers
    for i, s in enumerate(doc.sections):
        for name, part in (("header", s.header), ("footer", s.footer)):
            t = "\n".join(p.text for p in part.paragraphs if p.text.strip())
            if t:
                print(f"<{name} section={i}> {t}")
    out.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\79834\Desktop\个人简历.docx")
