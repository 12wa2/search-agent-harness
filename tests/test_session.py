"""Tests for AgentSessionStore: create, paginate, invalidate."""

from __future__ import annotations

import pytest

from search_agent.contract.tool import ToolCandidate
from search_agent.models.api import CandidateItem
from search_agent.session import (
    AgentSessionStore,
)


def _tc(doc_id, score=0.8):
    return ToolCandidate(
        doc_id=doc_id,
        score=score,
        item=CandidateItem(docId=doc_id, score=score, name=f"Candidate {doc_id}"),
        hard_matches=[{"field": "skill", "matched": True}],
        match_explanation={"summary": "good"},
    )


def _state(n=10):
    return {
        "query": "Python engineer",
        "company_id": 39978,
        "hr_id": 1,
        "final_items": [_tc(f"d{i}", 0.9 - i * 0.05) for i in range(n)],
        "agent_trace": {"iterations": [], "final": {"mergedPoolSize": n}},
        "agent_metrics": {"recall": {"judged": n}},
        "degraded_reason": None,
        "need_clarify": False,
    }


def test_create_returns_token():
    store = AgentSessionStore(ttl_seconds=60, connect_redis=False)
    token = store.create(_state(5))
    assert isinstance(token, str)
    assert len(token) > 0


def test_get_page_first_page():
    store = AgentSessionStore(ttl_seconds=60, connect_redis=False)
    token = store.create(_state(10))
    page = store.get_page(token, offset=0, limit=5)

    assert page.total == 10
    assert len(page.items) == 5
    assert page.current_page == 1
    assert page.has_more is True
    assert page.next_offset == 5
    assert all(isinstance(item, CandidateItem) for item in page.items)
    assert page.items[0].docId == "d0"


def test_get_page_second_page():
    store = AgentSessionStore(ttl_seconds=60, connect_redis=False)
    token = store.create(_state(10))
    page = store.get_page(token, offset=5, limit=5)

    assert len(page.items) == 5
    assert page.current_page == 2
    assert page.has_more is False
    assert page.next_offset == 5  # no more
    assert page.items[0].docId == "d5"


def test_get_page_last_partial():
    store = AgentSessionStore(ttl_seconds=60, connect_redis=False)
    token = store.create(_state(7))
    page = store.get_page(token, offset=5, limit=5)

    assert page.total == 7
    assert len(page.items) == 2  # only 2 left
    assert page.has_more is False


def test_get_page_empty_pool():
    store = AgentSessionStore(ttl_seconds=60, connect_redis=False)
    token = store.create(_state(0))
    page = store.get_page(token, offset=0, limit=20)

    assert page.total == 0
    assert page.items == []
    assert page.has_more is False


def test_invalidate():
    store = AgentSessionStore(ttl_seconds=60, connect_redis=False)
    token = store.create(_state(3))
    assert store.get_session(token) is not None
    store.invalidate(token)
    assert store.get_session(token) is None


def test_expired_token_raises():
    store = AgentSessionStore(ttl_seconds=60, connect_redis=False)
    token = store.create(_state(3))
    store.invalidate(token)
    import pytest

    with pytest.raises(KeyError):
        store.get_page(token)


def test_get_session_returns_trace():
    store = AgentSessionStore(ttl_seconds=60, connect_redis=False)
    state = _state(3)
    state["agent_trace"] = {"iterations": [{"index": 0}], "degradedReason": None}
    token = store.create(state)
    session = store.get_session(token)

    assert session is not None
    assert session.query == "Python engineer"
    assert session.total == 3
    assert "iterations" in session.agent_trace


def test_total_pages_calculation():
    store = AgentSessionStore(ttl_seconds=60, connect_redis=False)
    token = store.create(_state(25))
    page = store.get_page(token, offset=0, limit=10)

    assert page.total_pages == 3  # ceil(25/10)
    assert page.current_page == 1


def test_page_preserves_enriched_score_and_explanation():
    store = AgentSessionStore(ttl_seconds=60, connect_redis=False)
    token = store.create(_state(1))
    page = store.get_page(token, offset=0, limit=1)

    assert page.items[0].score == pytest.approx(0.9)
    assert page.items[0].matchExplanation == {"summary": "good"}


@pytest.mark.parametrize(("offset", "limit"), [(-1, 5), (0, 0)])
def test_page_rejects_invalid_slice(offset: int, limit: int) -> None:
    store = AgentSessionStore(ttl_seconds=60, connect_redis=False)
    token = store.create(_state(1))
    with pytest.raises(ValueError):
        store.get_page(token, offset=offset, limit=limit)
