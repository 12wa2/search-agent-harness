"""Termination semantics for the revise stage."""

from __future__ import annotations

from search_agent.core.budget import BudgetState
from search_agent.models.artifacts import (
    Budget,
    JobProfile,
    PathAction,
    Reflection,
    SearchPath,
)
from search_agent.models.ir import (
    CandidateSearchIR,
    StructuredConditionIR,
    WhereNode,
)
from search_agent.stages.base import Deps
from search_agent.stages.revise import run_revise


class _UnusedDependency:
    pass


def _deps() -> Deps:
    return Deps(
        llm=_UnusedDependency(),  # type: ignore[arg-type]
        tool=_UnusedDependency(),  # type: ignore[arg-type]
        budget_state=BudgetState(budget=Budget()),
    )


def _intent(value: str = "Python") -> CandidateSearchIR:
    return CandidateSearchIR(
        where=WhereNode(
            type="leaf",
            condition=StructuredConditionIR(
                field="skill",
                operator="any",
                values=[value],
            ),
        )
    )


def _pending_path(identifier: str = "p1") -> SearchPath:
    return SearchPath(id=identifier, rationale="seed", intent=_intent())


def test_revise_without_reflections_reports_reflection_empty() -> None:
    updates = run_revise(
        {"reflections": [], "paths": [], "tried_signatures": set(), "iteration": 0},
        _deps(),
    )

    assert updates["phase"] == "finalize"
    assert updates["finalize_reason"] == "reflection_empty"


def test_revise_without_actions_reports_no_path_actions() -> None:
    updates = run_revise(
        {
            "reflections": [Reflection(gaps=["Kafka"])],
            "paths": [_pending_path()],
            "tried_signatures": set(),
            "iteration": 0,
        },
        _deps(),
    )

    assert updates["phase"] == "finalize"
    assert updates["finalize_reason"] == "no_path_actions"


def test_revise_dropping_last_pending_path_reports_paths_exhausted() -> None:
    updates = run_revise(
        {
            "reflections": [
                Reflection(path_actions=[PathAction(kind="drop", target_path_id="p1")])
            ],
            "paths": [_pending_path()],
            "tried_signatures": set(),
            "iteration": 0,
        },
        _deps(),
    )

    assert updates["phase"] == "finalize"
    assert updates["finalize_reason"] == "paths_exhausted"


def test_revise_clarification_has_an_explicit_reason() -> None:
    updates = run_revise(
        {
            "reflections": [Reflection(path_actions=[PathAction(kind="clarify")])],
            "paths": [_pending_path()],
            "tried_signatures": set(),
            "iteration": 0,
        },
        _deps(),
    )

    assert updates["phase"] == "finalize"
    assert updates["finalize_reason"] == "clarification_requested"


def test_revise_add_path_continues_with_a_new_pending_path() -> None:
    updates = run_revise(
        {
            "reflections": [
                Reflection(
                    path_actions=[
                        PathAction(
                            kind="add_path",
                            rationale="cover Kafka",
                            intent=_intent("Kafka"),
                        )
                    ]
                )
            ],
            "paths": [_pending_path()],
            "tried_signatures": set(),
            "iteration": 0,
        },
        _deps(),
    )

    assert updates["phase"] == "execute"
    assert updates["iteration"] == 1
    assert any(path.status == "pending" for path in updates["paths"])


def test_revise_preserves_required_education_scope_in_new_path() -> None:
    job = JobProfile(
        raw_query="本科211",
        hard_constraints=[
            StructuredConditionIR(field="degree", operator="eq", values=["本科"]),
            StructuredConditionIR(field="school_tier", operator="eq", values=["211"]),
        ],
    )
    updates = run_revise(
        {
            "reflections": [
                Reflection(
                    path_actions=[
                        PathAction(
                            kind="add_path",
                            rationale="cover Kafka",
                            intent=_intent("Kafka"),
                        )
                    ]
                )
            ],
            "job_profile": job,
            "paths": [_pending_path()],
            "tried_signatures": set(),
            "iteration": 0,
        },
        _deps(),
    )

    new_path = next(path for path in updates["paths"] if path.origin == "add_path")

    def fields(node):
        if node.type == "leaf":
            return {node.condition.field} if node.condition else set()
        if node.type == "not":
            return fields(node.child) if node.child else set()
        result = set()
        for child in node.children:
            result |= fields(child)
        return result

    assert {"skill", "degree", "school_tier"} <= fields(new_path.intent.where)
