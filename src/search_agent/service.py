"""SearchAgentService: API entry adapter.

Thin layer: ``SearchRequest`` -> build initial ``SearchState`` -> construct
``Deps`` -> run Harness -> ``SearchResponse`` (adds ``agentTrace`` to ``debug``).
No loop logic here -- that lives in the Harness.
"""

from __future__ import annotations

import logging
from threading import Event
from typing import Any, Callable

from search_agent.config import budget_from_env, recall_pool_size_from_env
from search_agent.contract.tool import SearchTool, ToolCandidate
from search_agent.conversation_memory import (
    ConversationMemory,
    ConversationStore,
    ConversationTurn,
    new_conversation_id,
)
from search_agent.core.budget import BudgetState
from search_agent.core.experience import ExperienceStore, attribute_qualified, query_family
from search_agent.llm.client import LlmClient, MeteredLlmClient, OpenAILlmClient
from search_agent.models.api import CandidateItem, PageInfo, SearchRequest, SearchResponse
from search_agent.models.artifacts import Budget
from search_agent.runner import HarnessRunner, LoopRunner
from search_agent.session import AgentSessionStore
from search_agent.stages.base import Deps

logger = logging.getLogger(__name__)


def _budget_from_settings(top_k: int) -> Budget:
    """Budget for one run from SEARCH_AGENT_* environment variables."""
    return budget_from_env(top_k)


