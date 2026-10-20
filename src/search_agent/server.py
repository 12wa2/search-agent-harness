"""Optional FastAPI HTTP entry point (``pip install -e ".[api]"``).

Endpoints:

- ``GET  /``              -- the bundled web console (static assets, when present).
- ``POST /search``        -- JSON ``SearchRequest`` in, ``SearchResponse`` out.
- ``GET  /search/stream`` -- Server-Sent Events. Requests are routed before
  the Harness: greetings, help questions and clearly unrelated tasks receive a
  single terminal ``reply`` frame; searches stream one frame per stage and a
  final ``result`` frame.
- ``POST /search/stream`` -- the same stream from a JSON body, keeping queries
  out of URLs and access logs.
- ``GET  /healthz``       -- version, active mode and backend class names.
- ``POST /runtime/mode``  -- switch later searches between two complete presets:
  quick demo (scripted LLM + in-memory tool) and full online (real LLM +
  configured retrieval tool, normally Hybrid V2).

``create_app`` accepts ``tool`` / ``llm_client`` so the same server can front
a real backend; defaults to the zero-key offline stack. Run it via::

    search-agent serve --offline

or with uvicorn directly (module-level demo app)::

    uvicorn search_agent.server:app
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import Response
from starlette.types import Scope

from search_agent import __version__
from search_agent.contract.tool import SearchTool
from search_agent.conversation_memory import (
    ConversationMemory,
    ConversationStore,
    ConversationSummary,
    ConversationTurn,
    new_conversation_id,
)
from search_agent.demo import DEMO_DATA, ScriptedLlmClient
from search_agent.intent_router import Intent, IntentRouter, RouteDecision
from search_agent.llm.client import LlmClient, OpenAILlmClient
from search_agent.llm.runtime import RuntimeLlmRouter, RuntimeMode, RuntimeModeError
from search_agent.models.api import SearchRequest, SearchResponse
from search_agent.service import SearchAgentService
from search_agent.session import AgentSessionPage, AgentSessionStore
from search_agent.tools.memory import InMemorySearchTool
from search_agent.tools.runtime import RuntimeSearchTool

#: Console assets live inside the package so wheels and the Docker image both ship them.
_WEB_DIR = Path(__file__).resolve().parent / "web"
_PROCESS_STARTED_AT = time.time()
logger = logging.getLogger(__name__)


class _NoCacheStaticFiles(StaticFiles):
    """Keep local UI changes visible without relying on a manual hard refresh."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response


class RuntimeModeRequest(BaseModel):
    """Request body for switching the complete runtime preset."""

    mode: Literal["offline", "online"]


class ConversationCreateRequest(BaseModel):
    """Create an empty server-side conversation."""

    companyId: int = 1
    hrId: int | None = None
    title: str = ""


class CandidateExclusionRequest(BaseModel):
    """Toggle one candidate in the conversation's explicit exclusion set."""

    excluded: bool


