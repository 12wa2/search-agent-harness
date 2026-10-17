"""Equivalence tests: LangGraphRunner vs the default LoopRunner.

Skipped when the optional ``langgraph`` extra is absent.
"""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("langgraph")

from search_agent.demo import DEMO_DATA, DEMO_QUERIES, ScriptedLlmClient  # noqa: E402
from search_agent.graph import LangGraphRunner  # noqa: E402
from search_agent.models.api import SearchRequest  # noqa: E402
from search_agent.runner import LoopRunner  # noqa: E402
from search_agent.service import SearchAgentService  # noqa: E402
from search_agent.tools.memory import InMemorySearchTool  # noqa: E402


def _search(runner, query: str):
    service = SearchAgentService(
        tool=InMemorySearchTool(DEMO_DATA),
        llm_client=ScriptedLlmClient(),
        runner=runner,
    )
    return service.search(SearchRequest(query=query, topK=10, details=True))


@pytest.mark.parametrize("query", DEMO_QUERIES)
def test_langgraph_matches_loop_runner(query: str) -> None:
    baseline = _search(LoopRunner(), query)
    compiled = _search(LangGraphRunner(), query)

    assert compiled.total == baseline.total
    assert [item.docId for item in compiled.items] == [item.docId for item in baseline.items]

    base_debug = baseline.debug or {}
    graph_debug = compiled.debug or {}
    assert graph_debug.get("degradedReason") == base_debug.get("degradedReason")
    assert (
        graph_debug["agentMetrics"]["meta"]["llmCalls"]
        == (base_debug["agentMetrics"]["meta"]["llmCalls"])
    )
    assert len(graph_debug["agentTrace"]["iterations"]) == len(
        base_debug["agentTrace"]["iterations"]
    )


def test_langgraph_runner_satisfies_protocol() -> None:
    from search_agent.runner import HarnessRunner

    runner: HarnessRunner = LangGraphRunner()
    assert callable(runner.run)


def test_langgraph_pre_cancelled_run_returns_partial_trace() -> None:
    cancel = threading.Event()
    cancel.set()
    service = SearchAgentService(
        tool=InMemorySearchTool(DEMO_DATA),
        llm_client=ScriptedLlmClient(),
        runner=LangGraphRunner(),
    )

    response = service.search(
        SearchRequest(query=DEMO_QUERIES[0], topK=10, details=True),
        cancel_event=cancel,
    )

    assert response.total == 0
    assert response.items == []
    assert response.debug is not None
    assert response.debug["cancelled"] is True
    assert response.debug["cancelledAt"] == "understand"
    assert response.debug["agentTrace"]["degradedReason"] == "cancelled"
