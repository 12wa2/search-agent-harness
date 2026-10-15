"""Turn a backend name into a ``SearchTool`` -- one seam for CLI and containers.

The three bundled backends are interchangeable at runtime::

    build_search_tool("memory")
    build_search_tool("sqlite", sqlite_path="corpus.db")
    build_search_tool("es", es_index="search-agent-demo", recreate=True)

Optional imports stay lazy, so a bare ``pip install`` keeps working without the
``.[es]`` extra. The Elasticsearch URL defaults to ``SEARCH_AGENT_ES_URL`` and
the index name to ``SEARCH_AGENT_ES_INDEX``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Sequence

from search_agent.contract.tool import SearchTool
from search_agent.demo import DEMO_DATA
from search_agent.tools.memory import InMemorySearchTool

__all__ = ["BACKENDS", "DEFAULT_ES_INDEX", "ES_INDEX_ENV", "build_search_tool"]

#: Backend names accepted by the CLI ``--backend`` flag.
BACKENDS = ("memory", "sqlite", "es")

#: Default index for the bundled demo corpus.
DEFAULT_ES_INDEX = "search-agent-demo"

#: Environment override for that index name.
ES_INDEX_ENV = "SEARCH_AGENT_ES_INDEX"

#: Anything the tools already accept as a corpus.
Candidates = str | Path | Sequence[dict[str, Any]]


def build_search_tool(
    backend: str = "memory",
    *,
    data_path: Candidates | None = None,
    sqlite_path: str = ":memory:",
    es_index: str | None = None,
    es_url: str | None = None,
    recreate: bool = False,
) -> SearchTool:
    """Build one backend by name; ``ValueError`` for names outside ``BACKENDS``."""
    data: Candidates = data_path or DEMO_DATA
    if backend == "memory":
        return InMemorySearchTool(data)
    if backend == "sqlite":
        from search_agent.tools.sqlite_fts import SqliteFtsSearchTool

        return SqliteFtsSearchTool(data, db_path=sqlite_path)
    if backend == "es":
        from search_agent.tools.elasticsearch import ElasticsearchSearchTool

        return ElasticsearchSearchTool(
            data,
            url=es_url,
            index=es_index or os.environ.get(ES_INDEX_ENV) or DEFAULT_ES_INDEX,
            recreate=recreate,
        )
    raise ValueError(f"unknown backend {backend!r}; expected one of {', '.join(BACKENDS)}")
