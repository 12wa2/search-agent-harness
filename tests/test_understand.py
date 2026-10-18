"""Normalization tests for model output at the understand boundary."""

from __future__ import annotations

from search_agent.config import budget_from_env
from search_agent.core.budget import BudgetState
from search_agent.llm.client import FakeLlmClient
from search_agent.stages.base import Deps
from search_agent.stages.understand import run_understand


def _deps(response: dict) -> Deps:
    budget = budget_from_env(5)
    state = BudgetState(budget=budget)
    return Deps(
        llm=FakeLlmClient({"agent_understand": response}),
        tool=None,
        budget_state=state,
    )


def _response(position_filter: bool) -> dict:
    constraints = [
        {"field": "work_years", "operator": "gte", "values": [3]},
    ]
    if position_filter:
        constraints.append(
            {
                "field": "position",
                "operator": "any",
                "values": ["后端工程师", "服务端开发"],
            }
        )
    return {
        "title": "后端工程师",
        "must_abilities": [{"name": "高并发", "level": "must"}],
        "nice_abilities": [],
        "hard_constraints": constraints,
        "subjective_difficulty": "low",
        "talent_profile": {"must_keywords": ["高并发"], "soft_signals": []},
    }


def test_unstated_position_filter_becomes_semantic_signal() -> None:
    state = {
        "query": "找有3年以上后端经验、做过高并发系统的工程师",
        "conversation_history": [],
    }
    updates = run_understand(state, _deps(_response(position_filter=True)))

    job = updates["job_profile"]
    assert [condition.field for condition in job.hard_constraints] == ["work_years"]
    soft_texts = {condition.text for condition in updates["talent_profile"].soft_signals}
    assert "后端工程师" in soft_texts
    assert job.subjective_difficulty == "mid"


def test_explicit_position_filter_is_kept() -> None:
    state = {
        "query": "只要岗位是后端工程师的候选人，3年以上经验",
        "conversation_history": [],
    }
    updates = run_understand(state, _deps(_response(position_filter=True)))

    assert [condition.field for condition in updates["job_profile"].hard_constraints] == [
        "work_years",
        "position",
    ]
