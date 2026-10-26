"""Tests for tri-state hard-condition facts and the count-preserving soft gate."""

from __future__ import annotations

from typing import Any

from search_agent.contract.tool import ToolCandidate
from search_agent.core.hard_conditions import (
    apply_hard_condition_soft_gate,
    candidate_hard_condition_status,
)
from search_agent.models.api import CandidateItem
from search_agent.models.ir import StructuredConditionIR


def _condition() -> StructuredConditionIR:
    return StructuredConditionIR(
        field="city",
        operator="any",
        values=["Shanghai", "Hangzhou"],
    )


def _city_fact(
    *,
    matched: bool,
    actual: list[str] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    fact: dict[str, Any] = {
        "field": "city",
        "operator": "any",
        "expectedValues": ["Shanghai", "Hangzhou"],
        "matched": matched,
        "required": True,
    }
    if actual is not None:
        fact["actualValues"] = actual
    if status is not None:
        fact["status"] = status
    return fact


def _candidate(
    doc_id: str,
    facts: list[dict[str, Any]],
    score: float = 0.5,
) -> ToolCandidate:
    return ToolCandidate(
        doc_id=doc_id,
        score=score,
        item=CandidateItem(docId=doc_id, score=score),
        hard_matches=facts,
    )


def test_explicit_actual_value_mismatch_is_violated() -> None:
    candidate = _candidate("wrong-city", [_city_fact(matched=False, actual=["Beijing"])])

    assert candidate_hard_condition_status(candidate, [_condition()]) == "violated"


def test_missing_actual_value_is_unverified_not_violated() -> None:
    candidate = _candidate("unknown-city", [_city_fact(matched=False)])

    assert candidate_hard_condition_status(candidate, [_condition()]) == "unverified"


def test_explicit_violated_status_is_preserved() -> None:
    candidate = _candidate("explicit", [_city_fact(matched=False, status="violated")])

    assert candidate_hard_condition_status(candidate, [_condition()]) == "violated"


def test_missing_job_fact_is_materialized_as_unverified() -> None:
    candidate = _candidate(
        "missing-fact",
        [
            {
                "field": "skill",
                "operator": "any",
                "expectedValues": ["Python"],
                "actualValues": ["Python"],
                "matched": True,
                "required": True,
            }
        ],
    )

    ordered, summary = apply_hard_condition_soft_gate([candidate], [_condition()])

    facts = ordered[0].match_explanation["hardMatches"]
    assert len(facts) == 1
    assert facts[0]["field"] == "city"
    assert facts[0]["status"] == "unverified"
    assert "actualValues" not in facts[0]
    assert summary["unverified"] == 1
    assert summary["violated"] == 0


def test_soft_gate_preserves_membership_and_keeps_group_order() -> None:
    items = [
        _candidate("missing", [_city_fact(matched=False)], 0.2),
        _candidate("violated", [_city_fact(matched=False, actual=["Beijing"])], 0.9),
        _candidate("good-a", [_city_fact(matched=True, actual=["Shanghai"])], 0.5),
        _candidate("good-b", [_city_fact(matched=True, actual=["Hangzhou"])], 0.4),
    ]
    original_ids = [item.doc_id for item in items]

    ordered, summary = apply_hard_condition_soft_gate(items, [_condition()])

    assert [item.doc_id for item in ordered] == [
        "good-a",
        "good-b",
        "missing",
        "violated",
    ]
    assert len(ordered) == len(items)
    assert {item.doc_id for item in ordered} == set(original_ids)
    assert [item.doc_id for item in items] == original_ids
    assert summary == {
        "status": "review_required",
        "delivered": 4,
        "satisfied": 2,
        "violated": 1,
        "unverified": 1,
        "affected": 2,
        "conditionCount": 1,
    }


def test_summary_is_not_applicable_without_constraints_or_facts() -> None:
    ordered, summary = apply_hard_condition_soft_gate([_candidate("plain", [])], [])

    assert [item.doc_id for item in ordered] == ["plain"]
    assert summary["status"] == "not_applicable"
    assert summary["delivered"] == 1
    assert summary["affected"] == 0
    assert summary["conditionCount"] == 0
