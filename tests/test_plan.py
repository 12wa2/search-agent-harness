"""Tests for plan-stage IR normalization."""

from __future__ import annotations

from search_agent.config import budget_from_env
from search_agent.core.budget import BudgetState
from search_agent.llm.client import FakeLlmClient
from search_agent.models.artifacts import JobProfile, TalentProfile
from search_agent.models.ir import StructuredConditionIR
from search_agent.stages.base import Deps
from search_agent.stages.plan import run_plan


def test_plan_preserves_required_education_scope() -> None:
    job = JobProfile(
        raw_query="本科211的后端工程师",
        title="后端工程师",
        hard_constraints=[
            StructuredConditionIR(field="degree", operator="eq", values=["本科"]),
            StructuredConditionIR(field="school_tier", operator="eq", values=["211"]),
        ],
        subjective_difficulty="high",
    )
    state = {
        "query": job.raw_query,
        "job_profile": job,
        "talent_profile": TalentProfile(),
        "paths": [],
        "tried_signatures": set(),
    }
    deps = Deps(
        llm=FakeLlmClient(
            {
                "agent_plan": {
                    "paths": [
                        {
                            "rationale": "能力路径",
                            "intent": {
                                "where": {
                                    "type": "leaf",
                                    "condition": {
                                        "field": "work_years",
                                        "operator": "gte",
                                        "values": [3],
                                    },
                                },
                                "semantic": [],
                                "meta": {},
                            },
                        }
                    ]
                }
            }
        ),
        tool=None,
        budget_state=BudgetState(budget=budget_from_env(5)),
    )

    updates = run_plan(state, deps)

    intent = updates["paths"][0].intent

    def fields(node):
        if node.type == "leaf":
            return {node.condition.field} if node.condition else set()
        if node.type == "not":
            return fields(node.child) if node.child else set()
        result = set()
        for child in node.children:
            result |= fields(child)
        return result

    assert {"work_years", "degree", "school_tier"} <= fields(intent.where)
