"""Harness: the outer reasoning loop controller.

Drives stages (understand->plan->execute->judge->evaluate->reflect->revise
->finalize), manages state transitions, applies degradation on failures, and
builds ``agentTrace``. Does NOT call LLM/ES directly -- only drives stages +
tool (via stages). Thread-safe: all state mutation happens on the main thread;
parallelism is confined to the execute stage's worker pool.
"""

from __future__ import annotations

import logging
from typing import Any

from search_agent.core.degrade import degrade
from search_agent.core.difficulty import (
    adaptive_budget,
    difficulty_route,
)
from search_agent.core.metrics import (
    collect_metrics,
    emit_metrics,
)
from search_agent.core.signature import intent_signature
from search_agent.core.trace import (
    build_agent_trace,
    iteration_trace,
)
from search_agent.models.artifacts import SearchPath
from search_agent.models.ir import (
    CandidateSearchIR,
    SemanticConditionIR,
    StructuredConditionIR,
    WhereNode,
)
from search_agent.stages.base import Deps
from search_agent.stages.evaluate import run_evaluate
from search_agent.stages.execute import run_execute
from search_agent.stages.finalize import run_finalize
from search_agent.stages.judge import run_judge
from search_agent.stages.plan import run_plan
from search_agent.stages.reflect import run_reflect
from search_agent.stages.revise import run_revise
from search_agent.stages.understand import (
    run_understand,
)

_STAGES = {
    "understand": run_understand,
    "plan": run_plan,
    "execute": run_execute,
    "judge": run_judge,
    "evaluate": run_evaluate,
    "reflect": run_reflect,
    "revise": run_revise,
    "finalize": run_finalize,
}

_MAX_STEPS = 60
logger = logging.getLogger(__name__)


def init_state_defaults(initial: dict[str, Any]) -> dict[str, Any]:
    """Copy ``initial`` and fill the loop's default keys."""
    state = dict(initial)
    state.setdefault("phase", "understand")
    state.setdefault("iteration", 0)
    state.setdefault("accumulated", {})
    state.setdefault("seen_doc_ids", set())
    state.setdefault("tried_signatures", set())
    state.setdefault("paths", [])
    state.setdefault("path_results", [])
    state.setdefault("reflections", [])
    state.setdefault("llm_calls", 0)
    state.setdefault("finalize", False)
    state.setdefault("need_clarify", False)
    state.setdefault("fallback_baseline", False)
    state.setdefault("final_items", [])
    state.setdefault("final_total", 0)
    return state


def emit_event(deps: Deps, event: str, state: dict[str, Any]) -> None:
    """Fire the optional observability hook; sink errors never break a run."""
    sink = getattr(deps, "event_sink", None)
    if sink is None:
        return
    try:
        sink(event, state)
    except Exception:  # noqa: BLE001 - observability must not fail the run
        logger.debug("event sink failed for %s", event, exc_info=True)


def apply_difficulty_routing(state: dict[str, Any], deps: Deps) -> None:
    """After understand, route based on difficulty (adaptive budget included)."""
    job = state.get("job_profile")
    if not job:
        return
    route = difficulty_route(job.subjective_difficulty)
    state["difficulty_route"] = route

    new_budget = adaptive_budget(deps.budget_state.budget, route)
    deps.budget_state.budget = new_budget
    state["budget"] = new_budget

    if route == "single_pass":
        state["skip_reflection"] = True
        path = single_path_from_hard_constraints(state)
        if path is not None:
            state["paths"] = [path]
            state["tried_signatures"] = state.get("tried_signatures", set()) | {path.signature}
            state["phase"] = "execute"
        # if no hard_constraints to build a path, fall through to plan


