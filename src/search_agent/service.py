"""SearchAgentService: API entry adapter.

Thin layer: ``SearchRequest`` -> build initial ``SearchState`` -> construct
``Deps`` -> run Harness -> ``SearchResponse`` (adds ``agentTrace`` to ``debug``).
No loop logic here -- that lives in the Harness.
"""

from __future__ import annotations

from typing import Any, Callable

from search_agent.config import budget_from_env, recall_pool_size_from_env
from search_agent.contract.tool import SearchTool, ToolCandidate
from search_agent.conversation_memory import (
    ConversationStore,
    ConversationTurn,
)
from search_agent.core.budget import BudgetState
from search_agent.llm.client import LlmClient, OpenAILlmClient
from search_agent.models.api import CandidateItem, SearchRequest, SearchResponse
from search_agent.models.artifacts import Budget
from search_agent.runner import HarnessRunner, LoopRunner
from search_agent.stages.base import Deps


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
    ) -> None:
        self._tool = tool
        self._llm = llm_client or OpenAILlmClient()
        self._runner = runner or LoopRunner()
        self._fallback_fn = fallback_fn
        self._event_sink = event_sink
        self._conversation_store = ConversationStore()

    def search(self, req: SearchRequest) -> SearchResponse:
        budget = _budget_from_settings(req.topK)
        bs = BudgetState(budget=budget)
        deps = Deps(
            llm=self._llm,
            tool=self._tool,
            budget_state=bs,
            event_sink=self._event_sink,
        )

        perm_dict = req.permission.model_dump() if req.permission else None
        conversation_history: list[dict] = []
        conv_id = getattr(req, "conversationId", None)
        if conv_id:
            try:
                memory = self._conversation_store.load(conv_id)
                if memory:
                    conversation_history = [t.model_dump() for t in memory.turns]
            except Exception:
                pass

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

        if conv_id:
            try:
                self._save_conversation_turn(conv_id, req, state)
            except Exception:
                pass

        if state.get("fallback_baseline"):
            return self._handle_fallback(req, state)
        if state.get("need_clarify"):
            return self._handle_clarify(req, state)

        items = [self._to_candidate_item(tc) for tc in state.get("final_items", [])]
        return SearchResponse(
            query=req.query,
            total=state.get("final_total", len(items)),
            items=items,
            debug=self._build_debug(state, req),
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

    def _handle_clarify(self, req: SearchRequest, state: dict[str, Any]) -> SearchResponse:
        job = state.get("job_profile")
        questions = job.clarify_questions if job else []
        return SearchResponse(
            query=req.query,
            total=0,
            items=[],
            clarification={"needClarification": True, "questions": questions},
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

    @staticmethod
    def _build_debug(state: dict[str, Any], req: SearchRequest) -> dict[str, Any] | None:
        if not req.details:
            return None
        debug: dict[str, Any] = {"parseMode": "search_agent"}
        if state.get("degraded_reason"):
            debug["degradedReason"] = state["degraded_reason"]
        debug["agentTrace"] = state.get("agent_trace", {})
        debug["agentMetrics"] = state.get("agent_metrics", {})
        if state.get("validation_issues"):
            debug["validationIssues"] = state["validation_issues"]
        return debug

    def _save_conversation_turn(
        self, conversation_id: str, req: Any, state: dict[str, Any]
    ) -> None:
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
        turn = ConversationTurn(
            turn_index=len(state.get("conversation_history", [])),
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


__all__ = ["SearchAgentService"]
