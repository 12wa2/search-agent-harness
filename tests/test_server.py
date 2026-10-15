"""HTTP service tests. Skipped when the optional ``api`` extra is absent."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from search_agent.demo import DEMO_QUERIES  # noqa: E402
from search_agent.server import create_app  # noqa: E402

QUERY = DEMO_QUERIES[0]


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app(offline=True))


def test_healthz(client: TestClient) -> None:
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mode"] == "offline"
    assert body["backend"] == "InMemorySearchTool"


def test_search_returns_camel_case_payload(client: TestClient) -> None:
    resp = client.post("/search", json={"query": QUERY, "topK": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] > 0
    assert body["items"]
    assert "docId" in body["items"][0]
    assert "workYears" in body["items"][0]


def test_search_with_details_returns_agent_trace(client: TestClient) -> None:
    resp = client.post("/search", json={"query": QUERY, "topK": 5, "details": True})
    assert resp.status_code == 200
    debug = resp.json()["debug"]
    assert debug["agentTrace"]
    assert "agentMetrics" in debug


def test_search_rejects_empty_query(client: TestClient) -> None:
    resp = client.post("/search", json={"query": ""})
    assert resp.status_code == 422


def test_search_stream_emits_stage_and_result_frames(client: TestClient) -> None:
    with client.stream("GET", "/search/stream", params={"query": QUERY, "topK": 5}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = "".join(resp.iter_text())

    assert text.index('"stage": "understand"') < text.index('"stage": "finalize"')
    assert "event: stage" in text

    result_frames = [c for c in text.split("\n\n") if c.startswith("event: result")]
    assert result_frames, text[:500]
    data_line = next(line for line in result_frames[0].splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["total"] > 0
    assert payload["items"]