def handle_stage_failure(state: dict[str, Any], phase: str, deps: Deps) -> None:
    """Map a stage exception to the degradation matrix (control plane never raises)."""
    action, reason = degrade(phase, "llm_failed")
    if reason != "understand_failed" or state.get("degraded_reason") is None:
        state["degraded_reason"] = reason

    if action == "fallback_baseline":
        state["fallback_baseline"] = True
        state["phase"] = "done"
    elif action == "single_path":
        path = single_path_from_hard_constraints(state)
        if path is not None:
            state["paths"] = [path]
            state["tried_signatures"] = state.get("tried_signatures", set()) | {path.signature}
            state["phase"] = "execute"
        else:
            state["fallback_baseline"] = True
            state["phase"] = "done"
    elif action == "skip_judge":
        state["phase"] = "evaluate"
    elif action == "skip_global_rerank":
        state["phase"] = "finalize"
    else:  # finalize_now
        if phase == "finalize":
            state["final_items"] = []
            state["final_total"] = 0
            state["phase"] = "done"
        else:
            state["phase"] = "finalize"
    emit_event(deps, f"degrade:{phase}", state)


def build_trace(
    state: dict[str, Any],
    deps: Deps,
    iterations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble ``agentTrace`` from the state, budget usage and iterations."""
    bs = deps.budget_state
    budget = state.get("budget")
    tool_calls = [
        {
            "pathId": pr.path_id,
            "elapsedMs": pr.tool_result.timings_ms.get("total", 0),
            "total": pr.tool_result.total,
            "quality": pr.quality,
        }
        for pr in state.get("path_results", [])
    ]
    return build_agent_trace(
        iterations=iterations,
        merged_pool_size=len(state.get("accumulated", {})),
        reranked_top_k=len(state.get("final_items", [])),
        rerank_used=bool(state.get("rerank_used", False)),
        budget=budget.model_dump() if budget else {},
        budget_used={
            "llmCalls": bs.llm_calls,
            "elapsedMs": int(bs.elapsed_sec() * 1000),
        },
        degraded_reason=state.get("degraded_reason"),
        tool_calls=tool_calls,
    )


def assemble_observability(
    state: dict[str, Any],
    deps: Deps,
    iterations: list[dict[str, Any]],
) -> None:
    """Attach metrics + trace to the final state (shared by all runners)."""
    metrics = collect_metrics(state, deps.budget_state)
    emit_metrics(metrics, query=state.get("query"))
    state["agent_metrics"] = metrics.to_dict()
    state["agent_trace"] = build_trace(state, deps, iterations)


class Harness:
    """Outer loop controller. Stateless between runs; deps injected once."""

    def __init__(self, deps: Deps) -> None:
        self.deps = deps

    # ── public ──────────────────────────────────────────────────────────

    def run(self, initial_state: dict[str, Any]) -> dict[str, Any]:
        state = init_state_defaults(initial_state)
        bs = self.deps.budget_state

        iterations: list[dict[str, Any]] = []
        iter_index = 0

        for _ in range(_MAX_STEPS):
            phase = state.get("phase", "understand")
            if phase == "done":
                break

            # budget guard: if exceeded, jump to finalize (once)
            if phase not in ("understand", "finalize") and (
                bs.time_exceeded() or bs.llm_exceeded()
            ):
                if state.get("degraded_reason") is None:
                    state["degraded_reason"] = "budget_exceeded"
                if phase != "finalize":
                    state["phase"] = "finalize"
                continue

            had_pending = phase == "execute" and any(
                p.status == "pending" for p in state.get("paths", [])
            )
            pr_count_before = len(state.get("path_results", []))

            try:
                updates = _STAGES[phase](state, self.deps)
                state.update(updates)
                emit_event(self.deps, phase, state)
            except Exception:  # noqa: BLE001 - degradation
                handle_stage_failure(state, phase, self.deps)
                continue

            # post-understand: difficulty routing
            if phase == "understand" and not state.get("need_clarify"):
                apply_difficulty_routing(state, self.deps)

            # post-execute: detect all-paths-failed
            if phase == "execute" and had_pending:
                new_results = len(state.get("path_results", [])) - pr_count_before
                if new_results == 0:
                    _, reason = degrade("execute", "all_paths_failed")
                    state["degraded_reason"] = reason
                    state["fallback_baseline"] = True
                    state["phase"] = "done"
                    break

            # post-evaluate: record iteration trace + qualified snapshot
            if phase == "evaluate":
                state.setdefault("qualified_per_round", []).append(
                    len(state.get("accumulated", {}))
                )
                # difficulty routing: skip reflection for single_pass
                if state.get("skip_reflection") and state.get("phase") == "reflect":
                    state["phase"] = "finalize"
                    state["finalize"] = True
                    state["finalize_reason"] = "difficulty_single_pass"
                reflections = state.get("reflections", [])
                iterations.append(
                    iteration_trace(
                        index=iter_index,
                        phase="execute",
                        paths=state.get("paths", []),
                        path_results=state.get("path_results", []),
                        reflection=reflections[-1] if reflections else None,
                        llm_calls=bs.llm_calls,
                        elapsed_ms=int(bs.elapsed_sec() * 1000),
                    )
                )
                iter_index += 1

        assemble_observability(state, self.deps, iterations)
        return state


def _profile_semantic(state: dict[str, Any]) -> list[SemanticConditionIR]:
    """Semantic conditions for the one-shot path: profile keywords + signals.

    A degraded / single-pass run still has the understanding stage's talent
    profile. Anchoring the path on ``where`` alone would rank every surviving
    candidate by doc id (score 0), which is empirically a lottery on any corpus
    larger than the page; the profile's keywords and soft signals are what makes
    the single path an honest one-shot search.
    """
    talent = state.get("talent_profile")
    if talent is None:
        return []
    conditions: list[SemanticConditionIR] = []
    seen: set[str] = set()
    for keyword in list(getattr(talent, "must_keywords", []) or [])[:3]:
        text = str(keyword).strip()
        if text and text not in seen:
            seen.add(text)
            conditions.append(
                SemanticConditionIR(text=text, fields=["work_vector", "project_vector"], weight=1.2)
            )
    for signal in getattr(talent, "soft_signals", []) or []:
        if signal.text and signal.text not in seen:
            seen.add(signal.text)
            conditions.append(signal)
    return conditions[:4]


def single_path_from_hard_constraints(
    state: dict[str, Any], *, loose_fallback: bool = False
) -> SearchPath | None:
    """Build the single-path fallback from a job profile's hard constraints.

    This is the harness's own one-shot path constructor: used when plan is
    degraded or difficulty routes to ``single_pass``, and reused by the offline
    evaluation (``search_agent.eval``) as its single-pass baseline path. The
    intent carries the talent profile's keywords / soft signals so the single
    path ranks by relevance instead of falling back to doc-id order.

    Returns ``None`` when there is nothing to anchor the ``where`` clause, unless
    ``loose_fallback`` is set: the evaluation needs a comparable baseline for every
    query, including ones like "找后端工程师，最好来自大厂" that carry no hard
    constraint at all -- there the path anchors on ``work_years >= 0``.
    """
    job = state.get("job_profile")
    constraints = list(job.hard_constraints) if job else []
    if not constraints:
        if not loose_fallback:
            return None
        constraints = [
            StructuredConditionIR(
                field="work_years", operator="gte", values=[0], required=True, weight=1.0
            )
        ]
    leaves = [WhereNode(type="leaf", condition=c) for c in constraints]
    where = leaves[0] if len(leaves) == 1 else WhereNode(type="and", children=leaves)
    intent = CandidateSearchIR(where=where, semantic=_profile_semantic(state))
    sig = intent_signature(intent)
    return SearchPath(
        id="p1",
        rationale=(
            "fallback single path (plan degraded)"
            if job and job.hard_constraints
            else "loose single path (no hard constraint in query)"
        ),
        intent=intent,
        signature=sig,
        status="pending",
    )


__all__ = [
    "Harness",
    "apply_difficulty_routing",
    "assemble_observability",
    "build_trace",
    "emit_event",
    "handle_stage_failure",
    "init_state_defaults",
    "single_path_from_hard_constraints",
]
