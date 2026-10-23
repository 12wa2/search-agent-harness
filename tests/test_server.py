"""HTTP service tests. Skipped when the optional ``api`` extra is absent."""

from __future__ import annotations

import json
from importlib.resources import files

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from search_agent.conversation_memory import ConversationStore  # noqa: E402
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
    assert isinstance(body["startedAt"], float)


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


def test_permission_enforcement_rejects_unscoped_requests(monkeypatch) -> None:
    monkeypatch.setenv("SEARCH_AGENT_REQUIRE_PERMISSION", "1")
    client = TestClient(create_app(offline=True))
    company_id = 931_000_001

    missing = client.post("/search", json={"query": QUERY, "topK": 5})
    assert missing.status_code == 403

    mismatched = client.post(
        "/search",
        json={
            "query": QUERY,
            "topK": 5,
            "companyId": company_id + 1,
            "permission": {"companyId": company_id, "hasFullAccess": True},
        },
    )
    assert mismatched.status_code == 403

    allowed = client.post(
        "/search",
        json={
            "query": QUERY,
            "topK": 5,
            "pageSize": 2,
            "companyId": company_id,
            "permission": {"companyId": company_id, "hasFullAccess": True},
        },
    )
    assert allowed.status_code == 200
    token = allowed.json()["queryToken"]
    assert token

    assert client.get(f"/sessions/{token}").status_code == 403
    assert client.get(f"/sessions/{token}", params={"companyId": company_id + 1}).status_code == 404
    page = client.get(f"/sessions/{token}", params={"companyId": company_id})
    assert page.status_code == 200
    assert page.json()["queryToken"] == token
    assert (
        client.delete(f"/sessions/{token}", params={"companyId": company_id + 1}).status_code == 404
    )
    assert client.delete(f"/sessions/{token}", params={"companyId": company_id}).status_code == 200


def test_permission_enforcement_keeps_non_search_stream_available(monkeypatch) -> None:
    monkeypatch.setenv("SEARCH_AGENT_REQUIRE_PERMISSION", "1")
    client = TestClient(create_app(offline=True))
    company_id = 931_000_002

    with client.stream(
        "POST",
        "/search/stream",
        json={"query": "你好", "topK": 5, "companyId": company_id},
    ) as greeting:
        assert greeting.status_code == 200
        assert "event: reply" in "".join(greeting.iter_text())

    with client.stream(
        "POST",
        "/search/stream",
        json={"query": QUERY, "topK": 5, "companyId": company_id},
    ) as missing:
        assert missing.status_code == 403

    with client.stream(
        "POST",
        "/search/stream",
        json={
            "query": QUERY,
            "topK": 5,
            "companyId": company_id,
            "permission": {"companyId": company_id, "hasFullAccess": True},
        },
    ) as allowed:
        assert allowed.status_code == 200
        assert "event: result" in "".join(allowed.iter_text())


def test_conversation_api_persists_history_exclusions_and_tenant_scope() -> None:
    store = ConversationStore(connect_redis=False)
    app = create_app(offline=True, conversation_store=store)

    with TestClient(app) as scoped:
        created = scoped.post(
            "/conversations",
            json={"companyId": 98801, "hrId": 7, "title": "Backend hiring"},
        )
        assert created.status_code == 200
        conversation_id = created.json()["conversationId"]

        rows = scoped.get(
            "/conversations",
            params={"companyId": 98801, "hrId": 7},
        )
        assert rows.status_code == 200
        assert rows.json()[0]["title"] == "Backend hiring"

        excluded = scoped.patch(
            f"/conversations/{conversation_id}/candidates/c004",
            params={"companyId": 98801, "hrId": 7},
            json={"excluded": True},
        )
        assert excluded.status_code == 200
        assert excluded.json()["excludedCandidateIds"] == ["c004"]

        detail = scoped.get(
            f"/conversations/{conversation_id}",
            params={"companyId": 98801, "hrId": 7},
        )
        assert detail.status_code == 200
        assert detail.json()["excludedCandidateIds"] == ["c004"]

        assert (
            scoped.get(
                f"/conversations/{conversation_id}",
                params={"companyId": 40000, "hrId": 7},
            ).status_code
            == 404
        )
        assert scoped.delete(
            f"/conversations/{conversation_id}",
            params={"companyId": 40000, "hrId": 7},
        ).json() == {"deleted": False}
        assert scoped.delete(
            f"/conversations/{conversation_id}",
            params={"companyId": 98801, "hrId": 7},
        ).json() == {"deleted": True}


