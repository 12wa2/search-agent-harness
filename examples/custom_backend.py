"""Plug your own SearchTool backend into the harness (offline, no API key).

The harness only ever talks to the ``SearchTool`` protocol (two methods):

- ``search_with_intent``: receives an already-understood intent -- a boolean
  ``WhereNode`` tree plus semantic conditions. The agent has already done the
  query understanding, so your backend just executes the intent.
- ``rerank_pool``: the cross-path global rerank over the merged candidate pool.

This example wraps the bundled reference backend (``InMemorySearchTool``) with
call logging, then runs one query end to end using the scripted demo LLM.

Run::

    pip install -e ".[dev]"
    python examples/custom_backend.py
"""

from search_agent import SearchAgentService, SearchRequest
from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
)
from search_agent.demo import DEMO_DATA, ScriptedLlmClient
from search_agent.tools.memory import InMemorySearchTool

QUERY = "找有 3 年以上经验、做过高并发系统的后端工程师"


class LoggingBackend:
    """SearchTool implementation: log every call, then delegate."""

    def __init__(self, data_path: str) -> None:
        self._inner = InMemorySearchTool(data_path)
        self.calls = 0

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        self.calls += 1
        print(f"[backend] search #{self.calls}: {req.query_text!r} top_k={req.top_k}")
        return self._inner.search_with_intent(req)

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        self.calls += 1
        print(f"[backend] rerank #{self.calls}: {len(req.candidates)} candidates in pool")
        return self._inner.rerank_pool(req)


def main() -> None:
    backend = LoggingBackend(DEMO_DATA)
    service = SearchAgentService(tool=backend, llm_client=ScriptedLlmClient())
    resp = service.search(SearchRequest(query=QUERY, topK=5))
    print(f"\n{resp.total} candidates returned, {backend.calls} backend calls total")
    for item in resp.items:
        print(f"  - {item.name}  score={item.score:.3f}  docId={item.docId}")


if __name__ == "__main__":
    main()
