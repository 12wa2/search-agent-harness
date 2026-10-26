"""Tests for AgentMetrics collection + finalize output validation."""

from __future__ import annotations

from search_agent.contract.tool import (
    SearchToolResult,
    ToolCandidate,
)
from search_agent.core.budget import BudgetState
from search_agent.core.metrics import (
    AgentMetrics,
    collect_metrics,
)
from search_agent.harness import Harness
from search_agent.llm.client import FakeLlmClient
from search_agent.models.api import CandidateItem
from search_agent.models.artifacts import (
    Budget,
    CandidateJudgment,
    CandidateSource,
    JobAbility,
    JobProfile,
    TalentProfile,
)
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


def _llm():
    return FakeLlmClient(
        responses={
            "agent_understand": {
                "title": "Python engineer",
                "must_abilities": [{"name": "Python", "level": "must"}],
                "nice_abilities": [{"name": "Django", "level": "nice"}],
                "needs_clarification": False,
                "talent_profile": {
                    "source_hypotheses": [{"id": "s1", "kind": "company", "value": "Bat"}],
                    "must_keywords": ["Python"],
                },
            },
            "agent_plan": {
                "paths": [
                    {
                        "rationale": "p",
                        "intent": _ir().model_dump(),
                        "expected_yield": "high",
                        "source_refs": ["s1"],
                    }
                ]
            },
            "agent_judge": {
                "judgments": [
                    {
                        "doc_id": f"d{i}",
                        "matched_abilities": ["Python"],
                        "missing_abilities": ["Django"],
                        "summary": "ok",
                    }
                    for i in range(5)
                ]
            },
        }
    )


def _budget(top_k=20, target=20):
    return Budget(
        max_iterations=3,
        max_paths_per_iteration=4,
        target_qualified=target,
        time_budget_sec=12.0,
        max_llm_calls=40,
    )


def _deps(llm, tool, target=20):
    return Deps(llm=llm, tool=tool, budget_state=BudgetState(budget=_budget(target=target)))


def _state(top_k=20):
    return {
        "query": "Python engineer",
        "company_id": 39978,
        "hr_id": 1,
        "permission": None,
        "routing": 39978,
        "top_k": top_k,
        "recall_pool_size": None,
        "details": True,
        "budget": _budget(),
    }


# ── collect_metrics unit tests ──


def test_metrics_recall_rate():
    state = {
        "accumulated": {f"d{i}": CandidateJudgment(doc_id=f"d{i}", score=0.8) for i in range(10)},
        "budget": _budget(target=20),
        "path_results": [],
        "job_profile": None,
        "talent_profile": None,
        "paths": [],
        "iteration": 0,
        "qualified_per_round": [10],
    }
    bs = BudgetState(budget=_budget(target=20))
    m = collect_metrics(state, bs)
    assert m.judged_count == 10
    assert m.target_qualified == 20
    assert m.recall_rate == 0.5


def test_metrics_quality_distribution():
    from search_agent.contract.tool import SearchToolResult
    from search_agent.models.artifacts import PathResult

    state = {
        "accumulated": {},
        "budget": _budget(),
        "path_results": [
            PathResult(
                path_id="p1", tool_result=SearchToolResult(items=[], total=0), quality="rich"
            ),
            PathResult(
                path_id="p2", tool_result=SearchToolResult(items=[], total=0), quality="thin"
            ),
            PathResult(
                path_id="p3", tool_result=SearchToolResult(items=[], total=0), quality="rich"
            ),
        ],
        "job_profile": None,
        "talent_profile": None,
        "paths": [],
        "iteration": 0,
        "qualified_per_round": [],
    }
    bs = BudgetState(budget=_budget())
    m = collect_metrics(state, bs)
    assert m.quality_distribution == {"rich": 2, "thin": 1}


def test_metrics_match_quality():
    job = JobProfile(
        raw_query="test",
        must_abilities=[JobAbility(name="Python")],
        nice_abilities=[JobAbility(name="Django", level="nice")],
    )
    state = {
        "accumulated": {
            "d1": CandidateJudgment(
                doc_id="d1",
                score=0.8,
                matched_abilities=["Python"],
                missing_abilities=["Django"],
                hard_matches=[
                    {"field": "skill", "matched": True},
                    {"field": "city", "matched": False},
                ],
            ),
            "d2": CandidateJudgment(
                doc_id="d2",
                score=0.7,
                matched_abilities=["Python", "Django"],
                missing_abilities=[],
                hard_matches=[{"field": "skill", "matched": True}],
            ),
        },
        "budget": _budget(),
        "path_results": [],
        "job_profile": job,
        "talent_profile": None,
        "paths": [],
        "iteration": 0,
        "qualified_per_round": [],
    }
    bs = BudgetState(budget=_budget())
    m = collect_metrics(state, bs)
    # 2 abilities total; d1 matched 1/2=0.5, d2 matched 2/2=1.0; avg=0.75
    assert m.matched_ability_coverage == 0.75
    # d1: 1/2=0.5, d2: 1/1=1.0; avg=0.75
    assert m.hard_match_hit_rate == 0.75


def test_metrics_reflection_gain():
    state = {
        "accumulated": {},
        "budget": _budget(),
        "path_results": [],
        "job_profile": None,
        "talent_profile": None,
        "paths": [],
        "iteration": 2,
        "qualified_per_round": [5, 12, 15],  # round0=5, round1=+7, round2=+3
    }
    bs = BudgetState(budget=_budget())
    m = collect_metrics(state, bs)
    assert m.reflection_gain == [7, 3]


