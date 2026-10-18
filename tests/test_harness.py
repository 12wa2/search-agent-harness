"""End-to-end Harness tests with FakeLlmClient + FakeSearchTool.

Covers: single-pass, multi-path parallel execute, reflection loop (reflect ->
revise -> execute), understand failure (fallback_baseline), clarification path.
"""

from __future__ import annotations

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.core.budget import BudgetState
from search_agent.harness import Harness
from search_agent.llm.client import FakeLlmClient
from search_agent.models.api import CandidateItem
from search_agent.models.artifacts import Budget
from search_agent.models.ir import (
    CandidateSearchIR,
    SemanticConditionIR,
    StructuredConditionIR,
    WhereNode,
)
from search_agent.stages.base import Deps

# ── helpers ──────────────────────────────────────────────────────────────


def _leaf(field="skill", values=("Python",)):
    return WhereNode(
        type="leaf",
        condition=StructuredConditionIR(field=field, operator="any", values=list(values)),
    )


def _ir(field="skill", values=("Python",)):
    return CandidateSearchIR(where=_leaf(field, values))


def _sem_ir(text="Python backend"):
    return SemanticConditionIR(text=text, fields=["work_vector"])


def _candidate(doc_id, score=0.8):
    return ToolCandidate(
        doc_id=doc_id,
        score=score,
        item=CandidateItem(docId=doc_id, score=score, name=f"Candidate {doc_id}"),
        hard_matches=[{"field": "skill", "matched": True}],
    )


def _budget(top_k=20, target=20):
    return Budget(
        max_iterations=3,
        max_paths_per_iteration=4,
        target_qualified=target,
        time_budget_sec=12.0,
        max_llm_calls=40,
    )


def _deps(llm, tool, top_k=20, target=20):
    return Deps(
        llm=llm,
        tool=tool,
        budget_state=BudgetState(budget=_budget(top_k, target)),
    )


def _initial_state(query="Python 后端工程师", top_k=20):
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


class FakeSearchTool:
    """Stub SearchTool: returns canned items per call index."""

    def __init__(self, items_batches=None, rerank_items=None, fail_rerank=False):
        self._batches = items_batches or []
        self._rerank = rerank_items
        self._fail_rerank = fail_rerank
        self.search_calls: list[SearchToolRequest] = []
        self.rerank_calls: list[RerankToolRequest] = []

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        self.search_calls.append(req)
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
        if self._fail_rerank:
            raise RuntimeError("rerank unavailable")
        items = self._rerank if self._rerank is not None else req.candidates[: req.top_k]
        return SearchToolResult(
            items=items,
            total=len(items),
            quality="rich",
            timings_ms={"total": 200},
        )


def _understand_resp(needs_clarify=False, *, difficulty="mid"):
    return {
        "title": "Python 后端工程师",
        "must_abilities": [{"name": "Python", "level": "must"}],
        "nice_abilities": [{"name": "Django", "level": "nice"}],
        "hard_constraints": [],
        "subjective_difficulty": difficulty,
        "difficulty_reason": "",
        "needs_clarification": needs_clarify,
        "clarify_questions": ["请明确工作年限要求"] if needs_clarify else [],
        "talent_profile": {
            "source_hypotheses": [],
            "must_keywords": ["Python"],
            "soft_signals": [],
            "risk_filters": [],
        },
    }


def _plan_resp(paths):
    return {
        "paths": [
            {
                "rationale": p[0],
                "intent": p[1].model_dump(),
                "expected_yield": "high",
            }
            for p in paths
        ]
    }


def _judge_resp(doc_ids):
    return {
        "judgments": [
            {
                "doc_id": did,
                "matched_abilities": ["Python"],
                "missing_abilities": [],
                "summary": f"match for {did}",
            }
            for did in doc_ids
        ]
    }


# ── tests ────────────────────────────────────────────────────────────────


