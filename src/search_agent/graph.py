"""LangGraphRunner: the same eight stages, compiled into a ``StateGraph``.

Optional extra: ``pip install -e ".[langgraph]"``. The graph mirrors
``Harness.run`` one-to-one -- same stage functions, same budget guard, same
degradation matrix, same difficulty routing -- and is verified equivalent to
the default ``LoopRunner`` by ``tests/test_langgraph_runner.py``.

``LangGraphRunner`` is a drop-in :class:`~search_agent.runner.HarnessRunner`::

    from search_agent.graph import LangGraphRunner

    service = SearchAgentService(tool=..., llm_client=..., runner=LangGraphRunner())

Notes
-----
- Node functions receive the full state and return the full state (LangGraph
  ``dict`` schema writes whole-value updates; stages still return deltas,
  which the node wrapper merges like ``Harness.run`` does).
- ``build_graph`` is called once per run: the compiled graph closes over
  ``deps`` and the per-run iteration trace.
"""

from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, START, StateGraph

from search_agent.core.cancellation import (
    SearchCancelled,
    is_cancelled,
    mark_cancelled,
    raise_if_cancelled,
)
from search_agent.core.degrade import degrade
from search_agent.core.round_metrics import record_round_summary
from search_agent.core.trace import iteration_trace
from search_agent.harness import (
    apply_difficulty_routing,
    assemble_observability,
    emit_event,
    handle_stage_failure,
    init_state_defaults,
)
from search_agent.stages.base import Deps
from search_agent.stages.evaluate import run_evaluate
from search_agent.stages.execute import run_execute
from search_agent.stages.finalize import run_finalize
from search_agent.stages.judge import run_judge
from search_agent.stages.plan import run_plan
from search_agent.stages.reflect import run_reflect
from search_agent.stages.revise import run_revise
from search_agent.stages.understand import run_understand

_RECURSION_LIMIT = 120
# Budget guard skips these phases, mirroring Harness.run.
_UNGUARDED_PHASES = {"understand", "finalize"}


