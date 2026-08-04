from protocol.doc_review import DocFinding, DocPosition


def test_doc_finding_roundtrip():
    f = DocFinding(
        finding_id="f1", risk_type="legal", risk_level="high", title="t",
        positions=[DocPosition(page_no=1, block_id="p1b1", start=0, end=2)],
    )
    data = f.model_dump()
    assert data["risk_type"] == "legal"
    assert data["positions"][0]["block_id"] == "p1b1"
