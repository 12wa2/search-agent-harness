"""Tests for per-round strategy effect accounting."""

from __future__ import annotations

from search_agent.contract.tool import SearchToolResult, ToolCandidate
from search_agent.core.budget import BudgetState
from search_agent.core.metrics import collect_metrics
from search_agent.core.round_metrics import (
    build_final_search_summary,
    record_round_summary,
)
from search_agent.models.api import CandidateItem
from search_agent.models.artifacts import (
    Budget,
    CandidateJudgment,
    CandidateSource,
    JobAbility,
    JobProfile,
    PathAction,
    PathResult,
    Reflection,
    TalentProfile,
)
from search_agent.models.ir import SemanticConditionIR


def _budget() -> Budget:
    return Budget(max_iterations=3, max_llm_calls=40, target_qualified=20)


def _candidate(doc_id: str) -> ToolCandidate:
    return ToolCandidate(
        doc_id=doc_id,
        score=0.9,
        item=CandidateItem(docId=doc_id, score=0.9),
    )


def _path_result(path_id: str, doc_ids: list[str], total: int | None = None) -> PathResult:
    return PathResult(
        path_id=path_id,
        tool_result=SearchToolResult(
            items=[_candidate(doc_id) for doc_id in doc_ids],
            total=total if total is not None else len(doc_ids),
            quality="rich",
        ),
        quality="rich",
    )


def _job() -> JobProfile:
    return JobProfile(
        raw_query="backend engineer",
        title="Backend Engineer",
        must_abilities=[JobAbility(name="Python"), JobAbility(name="Distributed")],
    )


def _judgment(doc_id: str, matched: list[str]) -> CandidateJudgment:
    return CandidateJudgment(doc_id=doc_id, score=0.8, matched_abilities=matched)


def test_round_summary_dedupes_docs_and_counts_must_qualified() -> None:
    state = {
        "phase": "reflect",
        "path_results": [_path_result("p1", ["d1", "d2", "d2"], total=3)],
        "accumulated": {
            "d1": _judgment("d1", ["Python", "Distributed"]),
            "d2": _judgment("d2", ["Python"]),
        },
        "job_profile": _job(),
    }

    summary = record_round_summary(
        state,
        BudgetState(budget=_budget()),
        llm_calls=5,
        elapsed_ms=1200,
    )

    assert summary["round"] == 1
    assert summary["pathCount"] == 1
    assert summary["returned"] == 3
    assert summary["uniqueReturned"] == 2
    assert summary["newUnique"] == 2
    assert summary["newJudged"] == 2
    assert summary["judgedTotal"] == 2
    assert summary["newMustQualified"] == 1
    assert summary["mustQualifiedTotal"] == 1
    assert summary["newPathCount"] == 1
    assert summary["newSoftSignals"] == 0
    assert summary["newSourceHints"] == 0
    assert summary["llmCallsDelta"] == 5
    assert summary["elapsedMsDelta"] == 1200
    assert summary["next"] == "reflect"
    assert summary["stopReason"] is None
    assert state["round_path_cursor"] == 1
    assert state["round_judged_doc_ids"] == {"d1", "d2"}


def test_second_round_reports_only_new_docs_and_strategy_context() -> None:
    state = {
        "phase": "reflect",
        "path_results": [_path_result("p1", ["d1", "d2"])],
        "accumulated": {
            "d1": _judgment("d1", ["Python", "Distributed"]),
            "d2": _judgment("d2", ["Python"]),
        },
        "job_profile": _job(),
    }
    first = record_round_summary(
        state,
        BudgetState(budget=_budget()),
        llm_calls=5,
        elapsed_ms=1000,
    )
    assert first["round"] == 1

    state["phase"] = "finalize"
    state["finalize_reason"] = "target_reached"
    state["reflections"] = [
        Reflection(
            gaps=["missing payment evidence"],
            path_actions=[PathAction(kind="add_path", rationale="search payment teams")],
        )
    ]
    state["talent_profile"] = TalentProfile(
        source_hypotheses=[CandidateSource(id="s1", kind="industry", value="payments")],
        soft_signals=[
            SemanticConditionIR(text="payment", fields=["project_vector"]),
            SemanticConditionIR(text="trading", fields=["project_vector"]),
        ],
    )
    state["path_results"].append(_path_result("p2", ["d2", "d3"], total=2))
    state["accumulated"]["d3"] = _judgment("d3", ["Python", "Distributed"])

    second = record_round_summary(
        state,
        BudgetState(budget=_budget()),
        llm_calls=12,
        elapsed_ms=5000,
    )

    assert second["round"] == 2
    assert second["uniqueReturned"] == 2
    assert second["newUnique"] == 1
    assert second["newJudged"] == 1
    assert second["newMustQualified"] == 1
    assert second["newPathCount"] == 1
    assert second["newSoftSignals"] == 2
    assert second["newSourceHints"] == 1
    assert second["gaps"] == ["missing payment evidence"]
    assert second["actions"][0]["kind"] == "add_path"
    assert second["llmCallsDelta"] == 7
    assert second["elapsedMsDelta"] == 4000
    assert second["next"] == "finalize"
    assert second["stopReason"] == "target_reached"


def test_final_search_uses_finalize_start_cost_boundary() -> None:
    state = {
        "phase": "done",
        "finalize_reason": "target_reached",
        "job_profile": _job(),
        "accumulated": {
            "d1": _judgment("d1", ["Python", "Distributed"]),
            "d2": _judgment("d2", ["Python"]),
            "d3": _judgment("d3", ["Python", "Distributed"]),
            "d4": _judgment("d4", ["Python"]),
        },
        "round_judged_doc_ids": {"d1", "d2"},
        "final_search_total": 8,
        "final_search_new": 2,
        "final_judged": 2,
        "final_search_semantic": ["payment"],
        "finalize_start_llm_calls": 10,
        "finalize_start_elapsed_ms": 3000,
    }

    summary = build_final_search_summary(
        state,
        BudgetState(budget=_budget()),
        llm_calls=12,
        elapsed_ms=4500,
    )

    assert summary is not None
    assert summary["kind"] == "final"
    assert summary["returned"] == 8
    assert summary["newUnique"] == 2
    assert summary["newJudged"] == 2
    assert summary["newMustQualified"] == 1
    assert summary["mustQualifiedTotal"] == 2
    assert summary["llmCallsDelta"] == 2
    assert summary["elapsedMsDelta"] == 1500
    assert summary["stopReason"] == "target_reached"
    assert state["final_search_summary"] == summary


def test_metrics_separate_cumulative_and_per_round_judged_counts() -> None:
    state = {
        "accumulated": {
            "d1": _judgment("d1", ["Python"]),
            "d2": _judgment("d2", ["Python"]),
            "d3": _judgment("d3", ["Python"]),
        },
        "budget": _budget(),
        "path_results": [],
        "job_profile": None,
        "talent_profile": None,
        "paths": [],
        "iteration": 2,
        "qualified_per_round": [5, 12, 15],
    }

    metrics = collect_metrics(state, BudgetState(budget=_budget()))

    assert metrics.qualified_total_per_round == [5, 12, 15]
    assert metrics.new_qualified_per_round == [5, 7, 3]
    assert metrics.reflection_gain == [7, 3]
    payload = metrics.to_dict()
    assert payload["rounds"]["qualifiedTotalPerRound"] == [5, 12, 15]
    assert payload["rounds"]["newQualifiedPerRound"] == [5, 7, 3]