def test_search_writes_restorable_conversation_snapshot() -> None:
    store = ConversationStore(connect_redis=False)
    app = create_app(offline=True, conversation_store=store)

    with TestClient(app) as scoped:
        response = scoped.post(
            "/search",
            json={"query": QUERY, "topK": 5, "details": True, "pageSize": 2},
        )
        assert response.status_code == 200
        body = response.json()

        detail = scoped.get(
            f"/conversations/{body['conversationId']}",
            params={"companyId": 1},
        )
        assert detail.status_code == 200
        turn = detail.json()["turns"][0]
        assert turn["assistantSummary"]
        assert turn["route"]
        assert turn["status"] == "completed"
        assert turn["queryToken"] == body["queryToken"]
        assert turn["resultItems"]
        snapshot = turn["resultItems"][0]
        assert snapshot["docId"] == body["items"][0]["docId"]
        assert snapshot["workExperiences"]
        assert snapshot["educations"]


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
    assert "app.js?v=8" in resp.text
    assert "styles.css?v=10" in resp.text
    assert 'id="agent-view"' in resp.text
    assert 'id="sidebar-toggle"' in resp.text
    assert 'class="topbar-leading"' in resp.text
    assert 'id="sidebar-collapse"' in resp.text
    assert 'id="sidebar-rail-toggle"' in resp.text
    assert 'id="sidebar-content"' in resp.text
    assert 'id="agent-sidebar"' in resp.text
    assert 'id="agent-sidebar-scrim"' in resp.text
    assert 'id="mode-chip"' in resp.text
    assert 'id="online-mode-dialog"' in resp.text
    assert "mode-toggle" in resp.text
    assert "今天要找" in resp.text
    assert "什么样的人？" in resp.text
    assert "自适应执行" in resp.text
    assert "根据需求复杂度选择执行路径" in resp.text
    assert "选择执行路径" in resp.text
    assert "覆盖不足时反思并修订策略" in resp.text
    assert "8 阶段反思" not in resp.text
    assert "明确条件可直接生成检索 IR" in resp.text
    assert "只有覆盖不足时才定位缺口" in resp.text
    assert "Reflect · 按需触发" in resp.text
    assert 'id="sidebar-new"' in resp.text
    assert 'id="agent-session-list"' in resp.text
    assert 'id="agent-session-count"' in resp.text
    assert "最近会话" in resp.text
    assert "本次会话" not in resp.text
    assert "检索原则" not in resp.text
    assert "一条需求如何变成名单" in resp.text
    assert "执行观测" in resp.text
    assert resp.headers["cache-control"] == "no-cache, no-store, must-revalidate"


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
    assert script.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert "javascript" in script.headers["content-type"]
    assert "async function runSearch" in script.text
    assert "conversationId" in script.text
    assert "/sessions/" in script.text
    assert "agent-task-message" in script.text
    assert "confirmOnlineMode" in script.text
    assert "onlineModeDialog.showModal" in script.text
    assert "tool-call" in script.text
    assert "agent-delivery" in script.text
    assert "chat-progress-item" in script.text
    assert "renderChatResult" in script.text
    assert "agent-session-list" in script.text
    assert "SIDEBAR_KEY" in script.text
    assert "setSidebarVisible" in script.text
    assert "sidebarMediaQuery" in script.text
    assert "sidebarRailToggle" in script.text
    assert "/conversations/" in script.text
    assert "restoreConversation" in script.text
    assert "toggleCandidateExclusion" in script.text
    assert "localStorage" in script.text
    assert "excludedCandidateIds" in script.text
    assert "candidateResumeUrl" in script.text
    assert 'RESUME_PAGE = "/resume.html"' in script.text
    assert "RESUME_CACHE_PREFIX" in script.text
    assert "cand-resume" in script.text
    assert "resumeToken: turn.queryToken" in script.text
    assert 'url.searchParams.set("view", next)' in script.text
    assert "clarification-options" in script.text
    assert "为什么继续搜索" in script.text
    assert "delivery-overview" in script.text
    assert "/runtime/mode" in script.text
    assert "toggleRuntimeMode" in script.text
    assert "onlineConfigured" in script.text
    assert "完整在线尚未配置" in script.text
    assert "快速演示" in script.text
    assert "完整在线" in script.text
    assert "轻量搜索" in script.text
    assert "收到后会先判断难度，再选择直连、轻量或完整反思路径。" in script.text
    assert "收到后会先拆解能力，再决定从哪里搜。" not in script.text
    assert "difficultyRoute" in script.text
    assert "plannedStages" in script.text
    assert "is-skipped" in script.text
    assert 'direct_search: ["direct_search", "execute", "finalize"]' in script.text
    assert 'single_pass: ["understand", "execute", "judge", "evaluate", "finalize"]' in script.text
    assert "function routeStageTotal" in script.text
    assert "updateRouteSummary" in script.text
    assert "`${intent} · ${routeLabel(route)} · ${stages} 阶段`" in script.text
    assert "function formatSeconds(milliseconds)" in script.text
    assert 'setMetric("m-elapsed", formatSeconds(' in script.text
    assert "chat-run-details" not in script.text
    assert "raw event" in script.text
    assert 'modeChip.removeAttribute("title")' in script.text
    assert 'modeChip.setAttribute("aria-label"' in script.text
    assert "LLM、检索工具与候选人库会一起切换" not in script.text

    index = client.get("/")
    assert "快速演示" in index.text
    assert 'id="composer-mode"' in index.text
    assert 'id="delivery-overview"' in index.text
    assert 'id="candidate-method-note"' in index.text
    assert 'id="route-summary"' in index.text
    assert 'id="route-intent"' in index.text
    assert 'id="sidebar-new"' in index.text
    assert 'id="agent-session-list"' in index.text
    assert "最近会话" in index.text
    assert "SSE 实时事件" not in index.text

    styles = client.get("/styles.css")
    assert styles.status_code == 200
    assert styles.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert "text/css" in styles.headers["content-type"]
    assert styles.text.strip()
    assert ".mode-toggle" in styles.text
    assert ".chat-progress-track" in styles.text
    assert ".chat-delivery-overview" in styles.text
    assert ".clarification-options" in styles.text
    assert ".mode-confirm-actions" in styles.text
    assert ".rail-item.is-skipped" in styles.text
    assert ".chat-progress-segment.is-skipped" in styles.text
    assert ".route-summary" in styles.text
    assert ".agent-sidebar" in styles.text
    assert ".sidebar-toggle" in styles.text
    assert ".topbar-leading" in styles.text
    assert ".sidebar-collapse" in styles.text
    assert ".sidebar-rail-toggle" in styles.text
    assert ".agent-app.is-sidebar-collapsed" in styles.text
    assert ".agent-app.is-sidebar-open" in styles.text
    assert ".agent-session-list" in styles.text
    assert ".intro-grid" in styles.text
    assert ".candidate-name-link" in styles.text
    assert ".cand-resume" in styles.text
    assert ".chat-run-details" not in styles.text
    assert ".raw > summary" in styles.text


