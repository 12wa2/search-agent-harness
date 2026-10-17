"""Run the full agent loop against a real Hybrid V2 Elasticsearch index.

Prerequisites::

    docker compose up -d elasticsearch
    pip install -e ".[es]"

Usage::

    python examples/hybrid_v2_end_to_end.py
    python examples/hybrid_v2_end_to_end.py --query "找高并发后端工程师"
"""

from __future__ import annotations

import argparse
from dataclasses import replace

from search_agent.demo import DEMO_DATA, DEMO_QUERIES, ScriptedLlmClient
from search_agent.hybrid_v2 import HybridV2Config, HybridV2SearchService
from search_agent.hybrid_v2.es_client import ElasticsearchClient
from search_agent.hybrid_v2.indexing import HybridV2Indexer
from search_agent.models.api import SearchRequest
from search_agent.service import SearchAgentService


def _event(event: str, state: dict) -> None:
    extra = ""
    if event == "execute":
        extra = f" paths={len(state.get('path_results') or [])}"
    elif event == "evaluate":
        extra = f" phase={state.get('phase')}"
    print(f"  [{event}]{extra}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default=DEMO_QUERIES[0])
    parser.add_argument("--data", default=str(DEMO_DATA))
    parser.add_argument("--es-url", default=None)
    parser.add_argument("--index", default="search-agent-hybrid-v2-demo")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    base = HybridV2Config.from_env()
    config = replace(
        base,
        es_url=args.es_url or base.es_url,
        es_index=args.index,
        vector_mode="flat",
    )
    client = ElasticsearchClient(config)
    try:
        indexer = HybridV2Indexer(config, client=client)
        report = indexer.index_file(args.data, recreate=True)
        doctor = indexer.doctor()
        print(
            f"indexed={report.documents} index={report.index} "
            f"vectors={report.vector_mode} dims={report.embedding_dims}"
        )
        if not doctor.ok:
            for check in doctor.checks:
                print(f"  [{'PASS' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
            return 2

        tool = HybridV2SearchService(config=config, es_client=client)
        service = SearchAgentService(
            tool=tool,
            llm_client=ScriptedLlmClient(),
            event_sink=_event,
        )
        response = service.search(SearchRequest(query=args.query, topK=args.top_k, details=True))
        print(f"\nquery: {args.query}")
        print(f"delivered: {response.total}")
        for item in response.items:
            print(f"  - {item.docId}  {item.name}  score={item.score:.4f}")
        metrics = (response.debug or {}).get("agentMetrics") or {}
        print(f"agentMetrics: {metrics}")
        return 0
    except (ImportError, RuntimeError) as exc:
        print(f"[error] {exc}")
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
