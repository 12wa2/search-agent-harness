"""Tests for Hybrid V2 IR compilation semantics."""

from __future__ import annotations

from search_agent.hybrid_v2.compiler import compile_ir_to_hybrid_result
from search_agent.models.ir import (
    CandidateSearchIR,
    StructuredConditionIR,
    WhereNode,
)


def test_degree_and_school_tier_share_one_education_nested_scope() -> None:
    intent = CandidateSearchIR(
        where=WhereNode(
            type="and",
            children=[
                WhereNode(
                    type="leaf",
                    condition=StructuredConditionIR(
                        field="degree",
                        operator="eq",
                        values=["本科"],
                    ),
                ),
                WhereNode(
                    type="leaf",
                    condition=StructuredConditionIR(
                        field="school_tier",
                        operator="eq",
                        values=["211"],
                    ),
                ),
            ],
        )
    )

    result = compile_ir_to_hybrid_result(intent, query_text="本科是211")

    assert result.meta.notes.count("education_scope=same_nested") == 1
    assert len(result.hard.filter) == 1
    nested = result.hard.filter[0]["nested"]
    assert nested["path"] == "educations"
    filters = nested["query"]["bool"]["filter"]
    assert {"term": {"educations.degree.code": 5}} in filters
    assert {"term": {"educations.school_tier": "211"}} in filters
