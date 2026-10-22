"""Tests for difficulty routing + adaptive budget (Phase 3)."""

from __future__ import annotations

import pytest

from search_agent.contract.tool import (
    SearchToolResult,
    ToolCandidate,
)
from search_agent.core.budget import BudgetState
from search_agent.core.difficulty import (
    adaptive_budget,
    apply_difficulty_floor,
    difficulty_floor,
    difficulty_route,
    is_explicit_simple_query,
    resolve_difficulty,
    route_stages,
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
    assert difficulty_route("low") == "single_pass"
    assert difficulty_route("mid") == "reduced"
    assert difficulty_route("high") == "standard"


@pytest.mark.parametrize(
    "query",
    [
        "帮我找会 python 的",
        "寻找有 3 年经验的 Python 工程师",
        "招聘 Java 后端工程师，北京",
    ],
)
def test_explicit_action_queries_are_simple(query: str):
    assert is_explicit_simple_query(query) is True
    assert resolve_difficulty(query, "mid") == "low"
    assert resolve_difficulty(query, "high") == "low"


@pytest.mark.parametrize(
    "query",
    [
        "寻找 Python 后端工程师，重点考察服务端 API、微服务和系统设计经验",
        "寻找 Python 机器学习 / AI 工程师，重点考察模型训练、推理服务或算法落地经验",
        "寻找 Python 数据工程师，重点考察 ETL、数据平台和批流处理经验",
    ],
)
def test_structured_clarification_options_remain_simple(query: str):
    assert is_explicit_simple_query(query) is True
    assert resolve_difficulty(query, "mid") == "low"


@pytest.mark.parametrize(
    "query",
    [
        "找能力可迁移的架构师",
        "找高并发后端工程师",
        "找 Python 后端工程师，最好来自大厂",
        "Python 后端工程师",
    ],
)
def test_complex_or_non_action_queries_keep_model_or_floor(query: str):
    assert is_explicit_simple_query(query) is False
    assert resolve_difficulty(query, "high") == "high"


def test_single_pass_route_has_five_real_stages():
    assert route_stages("single_pass") == (
        "understand",
        "execute",
        "judge",
        "evaluate",
        "finalize",
    )
    assert "plan" not in route_stages("single_pass")
    assert "reflect" not in route_stages("single_pass")


def test_difficulty_floor_promotes_abstract_queries():
    assert difficulty_floor("找有3年以上经验、做过高并发系统的后端工程师") == "mid"
    assert difficulty_floor("找能力可迁移的架构师") == "high"
    assert difficulty_floor("找3年以上 Java 工程师") == "low"


def test_apply_difficulty_floor_never_lowers_model_choice():
    assert apply_difficulty_floor("找高并发后端工程师", "low") == "mid"
    assert apply_difficulty_floor("找高并发后端工程师", "high") == "high"
    assert apply_difficulty_floor("找 Java 工程师", "low") == "low"
    assert apply_difficulty_floor("找 Java 工程师", "mid") == "mid"


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


def test_low_difficulty_skips_plan_and_reflection():
    """low → single_pass: no plan call, no reflect call, single path."""
    tool = _FakeTool()
    llm = FakeLlmClient(
        responses={
            "agent_understand": _understand_resp("low"),
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
    # keep the one-shot judge within two pages of the requested delivery
    assert state["recall_pool_size"] == 40


def test_mid_difficulty_reduces_budget():
    """mid → reduced: plan still called, but budget shrunk."""
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

    assert state["difficulty_route"] == "reduced"
    assert state["budget"].max_iterations == 1
    assert state["budget"].max_paths_per_iteration == 2
    # plan WAS called (reduced still plans)
    plan_calls = [c for c in llm.calls if c[0] == "agent_plan"]
    assert len(plan_calls) == 1


def test_high_difficulty_standard():
    """high → standard: full reflective loop, budget unchanged."""
    tool = _FakeTool()
    llm = FakeLlmClient(
        responses={
            "agent_understand": _understand_resp("high"),
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


def test_low_difficulty_no_hard_constraints_uses_loose_single_path():
    """low with no hard constraints still has a semantic one-shot path."""
    tool = _FakeTool()
    resp = _understand_resp("low")
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

    # The loose path anchors on profile semantics and work_years >= 0, so plan
    # is not needed for an explicitly simple query.
    assert state["difficulty_route"] == "single_pass"
    assert state.get("skip_reflection") is True
    plan_calls = [c for c in llm.calls if c[0] == "agent_plan"]
    assert plan_calls == []
    assert state["executed_stages"] == [
        "understand",
        "execute",
        "judge",
        "evaluate",
        "finalize",
    ]


def test_explicit_simple_query_ignores_llm_clarification_request():
    tool = _FakeTool()
    resp = _understand_resp("mid")
    resp["needs_clarification"] = True
    resp["clarify_questions"] = ["Which role?"]
    llm = FakeLlmClient(
        responses={
            "agent_understand": resp,
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
    state = _state()
    state["query"] = "帮我找会 Python 的"

    state = Harness(_deps(llm, tool)).run(state)

    assert state.get("need_clarify") is not True
    assert state["job_profile"].needs_clarification is False
    assert state["difficulty_route"] == "single_pass"
    assert state["executed_stages"] == [
        "understand",
        "execute",
        "judge",
        "evaluate",
        "finalize",
    ]


def test_actionable_role_query_ignores_llm_clarification_request():
    tool = _FakeTool()
    resp = _understand_resp("high")
    resp["needs_clarification"] = True
    resp["clarify_questions"] = ["请提供具体的岗位名称"]
    llm = FakeLlmClient(
        responses={
            "agent_understand": resp,
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
    state = _state()
    state["query"] = (
        "找有8年以上经验的系统架构师，必须同时具备系统架构设计和微服务架构经验；"
        "跨团队协作、技术方案评审或可迁移背景优先"
    )

    state = Harness(_deps(llm, tool)).run(state)

    assert state.get("need_clarify") is not True
    assert state["job_profile"].needs_clarification is False
    assert "execute" in state["executed_stages"]
