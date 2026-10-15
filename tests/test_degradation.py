"""Degradation strategy tests: all 7 (stage, failure) pairs.

Tests two layers:
1. Unit: degrade() function returns correct (action, reason) for all 7 pairs
2. Integration: Harness applies the correct degradation when a stage fails

Existing test_harness.py already covers 3 of 7:
  - understand llm_failed -> fallback_baseline
  - understand clarify -> finalize_now
  - rerank_pool llm_failed -> skip_global_rerank

This file covers the remaining 4:
  - plan llm_failed -> single_path
  - execute all_paths_failed -> fallback_baseline
  - judge llm_failed -> skip_judge
  - any budget_exceeded -> finalize_now
"""

from __future__ import annotations

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.core.budget import BudgetState
from search_agent.core.degrade import degrade
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

# ── helpers (mirrors test_harness.py patterns) ───────────────────────────


def _leaf(field="skill", values=("Python",)):
    return WhereNode(
        type="leaf",
        condition=StructuredConditionIR(field=field, operator="any", values=list(values)),
    )


def _ir(field="skill", values=("Python",)):
    return CandidateSearchIR(where=_leaf(field, values))


def _candidate(doc_id, score=0.8):
    return ToolCandidate(
        doc_id=doc_id,
        score=score,
        item=CandidateItem(docId=doc_id, score=score, name=f"Candidate {doc_id}"),
        hard_matches=[{"field": "skill", "matched": True}],
    )


def _budget(top_k=20, target=5, max_llm=40, time_sec=12.0):
    return Budget(
        max_iterations=3,
        max_paths_per_iteration=4,
        target_qualified=target,
        time_budget_sec=time_sec,
        max_llm_calls=max_llm,
    )


def _deps(llm, tool, top_k=20, target=5, max_llm=40, time_sec=12.0):
    return Deps(
        llm=llm,
        tool=tool,
        budget_state=BudgetState(budget=_budget(top_k, target, max_llm, time_sec)),
    )


def _initial_state(query="Python engineer", top_k=20):
    return {
        "query": query,
        "company_id": 39978,
        "hr_id": 1,
        "permission": None,
        "routing": 39978,
        "top_k": top_k,
        "recall_pool_size": None,
        "details": True,
        "budget": _budget(top_k),
    }


def _understand_resp(with_hard=False):
    return {
        "title": "Python engineer",
        "must_abilities": [{"name": "Python", "level": "must"}],
        "nice_abilities": [],
        "hard_constraints": (
            [
                {
                    "field": "skill",
                    "operator": "any",
                    "values": ["Python"],
                    "required": True,
                    "weight": 1.0,
                    "softText": None,
                }
            ]
            if with_hard
            else []
        ),
        "subjective_difficulty": "mid",
        "difficulty_reason": "",
        "needs_clarification": False,
        "clarify_questions": [],
        "talent_profile": {
            "source_hypotheses": [],
            "must_keywords": ["Python"],
            "soft_signals": [],
            "risk_filters": [],
        },
    }


def _plan_resp():
    return {
        "paths": [{"rationale": "direct", "intent": _ir().model_dump(), "expected_yield": "high"}]
    }


def _judge_resp(doc_ids):
    return {
        "judgments": [
            {
                "doc_id": d,
                "matched_abilities": ["Python"],
                "missing_abilities": [],
                "summary": "ok",
                "score": 0.8,
            }
            for d in doc_ids
        ]
    }


class FakeSearchTool:
    def __init__(self, items_batches=None, rerank_items=None, fail_search=False):
        self._batches = items_batches or []
        self._rerank = rerank_items
        self._fail_search = fail_search
        self.search_calls: list[SearchToolRequest] = []
        self.rerank_calls: list[RerankToolRequest] = []

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        self.search_calls.append(req)
        if self._fail_search:
            raise RuntimeError("ES unavailable")
        idx = len(self.search_calls) - 1
        items = self._batches[idx] if idx < len(self._batches) else []
        return SearchToolResult(
            items=items,
            total=len(items),
            quality="rich" if len(items) >= 5 else ("thin" if items else "empty"),
            timings_ms={"total": 100},
        )

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        self.rerank_calls.append(req)
        items = self._rerank if self._rerank is not None else req.candidates[: req.top_k]
        return SearchToolResult(
            items=items,
            total=len(items),
            quality="rich",
            timings_ms={"total": 200},
        )


# ── 1. Unit tests: degrade() function ────────────────────────────────────