def _stage_payload(event: str, state: dict[str, Any]) -> dict[str, Any]:
    """JSON-safe summary of one stage event (mirrors the CLI event printer)."""
    if event == "direct_search":
        direct = state.get("direct_search") or {}
        payload = dict(direct.get("plan") or {})
        payload.update(
            {
                "difficultyRoute": state.get("difficulty_route"),
                "plannedStages": list(state.get("planned_stages") or []),
                "historyTurns": len((state.get("conversation_history") or [])[-3:]),
            }
        )
        return payload
    if event == "understand":
        job = state.get("job_profile")
        if job is None:
            return {}
        return {
            "jobTitle": job.title,
            "mustAbilities": [a.name for a in job.must_abilities],
            "hardConstraints": len(job.hard_constraints),
            "hardConstraintDetails": [
                constraint.model_dump() for constraint in job.hard_constraints
            ],
            "historyTurns": len((state.get("conversation_history") or [])[-3:]),
            "needsClarification": bool(job.needs_clarification),
            "clarifyQuestions": list(job.clarify_questions),
            "difficulty": job.subjective_difficulty,
            "difficultyRoute": state.get("difficulty_route"),
            "plannedStages": list(state.get("planned_stages") or []),
        }
    if event == "plan":
        paths = state.get("paths") or []
        return {"paths": len(paths), "rationales": [p.rationale for p in paths]}
    if event == "execute":
        if state.get("difficulty_route") == "direct_search":
            direct = state.get("direct_search") or {}
            return {
                "paths": 1,
                "recalled": direct.get("recalled", 0),
                "qualities": [f"direct:{direct.get('quality') or 'unknown'}"],
                "timingsMs": direct.get("timingsMs") or {},
                "direct": True,
            }
        results = state.get("path_results") or []
        return {
            "paths": len(results),
            "recalled": sum(pr.tool_result.total for pr in results),
            "qualities": [f"{pr.path_id}:{pr.quality}" for pr in results],
        }
    if event == "judge":
        return {"qualified": len(state.get("accumulated") or {})}
    if event == "evaluate":
        return {"next": state.get("phase"), "stopReason": state.get("finalize_reason")}
    if event == "reflect":
        reflections = state.get("reflections") or []
        if not reflections:
            return {}
        last = reflections[-1]
        return {
            "commonTraits": last.common_traits[:2],
            "gaps": last.gaps[:2],
            "newSoftSignals": len(last.inferred_soft_signals),
        }
    if event == "revise":
        talent = state.get("talent_profile")
        return {
            "softSignals": len(talent.soft_signals) if talent else 0,
            "converged": bool(state.get("profile_converged")),
            "iteration": state.get("iteration"),
        }
    if event == "finalize":
        return {"total": state.get("final_total"), "searchTotal": state.get("final_search_total")}
    if event.startswith("degrade"):
        return {"reason": state.get("degraded_reason")}
    return {}


