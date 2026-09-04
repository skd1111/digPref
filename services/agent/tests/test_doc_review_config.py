# services/agent/tests/test_doc_review_config.py
from agent.config import settings


def test_doc_review_defaults():
    assert settings.doc_review_db_path == "doc_review.db"
    assert settings.doc_review_classify_max_chars == 4000
    assert settings.doc_review_chunk_max_chars == 8000
    assert settings.doc_review_chunk_overlap == 200
    assert settings.doc_review_model is None
    assert settings.doc_review_llm_chain == ["cloud", "private"]
