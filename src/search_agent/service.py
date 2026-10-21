"""SearchAgentService: API entry adapter.

Thin layer: ``SearchRequest`` -> build initial ``SearchState`` -> construct
``Deps`` -> run Harness -> ``SearchResponse`` (adds ``agentTrace`` to ``debug``).
No loop logic here -- that lives in the Harness.
"""

from __future__ import annotations

import logging
import time
from threading import Event
from typing import Any, Callable

from search_agent.config import budget_from_env, recall_pool_size_from_env
from search_agent.contract.tool import SearchTool, SearchToolRequest, ToolCandidate
from search_agent.conversation_memory import (
    ConversationMemory,
    ConversationStore,
    ConversationTurn,
    new_conversation_id,
)
from search_agent.core.budget import BudgetState
from search_agent.core.cancellation import (
    SearchCancelled,
    is_cancelled,
    mark_cancelled,
)
from search_agent.core.experience import ExperienceStore, attribute_qualified, query_family
from search_agent.direct_search import (
    DIRECT_SEARCH_ROUTE,
    DirectSearchPlan,
    build_direct_search_plan,
)
from search_agent.hybrid_v2.parser import parse_query_to_ir
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

        perm_dict = req.permission.model_dump() if req.permission else None
        conversation_history: list[dict] = []
        excluded_doc_ids: list[str] = []
        conv_id = req.conversationId or new_conversation_id()
        conversation_turns = 0
        if req.conversationId:
            try:
                memory = self._conversation_store.load(req.conversationId)
                if memory and self._conversation_matches_scope(memory, req):
                    conversation_history = [
                        turn.model_dump() for turn in memory.turns if turn.status != "reply"
                    ]
                    conversation_turns = memory.total_turns
                    excluded_doc_ids = list(memory.excluded_candidate_ids)
                elif memory:
                    conv_id = new_conversation_id()
            except Exception:
                pass

        direct_plan = build_direct_search_plan(req.query)
        if direct_plan is not None:
            state = self._run_direct_search(
                req,
                direct_plan,
                budget=budget,
                conversation_history=conversation_history,
                excluded_doc_ids=excluded_doc_ids,
                cancel_event=cancel_event,
            )
        else:
            bs = BudgetState(budget=budget)
            deps = Deps(
                llm=MeteredLlmClient(self._llm, bs, cancel_event=cancel_event),
                tool=self._tool,
                budget_state=bs,
                event_sink=self._event_sink,
                experience=self._experience,
                cancel_event=cancel_event,
            )
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
                "excluded_doc_ids": excluded_doc_ids,
            }
            state = self._runner.run(initial_state, deps)

        # Defensive output filter: a backend must not be able to re-surface a
        # candidate the recruiter explicitly excluded in this conversation.
        if excluded_doc_ids and not state.get("cancelled"):
            excluded = set(excluded_doc_ids)
            state["final_items"] = [
                tc for tc in (state.get("final_items") or []) if tc.doc_id not in excluded
            ]
            state["final_total"] = len(state["final_items"])

        if state.get("cancelled"):
            resp = self._handle_cancelled(req, state)
        elif state.get("fallback_baseline"):
            resp = self._handle_fallback(req, state)
        elif state.get("need_clarify"):
            resp = self._handle_clarify(req, state)
        else:
            resp = self._build_completed_response(req, state)

        if not state.get("cancelled"):
            if direct_plan is None:
                self._record_experience(state)
            if conv_id:
                try:
                    memory = self._save_conversation_turn(
                        conv_id,
                        req,
                        state,
                        resp,
                        turn_index=conversation_turns,
                    )
                    conversation_turns = memory.total_turns
                except Exception:
                    pass

        return resp.model_copy(
            update={
                "conversationId": conv_id,
                "conversationTurns": conversation_turns,
                "topK": req.topK,
            }
        )

    def _run_direct_search(
        self,
        req: SearchRequest,
        plan: DirectSearchPlan,
        *,
        budget: Budget,
        conversation_history: list[dict],
        excluded_doc_ids: list[str],
        cancel_event: Event | None,
    ) -> dict[str, Any]:
        """Run the complete Hybrid V2 simple-search path.

        The deterministic router decides that a query is an explicit search;
        the V2 parser LLM then owns NL-to-IR. The result is executed by one
        SearchTool call, without entering any agent orchestration stage.
        """
        started = time.perf_counter()
        stages = ["direct_search", "execute", "finalize"]
        budget_state = BudgetState(budget=budget)
        state: dict[str, Any] = {
            "query": req.query,
            "company_id": req.companyId,
            "hr_id": req.hrId,
            "permission": req.permission.model_dump() if req.permission else None,
            "routing": req.companyId,
            "top_k": req.topK,
            "recall_pool_size": req.recallPoolSize or req.topK,
            "details": req.details,
            "budget": budget,
            "conversation_history": conversation_history,
            "excluded_doc_ids": excluded_doc_ids,
            "job_profile": plan.job_profile,
            "talent_profile": plan.talent_profile,
            "difficulty_route": DIRECT_SEARCH_ROUTE,
            "planned_stages": stages,
            "executed_stages": [],
            "finalize_reason": "direct_search",
            "phase": "done",
            "cancelled": False,
            "final_items": [],
            "final_total": 0,
        }

        if is_cancelled(cancel_event):
            mark_cancelled(state, stage="direct_search")
            return state

        parser_started = time.perf_counter()
        try:
            parsed = parse_query_to_ir(
                req.query,
                llm_client=MeteredLlmClient(
                    self._llm,
                    budget_state,
                    cancel_event=cancel_event,
                ),
            )
        except SearchCancelled:
            mark_cancelled(state, stage="direct_search")
            return state
        parser_elapsed_ms = int((time.perf_counter() - parser_started) * 1000)
        budget_state.record_llm(parsed.attempts)

        if parsed.intent is not None:
            effective_plan = plan.with_intent(
                parsed.intent,
                ir_source="hybrid_v2_parser",
                parser_attempts=parsed.attempts,
            )
        else:
            effective_plan = plan.with_intent(
                plan.intent,
                ir_source="local_rules_fallback",
                parser_attempts=parsed.attempts,
                parser_error=parsed.error,
            )
            state["degraded_reason"] = "hybrid_v2_parse_failed"

        state["job_profile"] = effective_plan.job_profile
        state["talent_profile"] = effective_plan.talent_profile
        state["direct_search"] = {
            "plan": effective_plan.event_payload(),
            "parser": {
                "source": effective_plan.ir_source,
                "attempts": parsed.attempts,
                "error": parsed.error,
                "elapsedMs": parser_elapsed_ms,
                "llmCalls": budget_state.llm_calls,
                "promptTokens": budget_state.prompt_tokens,
                "completionTokens": budget_state.completion_tokens,
                "tokensEstimated": budget_state.tokens_estimated,
                "llmRetries": budget_state.llm_retries,
            },
        }
        self._emit_direct_stage("direct_search", state)

        if is_cancelled(cancel_event):
            mark_cancelled(state, stage="direct_search")
            return state

        tool_started = time.perf_counter()
        result = self._tool.search_with_intent(
            SearchToolRequest(
                intent=effective_plan.intent,
                query_text=req.query,
                company_id=req.companyId,
                hr_id=req.hrId,
                permission=state["permission"],
                top_k=req.topK,
                recall_pool_size=req.recallPoolSize or req.topK,
                routing=req.companyId,
                details=req.details,
                exclude_doc_ids=list(excluded_doc_ids),
                skip_rerank=False,
            )
        )
        tool_elapsed_ms = int((time.perf_counter() - tool_started) * 1000)
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        final_items = list(result.items[: req.topK])
        rerank_used = "rerank" in result.timings_ms
        degraded_reason = (
            ";".join(
                reason
                for reason in (state.get("degraded_reason"), result.degraded_reason)
                if reason
            )
            or None
        )
        state.update(
            {
                "final_items": final_items,
                "final_total": len(final_items),
                "final_search_total": max(result.total, len(result.items)),
                "degraded_reason": degraded_reason,
                "rerank_used": rerank_used,
            }
        )
        state["direct_search"].update(
            {
                "recalled": max(result.total, len(result.items)),
                "returned": len(result.items),
                "quality": result.quality,
                "timingsMs": dict(result.timings_ms),
                "parseElapsedMs": parser_elapsed_ms,
                "toolElapsedMs": tool_elapsed_ms,
                "elapsedMs": elapsed_ms,
            }
        )
        self._emit_direct_stage("execute", state)

        if is_cancelled(cancel_event):
            mark_cancelled(state, stage="execute")
            return state

        self._emit_direct_stage("finalize", state)
        self._attach_direct_observability(state, req=req)
        return state

    def _emit_direct_stage(self, stage: str, state: dict[str, Any]) -> None:
        state["executed_stages"].append(stage)
        if self._event_sink is None:
            return
        try:
            self._event_sink(stage, state)
        except Exception:  # noqa: BLE001 - observability must not fail a run
            logger.debug("direct-search event sink failed for %s", stage, exc_info=True)

    @staticmethod
    def _attach_direct_observability(state: dict[str, Any], *, req: SearchRequest) -> None:
        """Build a trace separating Agent LLM calls from V2 parser calls."""
        direct = state.get("direct_search") or {}
        parser = direct.get("parser") or {}
        items = state.get("final_items") or []
        elapsed_ms = int(direct.get("elapsedMs") or 0)
        tool_elapsed_ms = int(direct.get("toolElapsedMs") or 0)
        parser_calls = int(parser.get("llmCalls") or 0)
        prompt_tokens = int(parser.get("promptTokens") or 0)
        completion_tokens = int(parser.get("completionTokens") or 0)
        budget = state.get("budget")
        direct_trace = dict(direct.get("plan") or {})
        direct_trace["parser"] = dict(parser)
        state["agent_trace"] = {
            "route": DIRECT_SEARCH_ROUTE,
            "iterations": [],
            "final": {
                "mergedPoolSize": int(direct.get("returned") or 0),
                "rerankedTopK": len(items),
                "rerankUsed": bool(state.get("rerank_used")),
            },
            "budget": budget.model_dump() if budget else {},
            "budgetUsed": {
                "llmCalls": parser_calls,
                "agentLlmCalls": 0,
                "parserLlmCalls": parser_calls,
                "elapsedMs": elapsed_ms,
            },
            "degradedReason": state.get("degraded_reason"),
            "toolCalls": [
                {
                    "pathId": "direct_search",
                    "elapsedMs": tool_elapsed_ms,
                    "total": int(direct.get("recalled") or 0),
                    "quality": direct.get("quality"),
                }
            ],
            "directSearch": direct_trace,
        }
        state["agent_metrics"] = {
            "recall": {
                "rate": round(len(items) / max(req.topK, 1), 3),
                "judged": 0,
                "trulyMatched": 0,
                "target": req.topK,
                "qualityDistribution": (
                    {str(direct.get("quality")): 1} if direct.get("quality") else {}
                ),
                "surfacedDocIds": [tc.doc_id for tc in items],
            },
            "rounds": {"iterations": 0, "newQualifiedPerRound": []},
            "match": {"abilityCoverage": 0.0, "hardMatchHitRate": 0.0},
            "reasoning": {"sourceConversion": {}},
            "reflection": {"gain": []},
            "meta": {
                "llmCalls": parser_calls,
                "agentLlmCalls": 0,
                "parserLlmCalls": parser_calls,
                "llmRetries": int(parser.get("llmRetries") or 0),
                "promptTokens": prompt_tokens,
                "completionTokens": completion_tokens,
                "totalTokens": prompt_tokens + completion_tokens,
                "tokensEstimated": bool(parser.get("tokensEstimated")),
                "elapsedMs": elapsed_ms,
                "degradedReason": state.get("degraded_reason"),
                "rerankUsed": bool(state.get("rerank_used")),
                "fallbackBaseline": False,
            },
        }

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
        debug["difficultyRoute"] = state.get("difficulty_route")
        debug["plannedStages"] = list(state.get("planned_stages") or [])
        debug["executedStages"] = list(state.get("executed_stages") or [])
        if state.get("direct_search"):
            debug["directSearch"] = state["direct_search"]
        return debug

    @staticmethod
    def _snapshot_candidate(tc: ToolCandidate) -> dict[str, Any]:
        """Keep the fields needed to restore both the result card and resume."""
        item = SearchAgentService._to_candidate_item(tc)
        fields = {
            "id",
            "docId",
            "candidateProfileId",
            "userId",
            "newestAppId",
            "score",
            "matchRate",
            "name",
            "workYears",
            "summaryEn",
            "avatar",
            "summary",
            "gender",
            "age",
            "currentResidence",
            "tags",
            "skills",
            "certificates",
            "languages",
            "workExperiences",
            "internshipExperiences",
            "projectExperiences",
            "educations",
            "expectedPositions",
            "expectedIndustries",
            "expectedCities",
            "expectedSalary",
            "expectedWorkType",
            "workState",
            "profileSnapshot",
            "matchExplanation",
        }
        return item.model_dump(
            by_alias=True,
            exclude_none=True,
            include=fields,
        )

    def _save_conversation_turn(
        self,
        conversation_id: str,
        req: SearchRequest,
        state: dict[str, Any],
        response: SearchResponse,
        *,
        turn_index: int,
    ) -> ConversationMemory:
        """Persist one complete turn so the sidebar can restore the session."""
        job = state.get("job_profile")
        summary: dict[str, Any] = {}
        if job:
            summary = {
                "title": job.title,
                "must_abilities": [a.name for a in (job.must_abilities or [])],
                "hard_constraints": [c.model_dump() for c in (job.hard_constraints or [])],
                "difficulty": job.subjective_difficulty,
            }

        final_items = list(state.get("final_items") or [])
        top_names = [tc.item.name for tc in final_items[:3] if tc.item.name]
        top_ids = [tc.doc_id for tc in final_items]
        snapshots = [self._snapshot_candidate(tc) for tc in final_items[:10]]
        cancelled = bool(state.get("cancelled"))
        fallback = bool(state.get("fallback_baseline"))
        clarification = response.clarification
        if cancelled:
            status = "cancelled"
            assistant_summary = "本轮已取消，未交付候选人。"
        elif clarification:
            status = "clarification"
            questions = [
                str(question).strip()
                for question in (clarification.get("questions") or [])
                if str(question).strip()
            ]
            assistant_summary = "需要补充招聘条件：" + ("；".join(questions) or "岗位方向")
        elif fallback:
            status = "degraded"
            assistant_summary = "本轮触发降级，未交付候选人。"
        elif final_items:
            status = "completed"
            assistant_summary = f"本轮检索完成，交付 {len(final_items)} 位候选人。"
        else:
            status = "completed"
            assistant_summary = "本轮检索完成，未找到符合条件的新候选人。"

        if job and job.title:
            assistant_summary = f"岗位：{job.title}。" + assistant_summary
        if top_names:
            assistant_summary += " 优先展示：" + "、".join(top_names) + "。"

        turn = ConversationTurn(
            turn_index=turn_index,
            query=req.query,
            assistant_summary=assistant_summary,
            job_profile_summary=summary,
            result_count=response.total,
            top_candidate_names=top_names,
            top_candidate_ids=top_ids,
            result_items=snapshots,
            route=state.get("difficulty_route") or DIRECT_SEARCH_ROUTE,
            status=status,
            query_token=response.queryToken,
            clarification=clarification,
            top_k=req.topK,
            timestamp=int(time.time()),
        )
        return self._conversation_store.append_turn(
            conversation_id=conversation_id,
            company_id=req.companyId,
            hr_id=req.hrId,
            turn=turn,
        )


__all__ = ["SearchAgentService"]
