"""Run the full agent loop against a real Elasticsearch backend.

Prerequisites::

    docker compose up -d elasticsearch     # single-node ES 8 on port 9200
    pip install -e ".[es]"

Usage::

    python examples/es_end_to_end.py [QUERY]

Indexes the bundled demo corpus, runs the same query through the agent twice
(Elasticsearch backend vs in-memory reference) and verifies both runs agree.
"""

from __future__ import annotations

import sys

from search_agent.demo import DEMO_DATA, DEMO_QUERIES, ScriptedLlmClient
from search_agent.models.api import SearchRequest
from search_agent.service import SearchAgentService
from search_agent.tools.elasticsearch import ElasticsearchSearchTool
from search_agent.tools.memory import InMemorySearchTool


def run(tool, query: str):
    service = SearchAgentService(tool=tool, llm_client=ScriptedLlmClient())
    return service.search(SearchRequest(query=query, topK=5, details=True))


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else DEMO_QUERIES[1]
    try:
        tool = ElasticsearchSearchTool(DEMO_DATA, index="search-agent-demo", recreate=True)
    except (RuntimeError, ImportError) as exc:
        print(f"[skip] {exc}")
        return 1

    print(f"indexed {tool.size} candidates -> {tool.url} (index: {tool.index_name})")
    es_response = run(tool, query)
    memory_response = run(InMemorySearchTool(DEMO_DATA), query)
    es_ids = [item.docId for item in es_response.items]
    memory_ids = [item.docId for item in memory_response.items]

    print(f"\nquery: {query}")
    print(f"qualified: {es_response.total} (ES) vs {memory_response.total} (in-memory)")
    for item in es_response.items[:5]:
        print(f"  - {item.docId}  {item.name}  score={item.score}")

    debug = es_response.debug or {}
    metrics = debug.get("agentMetrics") or {}
    recall = metrics.get("recall", {})
    rounds = metrics.get("rounds", {})
    print(
        "\nagentMetrics:"
        f" recallRate={recall.get('rate')} judged={recall.get('judged')}"
        f" target={recall.get('target')} iterations={rounds.get('iterations')}"
    )

    if es_ids == memory_ids:
        print("\nparity: Elasticsearch results == in-memory reference results")
        return 0
    print(f"\nparity MISMATCH:\n  es     = {es_ids}\n  memory = {memory_ids}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