def test_single_pass_happy_path():
    """understand -> plan(1) -> execute -> judge -> evaluate -> finalize -> done."""
    cands = [_candidate(f"d{i}") for i in range(8)]
    tool = FakeSearchTool(items_batches=[cands], rerank_items=cands[:20])
    llm = FakeLlmClient(
        responses={
            "agent_understand": _understand_resp(),
            "agent_plan": _plan_resp([("direct skill", _ir())]),
            "agent_judge": _judge_resp([f"d{i}" for i in range(8)]),
        }
    )
    harness = Harness(_deps(llm, tool))
    state = harness.run(_initial_state())

    assert state["phase"] == "done"
    assert not state.get("fallback_baseline")
    assert state.get("rerank_used") is True
    assert len(state["final_items"]) == 8
    assert state["final_total"] == 8
    assert len(tool.search_calls) == 1
    assert len(tool.rerank_calls) == 1
    # agentTrace present
    trace = state["agent_trace"]
    assert "iterations" in trace
    assert trace["final"]["rerankUsed"] is True
    assert trace["degradedReason"] is None


def test_multi_path_parallel_execute():
    """plan produces 2 paths; both execute (parallel), results merge."""
    batch1 = [_candidate(f"a{i}") for i in range(6)]
    batch2 = [_candidate(f"b{i}") for i in range(6)]
    tool = FakeSearchTool(items_batches=[batch1, batch2])
    ir1 = _ir("skill", ("Python",))
    ir2 = _ir("skill", ("Java",))
    llm = FakeLlmClient(
        responses={
            "agent_understand": _understand_resp(),
            "agent_plan": _plan_resp([("python path", ir1), ("java path", ir2)]),
            "agent_judge": _judge_resp([f"a{i}" for i in range(6)] + [f"b{i}" for i in range(6)]),
        }
    )
    harness = Harness(_deps(llm, tool))
    state = harness.run(_initial_state())

    assert state["phase"] == "done"
    assert len(tool.search_calls) == 2
    # 12 unique candidates merged
    assert len(state["accumulated"]) == 12
    # rerank_pool received the merged pool
    assert len(tool.rerank_calls) == 1
    assert len(tool.rerank_calls[0].candidates) == 12


def test_reflection_loop_then_finalize():
    """First round thin -> reflect -> revise(add_path) -> execute -> finalize."""
    # round 1: 3 candidates (thin, below target)
    # round 2: 10 candidates (enough to hit target)
    batch1 = [_candidate(f"a{i}") for i in range(3)]
    batch2 = [_candidate(f"b{i}") for i in range(10)]
    tool = FakeSearchTool(items_batches=[batch1, batch2])

    class _PlanScript:
        def __init__(self):
            self.n = 0

        def __call__(self):
            self.n += 1
            if self.n == 1:
                return _plan_resp([("first path", _ir("skill", ("Python",)))])
            return {"paths": []}  # no new paths on re-plan

    reflect_resp = {
        "common_traits": ["Python"],
        "gaps": ["need more"],
        "new_source_hints": [],
        "path_actions": [
            {
                "kind": "add_path",
                "rationale": "broader search",
                "intent": _ir("skill", ("Go",)).model_dump(),
            }
        ],
    }

    llm = FakeLlmClient(
        responses={
            "agent_understand": _understand_resp(),
            "agent_plan": _PlanScript(),
            "agent_judge": lambda: _judge_resp(
                [f"a{i}" for i in range(3)] + [f"b{i}" for i in range(10)]
            ),
            "agent_reflect": reflect_resp,
        }
    )
    harness = Harness(_deps(llm, tool, target=20))
    state = harness.run(_initial_state())

    assert state["phase"] == "done"
    assert state["iteration"] >= 1
    # 2 execute rounds
    assert len(tool.search_calls) == 2
    assert len(state["accumulated"]) == 13