def test_resume_page_and_assets_are_served(client: TestClient) -> None:
    page = client.get("/resume.html")
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert page.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert "候选人简历" in page.text
    assert 'id="resume-sheet"' in page.text
    assert 'id="resume-work-list"' in page.text
    assert 'id="resume-projects-list"' in page.text
    assert 'id="resume-education-list"' in page.text
    assert 'id="resume-detail-notice"' in page.text
    assert "工作经历" in page.text
    assert "项目经历" in page.text
    assert "教育经历" in page.text
    assert "返回检索结果" in page.text
    assert "/resume.css?v=1" in page.text
    assert "/resume.js?v=1" in page.text

    script = client.get("/resume.js")
    assert script.status_code == 200
    assert script.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert "javascript" in script.headers["content-type"]
    assert "search-agent:candidate:" in script.text
    assert "function renderResume" in script.text
    assert "function normalizeWorkRow" in script.text
    assert "function normalizeEducationRow" in script.text
    assert "school_name" in script.text
    assert "start_date" in script.text
    assert "/sessions/" in script.text
    assert "function fetchSessionCandidate" in script.text
    assert "function hasResumeDetails" in script.text
    assert "fallback?.token" in script.text
    assert "function rememberCandidate" in script.text
    assert "简历详情已过期" in script.text
    assert "window.print()" in script.text

    styles = client.get("/resume.css")
    assert styles.status_code == 200
    assert styles.headers["cache-control"] == "no-cache, no-store, must-revalidate"
    assert "text/css" in styles.headers["content-type"]
    assert ".resume-columns" in styles.text
    assert ".resume-history" in styles.text
    assert ".resume-skill-list" in styles.text
    assert ".resume-detail-notice" in styles.text
    assert "@media print" in styles.text
    assert "[hidden]" in styles.text


