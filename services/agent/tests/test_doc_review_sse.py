# services/agent/tests/test_doc_review_sse.py
from agent.doc_review.events import (
    EVT_DOC_REVIEW_CLASSIFIED,
    EVT_DOC_REVIEW_FAILED,
    EVT_DOC_REVIEW_FINDINGS_READY,
    EVT_DOC_REVIEW_STARTED,
    consume_events,
    emit_event_sync,
)


async def test_events_roundtrip():
    emit_event_sync(EVT_DOC_REVIEW_STARTED, {"doc_id": "d1"})
    events = await consume_events()
    assert events[0][0] == EVT_DOC_REVIEW_STARTED


def test_channel_by_kind_contains_all():
    from agent.graph.stream import _CHANNEL_BY_KIND

    for kind in (EVT_DOC_REVIEW_STARTED, EVT_DOC_REVIEW_CLASSIFIED,
                 EVT_DOC_REVIEW_FINDINGS_READY, EVT_DOC_REVIEW_FAILED):
        assert kind in _CHANNEL_BY_KIND