def test_understand_failure_falls_back():
    """understand LLM raises -> fallback_baseline, no tool calls."""

    class _BoomLlm:
        def complete_json(self, **kwargs):
            raise RuntimeError("LLM down")

    tool = FakeSearchTool()
    harness = Harness(_deps(_BoomLlm(), tool))
    state = harness.run(_initial_state())

    assert state["phase"] == "done"
    assert state["fallback_baseline"] is True
    assert state["degraded_reason"] == "understand_failed"
    assert len(tool.search_calls) == 0


def test_clarification_short_circuits():
    """understand returns needs_clarification -> done, no execute."""
    tool = FakeSearchTool()
    llm = FakeLlmClient(responses={"agent_understand": _understand_resp(needs_clarify=True)})
    harness = Harness(_deps(llm, tool))
    state = harness.run(_initial_state())

    assert state["phase"] == "done"
    assert state["need_clarify"] is True
    assert len(tool.search_calls) == 0
    assert state["final_total"] == 0


def test_rerank_failure_falls_back_to_max_score():
    """rerank_pool raises -> finalize uses max-score order, degradedReason set."""
    cands = [_candidate(f"d{i}", score=0.9 - i * 0.05) for i in range(5)]
    tool = FakeSearchTool(items_batches=[cands], fail_rerank=True)
    llm = FakeLlmClient(
        responses={
            "agent_understand": _understand_resp(),
            "agent_plan": _plan_resp([("path", _ir())]),
            "agent_judge": _judge_resp([f"d{i}" for i in range(5)]),
        }
    )
    harness = Harness(_deps(llm, tool))
    state = harness.run(_initial_state())

    assert state["phase"] == "done"
    assert state["degraded_reason"] == "global_rerank_failed"
    assert state.get("rerank_used") is False
    assert len(state["final_items"]) == 5
    # sorted by score descending
    scores = [tc.score for tc in state["final_items"]]
    assert scores == sorted(scores, reverse=True)


def test_profile_convergence_triggers_finalize():
    """When the talent profile stops evolving (no new sources/signals across
    two reflection rounds), evaluate triggers finalize with
    'profile_converged' instead of continuing to reflect."""
    batch1 = [_candidate(f"a{i}") for i in range(5)]
    batch2 = [_candidate(f"b{i}") for i in range(5)]
    batch3 = [_candidate(f"c{i}") for i in range(5)]
    tool = FakeSearchTool(items_batches=[batch1, batch2, batch3])

    class _ReflectScript:
        def __init__(self):
            self.n = 0

        def __call__(self):
            self.n += 1
            if self.n == 1:
                return {
                    "common_traits": ["Python"],
                    "gaps": [],
                    "new_source_hints": [],
                    "path_actions": [
                        {
                            "kind": "add_path",
                            "rationale": "Go path",
                            "intent": _ir("skill", ("Go",)).model_dump(),
                        }
                    ],
                }
            return {
                "common_traits": ["Python"],
                "gaps": [],
                "new_source_hints": [],
                "path_actions": [
                    {
                        "kind": "add_path",
                        "rationale": "Java path",
                        "intent": _ir("skill", ("Java",)).model_dump(),
                    }
                ],
            }

    llm = FakeLlmClient(
        responses={
            "agent_understand": _understand_resp(difficulty="high"),
            "agent_plan": _plan_resp([("python path", _ir("skill", ("Python",)))]),
            "agent_judge": lambda: _judge_resp(
                [f"a{i}" for i in range(5)]
                + [f"b{i}" for i in range(5)]
                + [f"c{i}" for i in range(5)]
            ),
            "agent_reflect": _ReflectScript(),
        }
    )
    harness = Harness(_deps(llm, tool, target=20))
    state = harness.run(_initial_state())

    assert state["phase"] == "done"
    assert state.get("finalize_reason") == "profile_converged"
    # 3 execute rounds: Python, Go, Java (Java executes before convergence
    # is detected at evaluate)
    assert len(tool.search_calls) == 3
