"""Cooperative cancellation: stage boundaries, LLM gates, service and SSE close."""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from search_agent.core.budget import BudgetState
from search_agent.core.cancellation import SearchCancelled
from search_agent.demo import DEMO_DATA
from search_agent.harness import Harness
from search_agent.llm.client import FakeLlmClient, MeteredLlmClient
from search_agent.models.api import SearchRequest
from search_agent.models.artifacts import Budget
from search_agent.service import SearchAgentService
from search_agent.stages.base import Deps
from search_agent.tools.memory import InMemorySearchTool

UNDERSTAND_RESPONSE = {
    "title": "Python backend engineer",
    "must_abilities": [{"name": "Python", "level": "must"}],
    "nice_abilities": [],
    "hard_constraints": [],
    "subjective_difficulty": "mid",
    "difficulty_reason": "",
    "needs_clarification": False,
    "clarify_questions": [],
    "talent_profile": {
        "source_hypotheses": [],
        "must_keywords": ["Python"],
        "soft_signals": [],
        "risk_filters": [],
    },
}


def _budget() -> Budget:
    return Budget(
        max_iterations=1,
        max_paths_per_iteration=2,
        target_qualified=5,
        time_budget_sec=5.0,
        max_llm_calls=10,
    )


def _deps(llm, *, cancel_event=None, event_sink=None) -> Deps:
    return Deps(
        llm=llm,
        tool=InMemorySearchTool(DEMO_DATA),
        budget_state=BudgetState(budget=_budget()),
        cancel_event=cancel_event,
        event_sink=event_sink,
    )


def _state() -> dict[str, Any]:
    return {
        "query": "Python backend engineer",
        "company_id": 1,
        "hr_id": None,
        "permission": None,
        "routing": 1,
        "top_k": 5,
        "recall_pool_size": None,
        "details": True,
        "budget": _budget(),
    }


def test_pre_cancelled_run_makes_no_llm_calls() -> None:
    cancel = threading.Event()
    cancel.set()
    llm = FakeLlmClient({"agent_understand": UNDERSTAND_RESPONSE})

    state = Harness(_deps(llm, cancel_event=cancel)).run(_state())

    assert state["cancelled"] is True
    assert state["cancelled_at"] == "understand"
    assert state["degraded_reason"] == "cancelled"
    assert state["phase"] == "done"
    assert llm.calls == []


def test_cancel_after_understand_stops_before_plan() -> None:
    cancel = threading.Event()

    def sink(event: str, _state: dict[str, Any]) -> None:
        if event == "understand":
            cancel.set()

    llm = FakeLlmClient(
        {
            "agent_understand": UNDERSTAND_RESPONSE,
            "agent_plan": {"paths": []},
        }
    )
    state = Harness(_deps(llm, cancel_event=cancel, event_sink=sink)).run(_state())

    assert state["cancelled"] is True
    assert state["cancelled_at"] == "understand"
    assert [name for name, _ in llm.calls] == ["agent_understand"]


def test_metered_client_checks_cancel_before_forwarding() -> None:
    cancel = threading.Event()
    cancel.set()
    inner = FakeLlmClient({"agent_plan": {"paths": []}})
    metered = MeteredLlmClient(inner, BudgetState(budget=_budget()), cancel_event=cancel)

    with pytest.raises(SearchCancelled):
        metered.complete_json(system="s", user="u", schema_name="agent_plan", schema={})

    assert inner.calls == []


def test_service_returns_explicit_cancelled_debug() -> None:
    cancel = threading.Event()
    cancel.set()
    service = SearchAgentService(
        tool=InMemorySearchTool(DEMO_DATA),
        llm_client=FakeLlmClient({"agent_understand": UNDERSTAND_RESPONSE}),
    )

    response = service.search(
        SearchRequest(query="Python backend engineer", topK=5, details=True),
        cancel_event=cancel,
    )

    assert response.total == 0
    assert response.items == []
    assert response.debug is not None
    assert response.debug["cancelled"] is True
    assert response.debug["cancelledAt"] == "understand"
    assert response.debug["degradedReason"] == "cancelled"
    assert response.debug["agentTrace"]["degradedReason"] == "cancelled"


def test_closing_sse_stream_requests_backend_cancellation() -> None:
    from search_agent.server import _stream_search

    class GatedLlm:
        def __init__(self) -> None:
            self.started = threading.Event()
            self.release = threading.Event()
            self.plan_called = threading.Event()
            self.calls: list[str] = []

        def complete_json(
            self,
            *,
            system: str,
            user: str,
            schema_name: str,
            schema: dict[str, Any],
            model: str | None = None,
        ) -> dict[str, Any]:
            self.calls.append(schema_name)
            if schema_name == "agent_understand":
                self.started.set()
                assert self.release.wait(2.0)
                return UNDERSTAND_RESPONSE
            if schema_name == "agent_plan":
                self.plan_called.set()
                return {"paths": []}
            return {}

    llm = GatedLlm()
    cancel = threading.Event()
    tool = InMemorySearchTool(DEMO_DATA)
    request = SearchRequest(query="Python backend engineer", topK=5, details=True)

    async def scenario() -> None:
        generator = _stream_search(tool, llm, request, cancel_event=cancel)
        first_frame = asyncio.create_task(generator.__anext__())
        assert await asyncio.to_thread(llm.started.wait, 2.0)
        first_frame.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first_frame
        await generator.aclose()
        assert cancel.is_set()
        llm.release.set()
        assert not await asyncio.to_thread(llm.plan_called.wait, 0.5)
        assert llm.calls == ["agent_understand"]

    try:
        asyncio.run(scenario())
    finally:
        llm.release.set()
