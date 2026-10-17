"""HTTP service tests. Skipped when the optional ``api`` extra is absent."""

from __future__ import annotations

import json
from importlib.resources import files

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


def test_root_serves_the_web_console(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Search Agent" in resp.text
    assert "app.js" in resp.text


def test_post_search_stream_emits_stage_and_result_frames(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/search/stream",
        json={"query": QUERY, "topK": 5, "details": True},
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = "".join(resp.iter_text())

    assert '"stage": "understand"' in text
    assert '"stage": "finalize"' in text
    result_frames = [c for c in text.split("\n\n") if c.startswith("event: result")]
    assert result_frames, text[:500]
    data_line = next(line for line in result_frames[0].splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["items"]
    assert payload["debug"]["agentTrace"]


def test_static_assets_are_served(client: TestClient) -> None:
    script = client.get("/app.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "async function runSearch" in script.text

    styles = client.get("/styles.css")
    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]
    assert styles.text.strip()


def test_web_assets_are_present_in_the_package() -> None:
    web = files("search_agent").joinpath("web")
    for name in ("index.html", "styles.css", "app.js"):
        assert web.joinpath(name).is_file(), name


def test_stream_rejects_empty_query(client: TestClient) -> None:
    assert client.post("/search/stream", json={"query": ""}).status_code == 422
    assert client.get("/search/stream", params={"query": ""}).status_code == 422


def test_stream_emits_error_frame_when_worker_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self, req, *, cancel_event=None):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr("search_agent.server.SearchAgentService.search", boom)
    with TestClient(create_app(offline=True)) as failing:
        with failing.stream(
            "POST",
            "/search/stream",
            json={"query": QUERY, "topK": 5, "details": True},
        ) as resp:
            assert resp.status_code == 200
            text = "".join(resp.iter_text())

    assert "event: error" in text
    assert "worker exploded" in text