def test_metrics_source_conversion():
    from search_agent.models.artifacts import SearchPath

    talent = TalentProfile(
        source_hypotheses=[CandidateSource(id="s1", kind="company", value="Bat")],
    )
    paths = [SearchPath(id="p1", rationale="r", intent=_ir(), source_refs=["s1"])]
    state = {
        "accumulated": {
            "d1": CandidateJudgment(doc_id="d1", score=0.8, source_path_ids=["p1"]),
            "d2": CandidateJudgment(doc_id="d2", score=0.7, source_path_ids=["p1"]),
            "d3": CandidateJudgment(doc_id="d3", score=0.6, source_path_ids=[]),
        },
        "budget": _budget(),
        "path_results": [],
        "job_profile": None,
        "talent_profile": talent,
        "paths": paths,
        "iteration": 0,
        "qualified_per_round": [],
    }
    bs = BudgetState(budget=_budget())
    m = collect_metrics(state, bs)
    assert m.source_conversion == {"s1": 2}


def test_metrics_to_dict_structure():
    m = AgentMetrics(recall_rate=0.5, judged_count=10, target_qualified=20)
    d = m.to_dict()
    assert "recall" in d
    assert "rounds" in d
    assert "match" in d
    assert "reasoning" in d
    assert "reflection" in d
    assert "meta" in d
    assert d["recall"]["rate"] == 0.5
    assert d["recall"]["surfacedDocIds"] == []
    assert d["meta"]["totalTokens"] == 0


def test_surfaced_ids_include_judged_converged_candidates():
    state = {
        "accumulated": {
            "final-only": CandidateJudgment(doc_id="final-only", score=0.8),
        },
        "budget": _budget(),
        "path_results": [],
        "job_profile": None,
        "talent_profile": None,
        "paths": [],
        "iteration": 0,
        "qualified_per_round": [],
        "final_items": [],
    }
    metrics = collect_metrics(state, BudgetState(budget=_budget()))
    assert metrics.surfaced_doc_ids == ["final-only"]


# ── harness integration: metrics in state ──


def test_harness_produces_metrics():
    tool = _FakeTool()
    llm = _llm()
    h = Harness(_deps(llm, tool))
    state = h.run(_state())

    assert "agent_metrics" in state
    m = state["agent_metrics"]
    assert m["recall"]["judged"] == 5
    assert m["recall"]["target"] == 20
    assert m["rounds"]["iterations"] >= 0  # may reflect if qualified < target
    assert m["meta"]["rerankUsed"] is True
    assert m["meta"]["degradedReason"] is None


def test_harness_qualified_per_round_tracked():
    tool = _FakeTool()
    llm = _llm()
    h = Harness(_deps(llm, tool))
    state = h.run(_state())

    assert state.get("qualified_per_round") == [5]  # one evaluate, 5 accumulated


# ── finalize validation ──


def test_finalize_validation_issues_empty_result():
    """No final items, no clarification, no fallback -> validation issue."""
    from search_agent.stages.finalize import _validate_output

    state = {"need_clarify": False, "fallback_baseline": False, "accumulated": {}}
    issues = _validate_output([], state)
    assert "empty_result_no_clarification" in issues


def test_finalize_validation_ok_with_items():
    from search_agent.stages.finalize import _validate_output

    state = {"need_clarify": False, "fallback_baseline": False, "accumulated": {"d1": {}}}
    items = [_cand("d1", 0.8)]
    issues = _validate_output(items, state)
    assert issues == []


def test_finalize_validation_negative_score():
    from search_agent.stages.finalize import _validate_output

    state = {"need_clarify": False, "fallback_baseline": False, "accumulated": {"d1": {}}}
    items = [_cand("d1", -0.5)]
    issues = _validate_output(items, state)
    assert any("negative_score" in i for i in issues)


def test_finalize_validation_hard_violation():
    from search_agent.stages.finalize import _validate_output

    state = {"need_clarify": False, "fallback_baseline": False, "accumulated": {"d1": {}}}
    tc = ToolCandidate(
        doc_id="d1",
        score=0.8,
        item=CandidateItem(docId="d1", score=0.8),
        match_explanation={
            "hardMatches": [
                {
                    "field": "city",
                    "matched": False,
                    "actualValues": ["北京"],
                    "required": True,
                }
            ]
        },
    )
    issues = _validate_output([tc], state)
    assert any("hard_violation" in i for i in issues)


def test_finalize_validation_missing_hard_evidence_is_not_a_violation():
    from search_agent.stages.finalize import _validate_output

    state = {"need_clarify": False, "fallback_baseline": False, "accumulated": {"d1": {}}}
    tc = ToolCandidate(
        doc_id="d1",
        score=0.8,
        item=CandidateItem(docId="d1", score=0.8),
        match_explanation={"hardMatches": [{"field": "city", "matched": False, "required": True}]},
    )

    assert _validate_output([tc], state) == []


def test_service_debug_includes_metrics():
    from search_agent.models.api import SearchRequest
    from search_agent.service import SearchAgentService

    tool = _FakeTool()
    llm = _llm()
    svc = SearchAgentService(tool=tool, llm_client=llm)
    req = SearchRequest(query="Python engineer", companyId=39978, topK=20, details=True)
    resp = svc.search(req)
    assert resp.debug is not None
    assert "agentMetrics" in resp.debug
    assert resp.debug["agentMetrics"]["recall"]["judged"] == 5