class TestDegradeFunction:
    """Verify all 7 (stage, failure) -> (action, reason) mappings."""

    def test_understand_llm_failed(self):
        action, reason = degrade("understand", "llm_failed")
        assert action == "fallback_baseline"
        assert reason == "understand_failed"

    def test_understand_clarify(self):
        action, reason = degrade("understand", "clarify")
        assert action == "finalize_now"
        assert reason == "understand_failed"

    def test_plan_llm_failed(self):
        action, reason = degrade("plan", "llm_failed")
        assert action == "single_path"
        assert reason == "plan_failed_single_path"

    def test_execute_all_paths_failed(self):
        action, reason = degrade("execute", "all_paths_failed")
        assert action == "fallback_baseline"
        assert reason == "all_paths_failed"

    def test_judge_llm_failed(self):
        action, reason = degrade("judge", "llm_failed")
        assert action == "skip_judge"
        assert reason == "judge_failed"

    def test_rerank_llm_failed(self):
        action, reason = degrade("rerank_pool", "llm_failed")
        assert action == "skip_global_rerank"
        assert reason == "global_rerank_failed"

    def test_budget_exceeded(self):
        action, reason = degrade("any", "budget_exceeded")
        assert action == "finalize_now"
        assert reason == "budget_exceeded"

    def test_unknown_failure_defaults_to_finalize(self):
        action, reason = degrade("unknown_stage", "unknown_failure")
        assert action == "finalize_now"


# ── 2. Integration: plan failure -> single_path ──────────────────────────


class TestPlanFailureSinglePath:
    """plan LLM raises -> harness builds fallback path from hard_constraints."""

    def test_plan_failure_uses_single_path_fallback(self):
        cands = [_candidate(f"d{i}", score=0.8) for i in range(5)]
        tool = FakeSearchTool(items_batches=[cands])

        class _PlanBoomLlm(FakeLlmClient):
            def complete_json(self, **kwargs):
                name = kwargs.get("schema_name")
                if name == "agent_understand":
                    return _understand_resp(with_hard=True)
                if name == "agent_plan":
                    raise RuntimeError("plan LLM down")
                if name == "agent_judge":
                    return _judge_resp([f"d{i}" for i in range(5)])
                return {}

        harness = Harness(_deps(_PlanBoomLlm(), tool, target=5))
        state = harness.run(_initial_state())

        assert state["phase"] == "done"
        assert state["degraded_reason"] == "plan_failed_single_path"
        # single_path fallback still executed a search
        assert len(tool.search_calls) == 1
        # results were produced despite plan failure
        assert len(state["final_items"]) > 0


# ── 3. Integration: execute all_paths_failed -> fallback_baseline ──────────────


class TestExecuteAllPathsFailed:
    """SearchTool raises on all paths -> fallback_baseline, no results."""

    def test_all_paths_failed_triggers_fallback(self):
        tool = FakeSearchTool(fail_search=True)
        llm = FakeLlmClient(
            responses={
                "agent_understand": _understand_resp(),
                "agent_plan": _plan_resp(),
            }
        )
        harness = Harness(_deps(llm, tool, target=5))
        state = harness.run(_initial_state())

        assert state["phase"] == "done"
        assert state["fallback_baseline"] is True
        assert state["degraded_reason"] == "all_paths_failed"
        assert len(state.get("final_items", [])) == 0


# ── 4. Integration: judge failure -> skip_judge ──────────────────────────


class TestJudgeFailureSkipJudge:
    """judge LLM raises -> skip to evaluate, finalize with tool scores."""

    def test_judge_failure_skips_to_evaluate(self):
        cands = [_candidate(f"d{i}", score=0.8 - i * 0.05) for i in range(5)]
        tool = FakeSearchTool(items_batches=[cands])

        class _JudgeBoomLlm(FakeLlmClient):
            def complete_json(self, **kwargs):
                name = kwargs.get("schema_name")
                if name == "agent_understand":
                    return _understand_resp()
                if name == "agent_plan":
                    return _plan_resp()
                if name == "agent_judge":
                    raise RuntimeError("judge LLM down")
                return {}

        harness = Harness(_deps(_JudgeBoomLlm(), tool, target=5))
        state = harness.run(_initial_state())

        assert state["phase"] == "done"
        assert state["degraded_reason"] == "judge_failed"
        # finalize still produced results from tool scores (no judge scores)
        assert len(state["final_items"]) == 5


# ── 5. Integration: budget exceeded -> finalize_now ──────────────────────


class TestBudgetExceededFinalize:
    """LLM call budget exceeded after understand -> jump to finalize."""

    def test_llm_budget_exceeded_after_understand(self):
        cands = [_candidate(f"d{i}") for i in range(5)]
        tool = FakeSearchTool(items_batches=[cands])
        llm = FakeLlmClient(
            responses={
                "agent_understand": _understand_resp(),
                "agent_plan": _plan_resp(),
            }
        )
        # max_llm_calls=1: understand uses the only call, plan triggers budget guard
        deps = Deps(
            llm=llm,
            tool=tool,
            budget_state=BudgetState(budget=_budget(target=5, max_llm=1)),
        )
        harness = Harness(deps)
        state = harness.run(_initial_state())

        assert state["phase"] == "done"
        assert state["degraded_reason"] == "budget_exceeded"
        # no search was executed (budget exceeded before plan could run)
        assert len(tool.search_calls) == 0
