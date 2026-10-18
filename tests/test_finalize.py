"""Finalize selection tests: judge the full converged recall page."""

from __future__ import annotations

import json
from typing import Any

from search_agent.contract.tool import SearchToolRequest, SearchToolResult, ToolCandidate
from search_agent.core.budget import BudgetState
from search_agent.models.api import CandidateItem
from search_agent.models.artifacts import (
    Budget,
    CandidateJudgment,
    JobAbility,
    JobProfile,
)
from search_agent.models.ir import StructuredConditionIR
from search_agent.stages.base import Deps
from search_agent.stages.finalize import _selection_key, run_finalize


def _candidate(doc_id: str, score: float, *, has_python: bool) -> ToolCandidate:
    return ToolCandidate(
        doc_id=doc_id,
        score=score,
        item=CandidateItem(
            docId=doc_id,
            score=score,
            name=doc_id,
            skills=[{"name": "Python"}] if has_python else [],
        ),
        hard_matches=[{"field": "work_years", "matched": True, "required": True}],
    )


class _PoolTool:
    def __init__(self, candidates: list[ToolCandidate]) -> None:
        self.candidates = candidates
        self.search_calls: list[SearchToolRequest] = []

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        self.search_calls.append(req)
        return SearchToolResult(
            items=self.candidates,
            total=len(self.candidates),
            quality="rich",
        )

    def rerank_pool(self, req):  # pragma: no cover - must not be used here
        raise AssertionError("converged finalize should not fall back to rerank_pool")


class _SelectiveJudge:
    """Judge only the one relevant candidate as matching Python."""

    def __init__(self, relevant_id: str) -> None:
        self.relevant_id = relevant_id
        self.calls = 0

    def complete_json(self, *, user: str, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        payload = json.loads(user)
        judgments = []
        for candidate in payload.get("candidates", []):
            doc_id = candidate["docId"]
            if doc_id == self.relevant_id:
                matched, missing = ["Python"], []
            else:
                matched, missing = [], ["Python"]
            judgments.append(
                {
                    "doc_id": doc_id,
                    "matched_abilities": matched,
                    "missing_abilities": missing,
                    "summary": "match" if matched else "not a match",
                }
            )
        return {"judgments": judgments}


def test_finalize_judges_full_converged_pool_before_top_k_cut() -> None:
    # The first five backend hits are noise. The relevant hit is rank 10 and
    # would be lost if finalize cut fs_result.items[:top_k] before judging.
    candidates = [_candidate(f"n{i}", 1.0 - i * 0.01, has_python=False) for i in range(30)]
    candidates[9] = _candidate("relevant", 0.01, has_python=True)
    tool = _PoolTool(candidates)
    llm = _SelectiveJudge("relevant")
    deps = Deps(
        llm=llm,
        tool=tool,
        budget_state=BudgetState(budget=Budget(max_llm_calls=20, time_budget_sec=120.0)),
    )
    state = {
        "need_clarify": False,
        "top_k": 5,
        "recall_pool_size": 30,
        "query": "Python backend",
        "company_id": 1,
        "hr_id": None,
        "permission": None,
        "routing": 1,
        "details": False,
        "job_profile": JobProfile(
            raw_query="Python backend",
            title="Backend",
            must_abilities=[JobAbility(name="Python", level="must")],
            hard_constraints=[
                StructuredConditionIR(field="work_years", operator="gte", values=[3])
            ],
        ),
        "reflections": [object()],
        "fallback_baseline": False,
        "paths": [],
        "path_results": [],
        "accumulated": {},
    }

    result = run_finalize(state, deps)

    assert tool.search_calls[0].recall_pool_size == 30
    assert result["final_judged"] == 30
    assert result["final_items"][0].doc_id == "relevant"
    assert "relevant" in {item.doc_id for item in result["final_items"]}
    assert len(result["final_items"]) == 5
    assert llm.calls == 1


def test_selection_prefers_transferable_backend_evidence_on_llm_tie() -> None:
    abilities = [
        JobAbility(name="后端开发能力", level="must"),
        JobAbility(name="高并发系统设计", level="must"),
    ]
    architect = ToolCandidate(
        doc_id="architect",
        score=0.9,
        item=CandidateItem(
            docId="architect",
            score=0.9,
            summary="系统架构师，负责容量规划和故障切换。",
            skills=[{"name": "高并发"}, {"name": "容量规划"}],
        ),
    )
    backend = ToolCandidate(
        doc_id="backend",
        score=0.8,
        item=CandidateItem(
            docId="backend",
            score=0.8,
            summary="业务系统研发工程师，主导交易链路重构。",
            skills=[{"name": "高并发"}, {"name": "Redis"}],
        ),
    )
    accumulated = {
        "architect": CandidateJudgment(doc_id="architect", matched_abilities=["高并发系统设计"]),
        "backend": CandidateJudgment(doc_id="backend", matched_abilities=["高并发系统设计"]),
    }

    assert _selection_key(backend, accumulated, abilities) < _selection_key(
        architect, accumulated, abilities
    )