def test_search_response_exposes_delivery_target_and_end_reason(client: TestClient) -> None:
    resp = client.post("/search", json={"query": QUERY, "topK": 5, "details": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["topK"] == 5
    assert body["debug"]["finalizeReason"]


def test_web_assets_are_present_in_the_package() -> None:
    web = files("search_agent").joinpath("web")
    for name in (
        "index.html",
        "styles.css",
        "app.js",
        "resume.html",
        "resume.css",
        "resume.js",
    ):
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
        ("你是谁", "IDENTITY"),
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
    conversations = ConversationStore(connect_redis=False)
    company_id = 800_000_000 + (id(conversations) % 100_000_000)
    app = create_app(tool=tool, llm_client=llm, conversation_store=conversations)
    kwargs = (
        {"params": {"query": query, "topK": 5, "companyId": company_id}}
        if method == "GET"
        else {
            "json": {
                "query": query,
                "topK": 5,
                "details": True,
                "companyId": company_id,
            }
        }
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
    assert payload["conversationId"]
    assert payload["conversationTurns"] == 1
    assert llm.calls == 0
    assert tool.calls == 0
    assert "event: stage" not in text
    assert "event: result" not in text
    assert "event: error" not in text

    rows = conversations.list(company_id, None)
    assert len(rows) == 1
    assert rows[0].title == query
    assert rows[0].last_query == query
    assert rows[0].status == "reply"
    assert rows[0].turn_count == 1


def test_routed_reply_reuses_conversation_id() -> None:
    llm = _ExplodingLlm()
    tool = _ExplodingSearchTool()
    conversations = ConversationStore(connect_redis=False)
    app = create_app(tool=tool, llm_client=llm, conversation_store=conversations)

    with TestClient(app) as routed:
        with routed.stream(
            "POST",
            "/search/stream",
            json={"query": "你好", "topK": 5, "details": True, "companyId": 39978},
        ) as first_response:
            first_text = "".join(first_response.iter_text())
        first_payload = _event_payload(_frames(first_text)[0])[1]

        with routed.stream(
            "POST",
            "/search/stream",
            json={
                "query": "你能做什么？",
                "topK": 5,
                "details": True,
                "conversationId": first_payload["conversationId"],
                "companyId": 39978,
            },
        ) as second_response:
            second_text = "".join(second_response.iter_text())
        second_payload = _event_payload(_frames(second_text)[0])[1]

    assert second_payload["conversationId"] == first_payload["conversationId"]
    assert second_payload["conversationTurns"] == 2
    memory = conversations.load(first_payload["conversationId"])
    assert memory is not None
    assert [turn.query for turn in memory.turns] == ["你好", "你能做什么？"]
    assert [turn.status for turn in memory.turns] == ["reply", "reply"]
    assert llm.calls == 0
    assert tool.calls == 0


def test_greeting_plus_recruitment_uses_direct_search(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/search/stream",
        json={"query": "你好，帮我找 Python 工程师", "topK": 5, "details": True},
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())

    assert "event: reply" not in text
    assert '"stage": "direct_search"' in text
    assert '"stage": "understand"' not in text
    assert "event: result" in text


def test_stream_uses_direct_search_for_skill_only_search(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/search/stream",
        json={"query": "帮我找会 Python 的", "topK": 6, "details": True},
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())

    assert "event: result" in text
    stage_frames = [frame for frame in _frames(text) if _event_payload(frame)[0] == "stage"]
    stages = [_event_payload(frame)[1]["stage"] for frame in stage_frames]
    assert stages == ["direct_search", "execute", "finalize"]
    assert "understand" not in stages
    assert "judge" not in stages
    assert "reflect" not in stages
    assert "revise" not in stages
    direct = next(
        payload
        for _, payload in (_event_payload(frame) for frame in stage_frames)
        if payload["stage"] == "direct_search"
    )
    assert direct["detail"]["requestIntent"] == "SEARCH"
    assert direct["detail"]["intentRule"] == "search"
    assert direct["detail"]["difficultyRoute"] == "direct_search"
    assert direct["detail"]["plannedStages"] == stages
    assert direct["detail"]["skills"] == ["Python"]
    assert direct["detail"]["ir"]["meta"]["notes"] == ["direct_search:deterministic_parse"]
    _, payload = _event_payload(_frames(text)[-1])
    assert payload["topK"] == 6
    assert payload["total"] > 0
    assert payload.get("clarification") is None
    assert payload["debug"]["requestIntent"] == "SEARCH"
    assert payload["debug"]["intentRule"] == "search"
    assert payload["debug"]["difficultyRoute"] == "direct_search"
    assert payload["debug"]["executedStages"] == stages
    assert payload["debug"]["agentMetrics"]["meta"]["llmCalls"] == 1
    assert payload["debug"]["agentMetrics"]["meta"]["agentLlmCalls"] == 0
    assert payload["debug"]["agentMetrics"]["meta"]["parserLlmCalls"] == 1


def test_stream_direct_search_uses_v2_parser_without_agent_judge() -> None:
    llm = ScriptedLlmClient()
    tool = _MemorySearchSpy()
    app = create_app(tool=tool, llm_client=llm)

    with TestClient(app) as direct:
        with direct.stream(
            "POST",
            "/search/stream",
            json={"query": "帮我找会 Python 的", "topK": 5, "details": True},
        ) as resp:
            text = "".join(resp.iter_text())

    assert "event: error" not in text
    assert [schema for schema, _ in llm.calls] == ["hybrid_search_v2_ir"]
    assert tool.search_calls == 1
    assert tool.rerank_calls == 0


def test_stream_keeps_lightweight_route_for_structured_clarification_option(
    client: TestClient,
) -> None:
    with client.stream(
        "POST",
        "/search/stream",
        json={
            "query": "寻找 Python 后端工程师，重点考察服务端 API、微服务和系统设计经验",
            "topK": 5,
            "details": True,
        },
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())

    stage_frames = [frame for frame in _frames(text) if _event_payload(frame)[0] == "stage"]
    stages = [_event_payload(frame)[1]["stage"] for frame in stage_frames]
    assert stages == ["understand", "execute", "judge", "evaluate", "finalize"]
    assert "plan" not in stages
    assert "reflect" not in stages
    assert "revise" not in stages
    _, payload = _event_payload(_frames(text)[-1])
    assert payload["debug"]["difficultyRoute"] == "single_pass"
    assert payload["debug"]["executedStages"] == stages


def test_stream_keeps_reflective_route_for_complex_query(client: TestClient) -> None:
    with client.stream(
        "POST",
        "/search/stream",
        json={"query": "找能力可迁移的架构师", "topK": 5, "details": True},
    ) as resp:
        assert resp.status_code == 200
        text = "".join(resp.iter_text())

    stage_frames = [frame for frame in _frames(text) if _event_payload(frame)[0] == "stage"]
    stages = [_event_payload(frame)[1]["stage"] for frame in stage_frames]
    assert stages == [
        "understand",
        "plan",
        "execute",
        "judge",
        "evaluate",
        "reflect",
        "revise",
        "finalize",
    ]
    understand = next(
        payload
        for _, payload in (_event_payload(frame) for frame in stage_frames)
        if payload["stage"] == "understand"
    )
    assert understand["detail"]["requestIntent"] == "SEARCH"
    assert understand["detail"]["intentRule"] == "search"
    assert understand["detail"]["difficultyRoute"] == "standard"
    assert understand["detail"]["plannedStages"] == stages
    _, payload = _event_payload(_frames(text)[-1])
    assert payload["debug"]["requestIntent"] == "SEARCH"
    assert payload["debug"]["intentRule"] == "search"
    assert payload["debug"]["difficultyRoute"] == "standard"


def test_complex_query_never_invokes_the_v2_parser() -> None:
    llm = ScriptedLlmClient()
    tool = _MemorySearchSpy()
    app = create_app(tool=tool, llm_client=llm)

    with TestClient(app) as complex_client:
        with complex_client.stream(
            "POST",
            "/search/stream",
            json={"query": "找能力可迁移的架构师", "topK": 5, "details": True},
        ) as resp:
            assert resp.status_code == 200
            text = "".join(resp.iter_text())

    assert "event: result" in text
    schemas = [schema for schema, _ in llm.calls]
    assert "agent_understand" in schemas
    assert "hybrid_search_v2_ir" not in schemas
