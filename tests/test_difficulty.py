"""Tests for difficulty routing + adaptive budget (Phase 3)."""

from __future__ import annotations

from search_agent.contract.tool import (
    SearchToolResult,
    ToolCandidate,
)
from search_agent.core.budget import BudgetState
from search_agent.core.difficulty import (
    adaptive_budget,
    difficulty_route,
)
from search_agent.harness import Harness
from search_agent.llm.client import FakeLlmClient
from search_agent.models.api import CandidateItem
from search_agent.models.artifacts import Budget
from search_agent.models.ir import (
    CandidateSearchIR,
    StructuredConditionIR,
    WhereNode,
)
from search_agent.stages.base import Deps


def _ir():
    return CandidateSearchIR(
        where=WhereNode(
            type="leaf",
            condition=StructuredConditionIR(field="skill", operator="any", values=["Python"]),
        )
    )


def _cand(doc_id, score=0.8):
    return ToolCandidate(
        doc_id=doc_id,
        score=score,
        item=CandidateItem(docId=doc_id, score=score),
        hard_matches=[{"field": "skill", "matched": True}],
    )


class _FakeTool:
    def search_with_intent(self, req):
        return SearchToolResult(items=[_cand(f"d{i}") for i in range(5)], total=5, quality="rich")

    def rerank_pool(self, req):
        return SearchToolResult(
            items=req.candidates[: req.top_k], total=len(req.candidates), quality="rich"
        )


def _budget():
    return Budget(
        max_iterations=3,
        max_paths_per_iteration=4,
        target_qualified=20,
        time_budget_sec=12.0,
        max_llm_calls=40,
    )


def _deps(llm, tool):
    return Deps(llm=llm, tool=tool, budget_state=BudgetState(budget=_budget()))


def _state():
    return {
        "query": "rare niche role",
        "company_id": 39978,
        "hr_id": 1,
        "permission": None,
        "routing": 39978,
        "top_k": 20,
        "recall_pool_size": None,
        "details": True,
        "budget": _budget(),
    }


def _understand_resp(difficulty="high"):
    return {
        "title": "X",
        "must_abilities": [{"name": "Python", "level": "must"}],
        "needs_clarification": False,
        "subjective_difficulty": difficulty,
        "difficulty_reason": "test",
        "hard_constraints": [{"field": "skill", "operator": "any", "values": ["Python"]}],
        "talent_profile": {"must_keywords": ["Python"]},
    }


# ── unit tests ──


def test_difficulty_route_mapping():
    assert difficulty_route("high") == "single_pass"
    assert difficulty_route("low") == "reduced"
    assert difficulty_route("mid") == "standard"


def test_adaptive_budget_single_pass():
    base = _budget()
    b = adaptive_budget(base, "single_pass")
    assert b.max_iterations == 0
    assert b.max_paths_per_iteration == 1


def test_adaptive_budget_reduced():
    base = _budget()
    b = adaptive_budget(base, "reduced")
    assert b.max_iterations == 1
    assert b.max_paths_per_iteration == 2


def test_adaptive_budget_standard_unchanged():
    base = _budget()
    b = adaptive_budget(base, "standard")
    assert b.max_iterations == 3
    assert b.max_paths_per_iteration == 4


# ── harness integration ──


def test_high_difficulty_skips_plan_and_reflection():
    """high → single_pass: no plan call, no reflect call, single path."""
    tool = _FakeTool()
    llm = FakeLlmClient(
        responses={
            "agent_understand": _understand_resp("high"),
            "agent_judge": {
                "judgments": [
                    {
                        "doc_id": f"d{i}",
                        "matched_abilities": ["Python"],
                        "missing_abilities": [],
                        "summary": "ok",
                    }
                    for i in range(5)
                ]
            },
        }
    )
    h = Harness(_deps(llm, tool))
    state = h.run(_state())

    assert state["phase"] == "done"
    assert state["difficulty_route"] == "single_pass"
    assert state.get("skip_reflection") is True
    # plan was NOT called
    plan_calls = [c for c in llm.calls if c[0] == "agent_plan"]
    assert len(plan_calls) == 0
    # reflect was NOT called
    reflect_calls = [c for c in llm.calls if c[0] == "agent_reflect"]
    assert len(reflect_calls) == 0
    # single path was created
    assert len(state.get("paths", [])) == 1
    # budget was adapted
    assert state["budget"].max_iterations == 0
    assert state["budget"].max_paths_per_iteration == 1


def test_low_difficulty_reduces_budget():
    """low → reduced: plan still called, but budget shrunk."""
    tool = _FakeTool()
    llm = FakeLlmClient(
        responses={
            "agent_understand": _understand_resp("low"),
            "agent_plan": {
                "paths": [
                    {"rationale": "p", "intent": _ir().model_dump(), "expected_yield": "high"}
                ]
            },
            "agent_judge": {
                "judgments": [
                    {
                        "doc_id": f"d{i}",
                        "matched_abilities": ["Python"],
                        "missing_abilities": [],
                        "summary": "ok",
                    }
                    for i in range(5)
                ]
            },
        }
    )
    h = Harness(_deps(llm, tool))
    state = h.run(_state())

    assert state["difficulty_route"] == "reduced"
    assert state["budget"].max_iterations == 1
    assert state["budget"].max_paths_per_iteration == 2
    # plan WAS called (reduced still plans)
    plan_calls = [c for c in llm.calls if c[0] == "agent_plan"]
    assert len(plan_calls) == 1


def test_mid_difficulty_standard():
    """mid → standard: normal flow, budget unchanged."""
    tool = _FakeTool()
    llm = FakeLlmClient(
        responses={
            "agent_understand": _understand_resp("mid"),
            "agent_plan": {
                "paths": [
                    {"rationale": "p", "intent": _ir().model_dump(), "expected_yield": "high"}
                ]
            },
            "agent_judge": {
                "judgments": [
                    {
                        "doc_id": f"d{i}",
                        "matched_abilities": ["Python"],
                        "missing_abilities": [],
                        "summary": "ok",
                    }
                    for i in range(5)
                ]
            },
        }
    )
    h = Harness(_deps(llm, tool))
    state = h.run(_state())

    assert state["difficulty_route"] == "standard"
    assert state["budget"].max_iterations == 3
    assert state["budget"].max_paths_per_iteration == 4


def test_high_difficulty_no_hard_constraints_falls_to_plan():
    """high but no hard_constraints → can't build fallback path → falls to plan."""
    tool = _FakeTool()
    resp = _understand_resp("high")
    resp["hard_constraints"] = []
    llm = FakeLlmClient(
        responses={
            "agent_understand": resp,
            "agent_plan": {
                "paths": [
                    {"rationale": "p", "intent": _ir().model_dump(), "expected_yield": "high"}
                ]
            },
            "agent_judge": {
                "judgments": [
                    {
                        "doc_id": f"d{i}",
                        "matched_abilities": ["Python"],
                        "missing_abilities": [],
                        "summary": "ok",
                    }
                    for i in range(5)
                ]
            },
        }
    )
    h = Harness(_deps(llm, tool))
    state = h.run(_state())

    # still single_pass route (skip_reflection), but plan was called
    assert state["difficulty_route"] == "single_pass"
    assert state.get("skip_reflection") is True
    plan_calls = [c for c in llm.calls if c[0] == "agent_plan"]
    assert len(plan_calls) == 1
