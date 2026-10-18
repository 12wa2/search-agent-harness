"""HTTP service tests. Skipped when the optional ``api`` extra is absent."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from search_agent.demo import DEMO_DATA, DEMO_QUERIES, ScriptedLlmClient  # noqa: E402
from search_agent.server import create_app  # noqa: E402
from search_agent.tools.memory import InMemorySearchTool  # noqa: E402

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
    assert isinstance(body["onlineConfigured"], bool)
    assert body["llm"] == "ScriptedLlmClient"
    assert body["backend"] == "InMemorySearchTool"


def test_runtime_mode_switch_uses_online_client_for_next_stream_search() -> None:
    offline = ScriptedLlmClient()
    online = ScriptedLlmClient()
    app = create_app(llm_client=offline, online_llm_client=online)

    with TestClient(app) as switched:
        health = switched.get("/healthz").json()
        assert health["mode"] == "offline"
        assert health["onlineConfigured"] is True

        switch = switched.post("/runtime/mode", json={"mode": "online"})
        assert switch.status_code == 200
        assert switch.json()["mode"] == "online"
        assert switch.json()["llm"] == "ScriptedLlmClient"

        with switched.stream(
            "POST",
            "/search/stream",
            json={"query": QUERY, "topK": 5, "details": True},
        ) as resp:
            assert resp.status_code == 200
            text = "".join(resp.iter_text())

        assert "event: result" in text
        assert app.state.runtime_llm.active_runs == 0

    assert online.calls
    assert offline.calls == []


class _MemorySearchSpy:
    def __init__(self) -> None:
        self._delegate = InMemorySearchTool(DEMO_DATA)
        self.search_calls = 0
        self.rerank_calls = 0

    def search_with_intent(self, req):
        self.search_calls += 1
        return self._delegate.search_with_intent(req)

    def rerank_pool(self, req):
        self.rerank_calls += 1
        return self._delegate.rerank_pool(req)


class _OfflineMemorySearchSpy(_MemorySearchSpy):
    pass


class _OnlineMemorySearchSpy(_MemorySearchSpy):
    pass


def test_dual_backend_presets_switch_llm_and_search_tool() -> None:
    offline_tool = _OfflineMemorySearchSpy()
    online_tool = _OnlineMemorySearchSpy()
    offline_llm = ScriptedLlmClient()
    online_llm = ScriptedLlmClient()
    app = create_app(
        tool=offline_tool,
        online_tool=online_tool,
        dual_backend=True,
        llm_client=offline_llm,
        online_llm_client=online_llm,
    )

    with TestClient(app) as switched:
        health = switched.get("/healthz").json()
        assert health["mode"] == "offline"
        assert health["backend"] == "_OfflineMemorySearchSpy"
        assert health["offlineBackend"] == "_OfflineMemorySearchSpy"
        assert health["onlineBackend"] == "_OnlineMemorySearchSpy"
        assert health["onlineConfigured"] is True

        switch = switched.post("/runtime/mode", json={"mode": "online"})
        assert switch.status_code == 200
        assert switch.json()["mode"] == "online"
        assert switch.json()["backend"] == "_OnlineMemorySearchSpy"

        response = switched.post("/search", json={"query": QUERY, "topK": 5})
        assert response.status_code == 200
        assert response.json()["items"]

    assert offline_tool.search_calls == 0
    assert online_tool.search_calls > 0
    assert offline_llm.calls == []
    assert online_llm.calls


def test_dual_backend_rejects_online_without_online_search_tool() -> None:
    offline_tool = _OfflineMemorySearchSpy()
    app = create_app(
        tool=offline_tool,
        online_tool=None,
        dual_backend=True,
        llm_client=ScriptedLlmClient(),
        online_llm_client=ScriptedLlmClient(),
    )

    with TestClient(app) as client:
        health = client.get("/healthz").json()
        assert health["mode"] == "offline"
        assert health["backend"] == "_OfflineMemorySearchSpy"
        assert health["onlineBackend"] is None
        assert health["onlineConfigured"] is False

        switch = client.post("/runtime/mode", json={"mode": "online"})
        assert switch.status_code == 409
        assert "Hybrid V2" in switch.json()["detail"]
        assert client.get("/healthz").json()["mode"] == "offline"


def test_runtime_mode_rejects_unconfigured_online_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SEARCH_AGENT_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with TestClient(create_app(offline=True)) as client:
        health = client.get("/healthz").json()
        assert health["mode"] == "offline"
        assert health["onlineConfigured"] is False

        switch = client.post("/runtime/mode", json={"mode": "online"})
        assert switch.status_code == 409
        assert "SEARCH_AGENT_API_KEY" in switch.json()["detail"]
        assert client.get("/healthz").json()["mode"] == "offline"


def test_runtime_mode_rejects_switch_while_run_is_active() -> None:
    app = create_app(offline=True, online_llm_client=ScriptedLlmClient())
    with TestClient(app) as client:
        runtime = app.state.runtime_llm
        runtime.begin_run()
        try:
            switch = client.post("/runtime/mode", json={"mode": "online"})
            assert switch.status_code == 409
            assert "正在执行搜索" in switch.json()["detail"]
            assert client.get("/healthz").json()["mode"] == "offline"
        finally:
            runtime.end_run()

        assert client.post("/runtime/mode", json={"mode": "online"}).status_code == 200


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


def test_search_returns_conversation_and_positions_first_page(client: TestClient) -> None:
    first = client.post(
        "/search",
        json={"query": QUERY, "topK": 5, "pageSize": 2, "details": True},
    )
    assert first.status_code == 200
    body = first.json()
    assert body["conversationId"]
    assert body["conversationTurns"] == 1
    assert body["queryToken"]
    assert len(body["items"]) == 2
    assert body["page"]["currentPage"] == 1
    assert body["page"]["hasMore"] is True

    second = client.post(
        "/search",
        json={
            "query": f"{QUERY} 最好有大厂背景",
            "topK": 5,
            "pageSize": 2,
            "conversationId": body["conversationId"],
        },
    )
    assert second.status_code == 200
    assert second.json()["conversationId"] == body["conversationId"]
    assert second.json()["conversationTurns"] == 2


def test_session_endpoint_paginates_and_expires_after_delete(client: TestClient) -> None:
    resp = client.post(
        "/search",
        json={"query": QUERY, "topK": 5, "pageSize": 2, "details": True},
    )
    token = resp.json()["queryToken"]
    assert token

    page = client.get(f"/sessions/{token}", params={"offset": 2, "limit": 2})
    assert page.status_code == 200
    payload = page.json()
    assert payload["queryToken"] == token
    assert payload["currentPage"] == 2
    assert len(payload["items"]) == 2

    deleted = client.delete(f"/sessions/{token}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert client.get(f"/sessions/{token}").status_code == 404


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


def test_get_stream_accepts_tenant_scope_and_page_size(client: TestClient) -> None:
    resp = client.get(
        "/search/stream",
        params={
            "query": QUERY,
            "topK": 5,
            "companyId": 39978,
            "hrId": 7,
            "pageSize": 2,
        },
    )
    assert resp.status_code == 200
    result_frames = [c for c in resp.text.split("\n\n") if c.startswith("event: result")]
    assert result_frames
    data_line = next(line for line in result_frames[0].splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))

    assert payload["conversationId"]
    assert payload["queryToken"]
    assert len(payload["items"]) == 2
    assert payload["page"]["currentPage"] == 1


def test_root_serves_the_web_console(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Search Agent" in resp.text
    assert "app.js" in resp.text
    assert 'id="agent-view"' in resp.text
    assert 'id="mode-chip"' in resp.text
    assert "mode-toggle" in resp.text
    assert "你好，我可以帮你寻找合适的候选人。" in resp.text
    assert "执行观测" in resp.text


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
    assert '"hardConstraintDetails"' in text
    result_frames = [c for c in text.split("\n\n") if c.startswith("event: result")]
    assert result_frames, text[:500]
    data_line = next(line for line in result_frames[0].splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert payload["items"]
    assert payload["debug"]["agentTrace"]
    assert payload["conversationId"]
    assert payload["queryToken"] is None
    assert payload["page"] is None


def test_static_assets_are_served(client: TestClient) -> None:
    script = client.get("/app.js")
    assert script.status_code == 200
    assert "javascript" in script.headers["content-type"]
    assert "async function runSearch" in script.text
    assert "conversationId" in script.text
    assert "/sessions/" in script.text
    assert "agent-task-message" in script.text
    assert "tool-call" in script.text
    assert "agent-delivery" in script.text
    assert "chat-progress-item" in script.text
    assert "renderChatResult" in script.text
    assert "/runtime/mode" in script.text
    assert "toggleRuntimeMode" in script.text
    assert "onlineConfigured" in script.text
    assert "快速演示" in script.text
    assert "完整在线" in script.text
    assert "LLM、检索工具与候选人库会一起切换" in script.text

    index = client.get("/")
    assert "快速演示" in index.text
    assert 'id="composer-mode"' in index.text

    styles = client.get("/styles.css")
    assert styles.status_code == 200
    assert "text/css" in styles.headers["content-type"]
    assert styles.text.strip()
    assert ".mode-toggle" in styles.text


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


class _ExplodingLlm:
    """Fail if a non-search request accidentally reaches the Harness."""

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, **_kwargs):
        self.calls += 1
        raise RuntimeError("intent-routed request must not call the LLM")


class _ExplodingSearchTool:
    """Fail and count if a routed reply accidentally reaches retrieval."""

    def __init__(self) -> None:
        self.calls = 0

    def search_with_intent(self, _req):
        self.calls += 1
        raise RuntimeError("intent-routed request must not call SearchTool")

    def rerank_pool(self, _req):
        self.calls += 1
        raise RuntimeError("intent-routed request must not call SearchTool")


def _frames(text: str) -> list[str]:
    return [frame.strip() for frame in text.split("\n\n") if frame.strip()]


def _event_payload(frame: str) -> tuple[str, dict]:
    event = ""
    data = []
    for line in frame.splitlines():
        if line.startswith("event: "):
            event = line.removeprefix("event: ")
        elif line.startswith("data: "):
            data.append(line.removeprefix("data: "))
    return event, json.loads("\n".join(data))


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    ("query", "intent"),
    [
        ("你好", "GREETING"),
        ("你能做什么？", "HELP"),
        ("帮我写一首诗", "OUT_OF_SCOPE"),
        ("推荐一本书", "OUT_OF_SCOPE"),
    ],
)
def test_non_search_requests_return_one_reply_without_calling_llm_or_tool(
    method: str, query: str, intent: str
) -> None:
    llm = _ExplodingLlm()
    tool = _ExplodingSearchTool()
    app = create_app(tool=tool, llm_client=llm)
    kwargs = (
        {"params": {"query": query, "topK": 5}}
        if method == "GET"
        else {"json": {"query": query, "topK": 5, "details": True}}
    )
    with TestClient(app) as routed:
        with routed.stream(method, "/search/stream", **kwargs) as resp:
            assert resp.status_code == 200
            text = "".join(resp.iter_text())

    frames = _frames(text)
    assert len(frames) == 1, text
    event, payload = _event_payload(frames[0])
    assert event == "reply"
    assert payload["intent"] == intent
    assert payload["searchStarted"] is False
    assert payload["suggestions"]
    assert llm.calls == 0
    assert tool.calls == 0
    assert "event: stage" not in text
    assert "event: result" not in text
    assert "event: error" not in text


def test_greeting_plus_recruitment_still_runs_the_agent(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/search/stream",
        json={"query": "你好，帮我找 Python 工程师", "topK": 5, "details": True},
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())

    assert "event: reply" not in text
    assert '"stage": "understand"' in text
    assert "event: result" in text