def _sse(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _record_reply_turn(
    store: ConversationStore,
    req: SearchRequest,
    decision: RouteDecision,
) -> ConversationMemory | None:
    """Archive a routed reply without starting search or changing L1 history."""
    conversation_id = req.conversationId or new_conversation_id()
    try:
        memory = store.load(conversation_id) if req.conversationId else None
        if memory is not None and (memory.company_id != req.companyId or memory.hr_id != req.hrId):
            conversation_id = new_conversation_id()
            memory = None
        return store.append_turn(
            conversation_id,
            req.companyId,
            req.hrId,
            ConversationTurn(
                turn_index=memory.total_turns if memory is not None else 0,
                query=req.query,
                assistant_summary=decision.message,
                route=decision.intent.value,
                status="reply",
                top_k=req.topK,
                timestamp=int(time.time()),
            ),
        )
    except Exception as exc:
        logger.warning("routed reply was not archived: %s", exc)
        return None


async def _stream_reply(
    decision: RouteDecision,
    memory: ConversationMemory | None = None,
) -> AsyncIterator[str]:
    """Yield one terminal reply frame without starting the search Harness."""
    payload = decision.to_payload()
    if memory is not None:
        payload["conversationId"] = memory.conversation_id
        payload["conversationTurns"] = memory.total_turns
    yield _sse("reply", payload)


async def _tracked_search_events(
    events: AsyncIterator[str], runtime: RuntimeLlmRouter
) -> AsyncIterator[str]:
    """Release the runtime run guard after the SSE generator closes."""
    try:
        async for event in events:
            yield event
    finally:
        runtime.end_run()


async def _stream_search(
    tool: SearchTool,
    llm: LlmClient,
    req: SearchRequest,
    *,
    cancel_event: threading.Event | None = None,
    conversation_store: ConversationStore | None = None,
    session_store: AgentSessionStore | None = None,
    request_decision: RouteDecision | None = None,
) -> AsyncIterator[str]:
    """Run one search in a worker thread; yield SSE frames as stages fire.

    Closing the stream sets ``cancel_event``. The harness then stops at its
    next stage boundary or before its next LLM call; an already in-flight
    provider request is allowed to return instead of being force-killed.
    """
    cancel_event = cancel_event or threading.Event()
    decision = request_decision or RouteDecision(intent=Intent.SEARCH, rule="search")
    events: queue.Queue = queue.Queue()

    def sink(event: str, state: dict[str, Any]) -> None:
        detail = _stage_payload(event, state)
        if event in {"understand", "direct_search"}:
            detail["requestIntent"] = decision.intent.value
            detail["intentRule"] = decision.rule
        events.put(("stage", {"stage": event, "detail": detail}))

    def worker() -> None:
        try:
            service = SearchAgentService(
                tool=tool,
                llm_client=llm,
                event_sink=sink,
                conversation_store=conversation_store,
                session_store=session_store,
            )
            resp = service.search(req, cancel_event=cancel_event)
            payload = resp.model_dump(by_alias=True)
            if payload.get("debug") is not None:
                payload["debug"]["requestIntent"] = decision.intent.value
                payload["debug"]["intentRule"] = decision.rule
            events.put(("result", payload))
        except Exception as exc:  # surfaced as an SSE error frame, not a crash
            events.put(("error", {"message": str(exc)}))
        finally:
            events.put(None)

    threading.Thread(target=worker, daemon=True).start()
    try:
        while True:
            item = await asyncio.to_thread(events.get)
            if item is None:
                break
            yield _sse(item[0], item[1])
    finally:
        # StreamingResponse cancels this generator when the client disconnects.
        # The sentinel also releases the to_thread waiter when that happens.
        cancel_event.set()
        events.put_nowait(None)


def create_app(
    *,
    tool: SearchTool | None = None,
    llm_client: LlmClient | None = None,
    online_llm_client: LlmClient | None = None,
    online_tool: SearchTool | None = None,
    dual_backend: bool = False,
    data_path: str | None = None,
    offline: bool = True,
    intent_router: IntentRouter | None = None,
    conversation_store: ConversationStore | None = None,
    session_store: AgentSessionStore | None = None,
) -> FastAPI:
    """Build the HTTP app. Defaults to the zero-key quick-demo preset.

    With ``dual_backend=True``, ``tool`` is the quick-demo tool and
    ``online_tool`` is the full-online tool. The same runtime mode controls
    both the LLM router and ``RuntimeSearchTool`` so a search never mixes
    presets.
    """
    resolved_offline_tool = tool or InMemorySearchTool(str(data_path or DEMO_DATA))
    resolved_online_tool = online_tool if dual_backend else resolved_offline_tool
    if llm_client is None:
        resolved_offline = ScriptedLlmClient()
        resolved_online = online_llm_client or OpenAILlmClient()
        initial_mode: RuntimeMode = "offline" if offline else "online"
    else:
        initial_mode = "offline" if isinstance(llm_client, ScriptedLlmClient) else "online"
        if initial_mode == "offline":
            resolved_offline = llm_client
            resolved_online = online_llm_client or OpenAILlmClient()
        else:
            resolved_offline = ScriptedLlmClient()
            resolved_online = llm_client
    if initial_mode == "online" and resolved_online_tool is None:
        raise ValueError("online mode requires an online search backend")
    runtime = RuntimeLlmRouter(
        resolved_offline,
        resolved_online,
        initial_mode=initial_mode,
    )
    runtime_tool = RuntimeSearchTool(
        resolved_offline_tool,
        resolved_online_tool,
        mode=lambda: runtime.mode,
    )
    router = intent_router or IntentRouter()
    resolved_conversations = conversation_store or ConversationStore()
    resolved_sessions = session_store or AgentSessionStore()
    service = SearchAgentService(
        tool=runtime_tool,
        llm_client=runtime,
        conversation_store=resolved_conversations,
        session_store=resolved_sessions,
    )

    app = FastAPI(
        title="search-agent-harness",
        version=__version__,
        description="Eight-stage reflective retrieval agent over HTTP.",
    )
    app.state.runtime_llm = runtime
    app.state.runtime_tool = runtime_tool

    def runtime_health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "mode": runtime.mode,
            "onlineConfigured": runtime.online_configured and runtime_tool.online_configured,
            "backend": runtime_tool.active_tool_name,
            "offlineBackend": runtime_tool.offline_tool_name,
            "onlineBackend": runtime_tool.online_tool_name,
            "llm": runtime.active_client_name,
            "startedAt": _PROCESS_STARTED_AT,
        }

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return runtime_health()

    @app.post("/runtime/mode")
    def switch_runtime_mode(req: RuntimeModeRequest) -> dict[str, Any]:
        if req.mode == "online" and not runtime_tool.online_configured:
            raise HTTPException(
                status_code=409,
                detail="完整在线模式需要配置 Hybrid V2 检索后端（Elasticsearch）。",
            )
        try:
            runtime.switch_mode(req.mode)
        except RuntimeModeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return runtime_health()

    @app.post("/search")
    def search(req: SearchRequest) -> SearchResponse:
        runtime.begin_run()
        try:
            return service.search(req)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        finally:
            runtime.end_run()

    def stream_response(req: SearchRequest) -> StreamingResponse:
        decision = router.route(req.query)
        if decision.intent is Intent.SEARCH:
            runtime.begin_run()
            body = _tracked_search_events(
                _stream_search(
                    runtime_tool,
                    runtime,
                    req,
                    conversation_store=resolved_conversations,
                    session_store=resolved_sessions,
                    request_decision=decision,
                ),
                runtime,
            )
        else:
            memory = _record_reply_turn(resolved_conversations, req, decision)
            body = _stream_reply(decision, memory)
        return StreamingResponse(
            body,
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/search/stream")
    def search_stream(
        query: str = Query(..., min_length=1),
        topK: int = Query(10, ge=1, le=5000),
        details: bool = Query(False),
        companyId: int = Query(1, gt=0),
        hrId: int | None = Query(None, gt=0),
        conversationId: str | None = Query(None),
        pageSize: int | None = Query(None, ge=1, le=100),
    ) -> StreamingResponse:
        return stream_response(
            SearchRequest(
                query=query,
                topK=topK,
                details=details,
                companyId=companyId,
                hrId=hrId,
                conversationId=conversationId,
                pageSize=pageSize,
            )
        )

    @app.post("/search/stream")
    def search_stream_post(req: SearchRequest) -> StreamingResponse:
        """POST variant: keeps the query out of URLs and access logs."""
        return stream_response(req)

    def conversation_for_scope(
        conversation_id: str,
        company_id: int,
        hr_id: int | None,
    ) -> ConversationMemory:
        memory = resolved_conversations.load(conversation_id)
        if memory is None or memory.company_id != company_id or memory.hr_id != hr_id:
            raise HTTPException(status_code=404, detail="conversation not found")
        return memory

    @app.post("/conversations", response_model=ConversationMemory)
    def create_conversation(req: ConversationCreateRequest) -> ConversationMemory:
        return resolved_conversations.create(
            req.companyId,
            req.hrId,
            title=req.title,
        )

    @app.get("/conversations", response_model=list[ConversationSummary])
    def list_conversations(
        companyId: int = Query(1, gt=0),
        hrId: int | None = Query(None, gt=0),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ) -> list[ConversationSummary]:
        return resolved_conversations.list(
            companyId,
            hrId,
            limit=limit,
            offset=offset,
        )

    @app.get("/conversations/{conversation_id}", response_model=ConversationMemory)
    def get_conversation(
        conversation_id: str,
        companyId: int = Query(1, gt=0),
        hrId: int | None = Query(None, gt=0),
    ) -> ConversationMemory:
        return conversation_for_scope(conversation_id, companyId, hrId)

    @app.delete("/conversations/{conversation_id}")
    def delete_conversation(
        conversation_id: str,
        companyId: int = Query(1, gt=0),
        hrId: int | None = Query(None, gt=0),
    ) -> dict[str, bool]:
        return {
            "deleted": resolved_conversations.delete(
                conversation_id,
                companyId,
                hrId,
            )
        }

    @app.patch(
        "/conversations/{conversation_id}/candidates/{doc_id}",
        response_model=ConversationMemory,
    )
    def set_candidate_exclusion(
        conversation_id: str,
        doc_id: str,
        req: CandidateExclusionRequest,
        companyId: int = Query(1, gt=0),
        hrId: int | None = Query(None, gt=0),
    ) -> ConversationMemory:
        memory = resolved_conversations.set_candidate_excluded(
            conversation_id,
            companyId,
            hrId,
            doc_id,
            excluded=req.excluded,
        )
        if memory is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return memory

    @app.get("/sessions/{query_token}", response_model=AgentSessionPage)
    def session_page(
        query_token: str,
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
    ) -> AgentSessionPage:
        try:
            return resolved_sessions.get_page(query_token, offset=offset, limit=limit)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/sessions/{query_token}")
    def delete_session(query_token: str) -> dict[str, bool]:
        return {"deleted": resolved_sessions.invalidate(query_token)}

    if _WEB_DIR.is_dir():
        # Mounted last so every API route above keeps precedence.
        app.mount("/", _NoCacheStaticFiles(directory=str(_WEB_DIR), html=True), name="web")

    return app


app = create_app()

__all__ = ["app", "create_app"]