class SearchAgentService:
    """Agent search entry point. ``tool`` is the backend SearchTool."""

    def __init__(
        self,
        *,
        tool: SearchTool,
        llm_client: LlmClient | None = None,
        runner: HarnessRunner | None = None,
        fallback_fn: Callable[[SearchRequest], SearchResponse] | None = None,
        event_sink: Callable[[str, dict[str, Any]], None] | None = None,
        experience: ExperienceStore | None = None,
        conversation_store: ConversationStore | None = None,
        session_store: AgentSessionStore | None = None,
    ) -> None:
        self._tool = tool
        self._llm = llm_client or OpenAILlmClient()
        self._runner = runner or LoopRunner()
        self._fallback_fn = fallback_fn
        self._event_sink = event_sink
        self._conversation_store = conversation_store or ConversationStore()
        self._session_store = session_store or AgentSessionStore()
        self._experience = experience or ExperienceStore()

    def search(self, req: SearchRequest, *, cancel_event: Event | None = None) -> SearchResponse:
        budget = _budget_from_settings(req.topK)
        bs = BudgetState(budget=budget)
        deps = Deps(
            llm=MeteredLlmClient(self._llm, bs, cancel_event=cancel_event),
            tool=self._tool,
            budget_state=bs,
            event_sink=self._event_sink,
            experience=self._experience,
            cancel_event=cancel_event,
        )

        perm_dict = req.permission.model_dump() if req.permission else None
        conversation_history: list[dict] = []
        conv_id = req.conversationId or new_conversation_id()
        if req.conversationId:
            try:
                memory = self._conversation_store.load(req.conversationId)
                if memory and self._conversation_matches_scope(memory, req):
                    conversation_history = [t.model_dump() for t in memory.turns]
                elif memory:
                    conv_id = new_conversation_id()
            except Exception:
                pass
        conversation_turns = len(conversation_history)

        initial_state: dict[str, Any] = {
            "query": req.query,
            "company_id": req.companyId,
            "hr_id": req.hrId,
            "permission": perm_dict,
            "routing": req.companyId,
            "top_k": req.topK,
            "recall_pool_size": req.recallPoolSize or recall_pool_size_from_env(req.topK),
            "details": req.details,
            "budget": budget,
            "conversation_history": conversation_history,
        }

        state = self._runner.run(initial_state, deps)
        if not state.get("cancelled"):
            self._record_experience(state)
            if conv_id:
                try:
                    conversation_turns = self._save_conversation_turn(conv_id, req, state)
                except Exception:
                    pass

        if state.get("cancelled"):
            resp = self._handle_cancelled(req, state)
        elif state.get("fallback_baseline"):
            resp = self._handle_fallback(req, state)
        elif state.get("need_clarify"):
            resp = self._handle_clarify(req, state)
        else:
            resp = self._build_completed_response(req, state)
        return resp.model_copy(
            update={
                "conversationId": conv_id,
                "conversationTurns": conversation_turns,
                "topK": req.topK,
            }
        )

    # ── output adapters ─────────────────────────────────────────────────

    @staticmethod
    def _to_candidate_item(tc: ToolCandidate) -> CandidateItem:
        return tc.item.model_copy(
            update={
                "score": tc.score,
                "matchExplanation": tc.match_explanation or None,
            }
        )

    @staticmethod
    def _conversation_matches_scope(memory: ConversationMemory, req: SearchRequest) -> bool:
        # A conversation belongs to exactly one (company, hr) scope. Treat a
        # missing hrId as a scope value as well, so an anonymous request cannot
        # inherit an HR-scoped history (and vice versa).
        return memory.company_id == req.companyId and memory.hr_id == req.hrId

    def _build_completed_response(
        self, req: SearchRequest, state: dict[str, Any]
    ) -> SearchResponse:
        final_items = state.get("final_items", [])
        items = [self._to_candidate_item(tc) for tc in final_items]
        query_token: str | None = None
        page_info: PageInfo | None = None
        if req.pageSize is not None:
            try:
                query_token = self._session_store.create(state)
                page = self._session_store.get_page(
                    query_token,
                    offset=0,
                    limit=req.pageSize,
                )
                items = list(page.items)
                page_info = PageInfo(
                    offset=page.offset,
                    limit=page.limit,
                    total=page.total,
                    currentPage=page.current_page,
                    totalPages=page.total_pages,
                    hasMore=page.has_more,
                    nextOffset=page.next_offset,
                )
            except Exception:  # noqa: BLE001 - pagination is best-effort
                logger.warning("agent session creation failed", exc_info=True)

        return SearchResponse(
            query=req.query,
            total=state.get("final_total", len(items)),
            items=items,
            debug=self._build_debug(state, req),
            queryToken=query_token,
            page=page_info,
        )

    def _handle_cancelled(self, req: SearchRequest, state: dict[str, Any]) -> SearchResponse:
        """Do not return a partial result set as if the run had completed."""
        return SearchResponse(
            query=req.query,
            total=0,
            items=[],
            debug=self._build_debug(state, req),
        )

    def _handle_clarify(self, req: SearchRequest, state: dict[str, Any]) -> SearchResponse:
        job = state.get("job_profile")
        questions = job.clarify_questions if job else []
        clarification: dict[str, Any] = {
            "needClarification": True,
            "questions": questions,
        }
        options = state.get("clarify_options") or []
        if options:
            clarification["options"] = options
        return SearchResponse(
            query=req.query,
            total=0,
            items=[],
            clarification=clarification,
            debug=self._build_debug(state, req),
        )

    def _handle_fallback(self, req: SearchRequest, state: dict[str, Any]) -> SearchResponse:
        if self._fallback_fn is not None:
            resp = self._fallback_fn(req)
            if req.details:
                debug = dict(resp.debug or {})
                debug["agentTrace"] = state.get("agent_trace", {})
                debug["degradedReason"] = state.get("degraded_reason")
                resp = resp.model_copy(update={"debug": debug})
            return resp
        return SearchResponse(
            query=req.query,
            total=0,
            items=[],
            debug=self._build_debug(state, req),
        )

    def _record_experience(self, state: dict[str, Any]) -> None:
        """Remember which moves paid off for this job family (never breaks a run)."""
        try:
            job = state.get("job_profile")
            if job is None or self._experience is None:
                return
            counts = attribute_qualified(state)
            if not counts:
                return
            family = query_family(job)
            for strategy, qualified in counts.items():
                self._experience.record(family, strategy, qualified=qualified)
            logger.debug("experience recorded: family=%s %s", family, counts)
        except Exception:  # noqa: BLE001 - strategy memory is best-effort
            logger.debug("experience recording failed", exc_info=True)

    @property
    def experience(self) -> ExperienceStore:
        """The strategy-memory store this service records into."""
        return self._experience

    @staticmethod
    def _build_debug(state: dict[str, Any], req: SearchRequest) -> dict[str, Any] | None:
        if not req.details:
            return None
        debug: dict[str, Any] = {"parseMode": "search_agent"}
        if state.get("degraded_reason"):
            debug["degradedReason"] = state["degraded_reason"]
        if state.get("cancelled"):
            debug["cancelled"] = True
            debug["cancelledAt"] = state.get("cancelled_at")
        debug["agentTrace"] = state.get("agent_trace", {})
        debug["agentMetrics"] = state.get("agent_metrics", {})
        if state.get("validation_issues"):
            debug["validationIssues"] = state["validation_issues"]
        if state.get("finalize_reason"):
            debug["finalizeReason"] = state["finalize_reason"]
        return debug

    def _save_conversation_turn(
        self, conversation_id: str, req: SearchRequest, state: dict[str, Any]
    ) -> int:
        """Save one conversation turn to Redis."""
        import time as _time

        job = state.get("job_profile")
        summary: dict[str, Any] = {}
        if job:
            summary = {
                "must_abilities": [a.name for a in (job.must_abilities or [])],
                "hard_constraints": [c.model_dump() for c in (job.hard_constraints or [])],
                "difficulty": job.subjective_difficulty,
            }
        final_items = state.get("final_items", [])
        top_names: list[str] = []
        for tc in final_items[:3]:
            name = getattr(getattr(tc, "item", None), "name", None)
            if name:
                top_names.append(name)
        history = state.get("conversation_history", [])
        turn_index = (
            max(
                (int(turn.get("turn_index", -1)) for turn in history),
                default=-1,
            )
            + 1
        )
        turn = ConversationTurn(
            turn_index=turn_index,
            query=req.query,
            job_profile_summary=summary,
            result_count=state.get("final_total", len(final_items)),
            top_candidate_names=top_names,
            timestamp=int(_time.time()),
        )
        self._conversation_store.append_turn(
            conversation_id=conversation_id,
            company_id=req.companyId,
            hr_id=getattr(req, "hrId", None),
            turn=turn,
        )
        return turn_index + 1


__all__ = ["SearchAgentService"]