def build_graph(deps: Deps) -> Any:
    """Compile the eight stages into a runnable StateGraph (state = dict)."""
    iterations: list[dict[str, Any]] = []

    def stage_node(
        phase: str, fn: Callable[[dict[str, Any], Deps], dict[str, Any]]
    ) -> Callable[[dict[str, Any]], dict[str, Any]]:
        def node(state: dict[str, Any]) -> dict[str, Any]:
            state = dict(state)
            bs = deps.budget_state

            try:
                raise_if_cancelled(deps.cancel_event, stage=phase)
            except SearchCancelled as exc:
                mark_cancelled(state, stage=exc.stage)
                return state

            if phase not in _UNGUARDED_PHASES and (bs.time_exceeded() or bs.llm_exceeded()):
                if state.get("degraded_reason") is None:
                    state["degraded_reason"] = "budget_exceeded"
                if state.get("phase") != "finalize":
                    state["phase"] = "finalize"
                return state

            had_pending = phase == "execute" and any(
                p.status == "pending" for p in state.get("paths", [])
            )
            pr_count_before = len(state.get("path_results", []))
            if phase == "finalize":
                state["finalize_start_llm_calls"] = bs.llm_calls
                state["finalize_start_elapsed_ms"] = int(bs.elapsed_sec() * 1000)

            try:
                state.update(fn(state, deps))
                emit_event(deps, phase, state)
            except SearchCancelled as exc:
                mark_cancelled(state, stage=exc.stage)
                return state
            except Exception:  # noqa: BLE001 - degradation matrix handles it
                handle_stage_failure(state, phase, deps)
                return state

            if is_cancelled(deps.cancel_event):
                mark_cancelled(state, stage=phase)
                return state

            if phase == "understand" and not state.get("need_clarify"):
                apply_difficulty_routing(state, deps)

            if phase == "execute" and had_pending:
                new_results = len(state.get("path_results", [])) - pr_count_before
                if new_results == 0:
                    _, reason = degrade("execute", "all_paths_failed")
                    state["degraded_reason"] = reason
                    state["fallback_baseline"] = True
                    state["phase"] = "done"

            if phase == "evaluate":
                if state.get("skip_reflection") and state.get("phase") == "reflect":
                    state["phase"] = "finalize"
                    state["finalize"] = True
                    state["finalize_reason"] = "difficulty_single_pass"
                record_round_summary(state, bs)
                state.setdefault("qualified_per_round", []).append(
                    len(state.get("accumulated", {}))
                )
                reflections = state.get("reflections", [])
                iterations.append(
                    iteration_trace(
                        index=len(iterations),
                        phase="execute",
                        paths=state.get("paths", []),
                        path_results=state.get("path_results", []),
                        reflection=reflections[-1] if reflections else None,
                        llm_calls=bs.llm_calls,
                        elapsed_ms=int(bs.elapsed_sec() * 1000),
                    )
                )
            return state

        return node

    def finish_node(state: dict[str, Any]) -> dict[str, Any]:
        state = dict(state)
        assemble_observability(state, deps, iterations)
        return state

    def route_after_understand(state: dict[str, Any]) -> str:
        if state.get("cancelled"):
            return "finish"
        phase = state.get("phase")
        if state.get("need_clarify") or phase == "done":
            return "finish"
        if phase == "execute":
            return "execute"
        if phase == "finalize":
            return "finalize"
        return "plan"

    def route_after_plan(state: dict[str, Any]) -> str:
        if state.get("cancelled"):
            return "finish"
        phase = state.get("phase")
        if phase == "done":
            return "finish"
        if phase == "finalize":
            return "finalize"
        return "execute"

    def route_after_execute(state: dict[str, Any]) -> str:
        if state.get("cancelled"):
            return "finish"
        phase = state.get("phase")
        if phase == "done":
            return "finish"
        if phase == "finalize":
            return "finalize"
        return "judge"

    def route_after_judge(state: dict[str, Any]) -> str:
        if state.get("cancelled"):
            return "finish"
        return "finalize" if state.get("phase") == "finalize" else "evaluate"

    def route_after_evaluate(state: dict[str, Any]) -> str:
        if state.get("cancelled"):
            return "finish"
        return "reflect" if state.get("phase") == "reflect" else "finalize"

    def route_after_reflect(state: dict[str, Any]) -> str:
        if state.get("cancelled"):
            return "finish"
        return "finalize" if state.get("phase") == "finalize" else "revise"

    def route_after_revise(state: dict[str, Any]) -> str:
        if state.get("cancelled"):
            return "finish"
        phase = state.get("phase")
        if phase == "done":
            return "finish"
        if phase == "finalize":
            return "finalize"
        return "execute"

    graph = StateGraph(dict)
    graph.add_node("understand", stage_node("understand", run_understand))
    graph.add_node("plan", stage_node("plan", run_plan))
    graph.add_node("execute", stage_node("execute", run_execute))
    graph.add_node("judge", stage_node("judge", run_judge))
    graph.add_node("evaluate", stage_node("evaluate", run_evaluate))
    graph.add_node("reflect", stage_node("reflect", run_reflect))
    graph.add_node("revise", stage_node("revise", run_revise))
    graph.add_node("finalize", stage_node("finalize", run_finalize))
    graph.add_node("finish", finish_node)

    graph.add_edge(START, "understand")
    graph.add_conditional_edges("understand", route_after_understand)
    graph.add_conditional_edges("plan", route_after_plan)
    graph.add_conditional_edges("execute", route_after_execute)
    graph.add_conditional_edges("judge", route_after_judge)
    graph.add_conditional_edges("evaluate", route_after_evaluate)
    graph.add_conditional_edges("reflect", route_after_reflect)
    graph.add_conditional_edges("revise", route_after_revise)
    graph.add_edge("finalize", "finish")
    graph.add_edge("finish", END)
    return graph.compile()


class LangGraphRunner:
    """``HarnessRunner`` implementation backed by a compiled StateGraph."""

    def run(self, initial_state: dict[str, Any], deps: Deps) -> dict[str, Any]:
        app = build_graph(deps)
        state = init_state_defaults(initial_state)
        return app.invoke(state, config={"recursion_limit": _RECURSION_LIMIT})


__all__ = ["LangGraphRunner", "build_graph"]
