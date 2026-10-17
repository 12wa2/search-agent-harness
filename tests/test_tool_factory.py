"""Backend factory: name -> SearchTool, and the CLI flags that pick one."""

from __future__ import annotations

import pytest

from search_agent.cli import _default_backend, main
from search_agent.demo import DEMO_QUERIES, ScriptedLlmClient
from search_agent.models.api import SearchRequest
from search_agent.service import SearchAgentService
from search_agent.tools.factory import BACKENDS, build_search_tool

QUERY = DEMO_QUERIES[0]


def _ids(tool) -> list[str]:
    service = SearchAgentService(tool=tool, llm_client=ScriptedLlmClient())
    return [item.docId for item in service.search(SearchRequest(query=QUERY, topK=5)).items]


def test_backends_are_named_in_documented_order() -> None:
    assert BACKENDS == ("memory", "sqlite", "es", "hybrid_v2")


def test_memory_and_sqlite_agree_through_the_factory() -> None:
    assert _ids(build_search_tool("memory")) == _ids(build_search_tool("sqlite"))


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown backend"):
        build_search_tool("pgvector")


def test_hybrid_v2_backend_is_loaded_lazily(monkeypatch) -> None:
    sentinel = object()

    def fake_build(*, no_graph: bool | None = None):
        assert no_graph is None
        return sentinel

    monkeypatch.setattr("search_agent.integrations.hybrid_v2.build_hybrid_v2_tool", fake_build)
    assert build_search_tool("hybrid_v2") is sentinel


def test_default_backend_prefers_hybrid_v2_when_available(monkeypatch) -> None:
    monkeypatch.delenv("SEARCH_AGENT_BACKEND", raising=False)
    monkeypatch.setattr("search_agent.cli._hybrid_v2_available", lambda: True)
    assert _default_backend() == "hybrid_v2"


def test_default_backend_falls_back_to_memory_without_hybrid_v2(monkeypatch) -> None:
    monkeypatch.delenv("SEARCH_AGENT_BACKEND", raising=False)
    monkeypatch.setattr("search_agent.cli._hybrid_v2_available", lambda: False)
    assert _default_backend() == "memory"


def test_es_backend_reports_an_unreachable_node() -> None:
    pytest.importorskip("elasticsearch")
    with pytest.raises(RuntimeError, match="not reachable"):
        build_search_tool("es", es_url="http://127.0.0.1:9")


def test_cli_search_selects_the_sqlite_backend(capsys) -> None:
    code = main(["search", QUERY, "--offline", "--backend", "sqlite", "--top-k", "3"])
    assert code == 0
    assert "finalize" in capsys.readouterr().out


def test_sqlite_backend_serves_over_http() -> None:
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from search_agent.server import create_app

    client = TestClient(create_app(tool=build_search_tool("sqlite"), offline=True))
    assert client.get("/healthz").json()["backend"] == "SqliteFtsSearchTool"
    body = client.post("/search", json={"query": QUERY, "topK": 3}).json()
    assert body["total"] > 0
    assert body["items"]
