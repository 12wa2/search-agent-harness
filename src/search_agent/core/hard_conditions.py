"""Tri-state hard-condition facts and a count-preserving soft gate.

Hard conditions are business constraints such as minimum years or allowed
cities. The agent exposes failures but never drops candidates for them: the
product promise is "deliver the requested headcount, then clearly expose the
gap".

``satisfied`` means the tool matched the condition; ``violated`` means the
tool returned an actual value that did not match; ``unverified`` means the
evidence is insufficient. Missing evidence is never treated as a pass or as a
violation. Candidate facts are aligned to the job-profile constraints so an
omitted fact is materialised as ``unverified`` in the UI.
"""

from __future__ import annotations

from typing import Any, Literal, cast

from search_agent.contract.tool import ToolCandidate

HardConditionStatus = Literal["satisfied", "violated", "unverified"]
CandidateHardConditionStatus = Literal["satisfied", "violated", "unverified", "not_applicable"]
_STATUS_RANK = {"satisfied": 0, "not_applicable": 0, "unverified": 1, "violated": 2}


def _as_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _expected_values(row: dict[str, Any]) -> list[Any]:
    for key in ("expectedValues", "expected_values", "values", "value"):
        if key in row:
            return _as_values(row.get(key))
    return []


def _actual_values(row: dict[str, Any]) -> list[Any] | None:
    for key in ("actualValues", "actual_values", "actual"):
        if key in row:
            return _as_values(row.get(key))
    return None


def _has_values(values: list[Any] | None) -> bool:
    return any(value is not None and str(value).strip() != "" for value in values or [])


def _fact_status(row: dict[str, Any]) -> HardConditionStatus:
    explicit = str(row.get("status") or "").strip()
    if explicit in {"satisfied", "violated", "unverified"}:
        return cast(HardConditionStatus, explicit)
    if row.get("matched") is True:
        return "satisfied"
    if row.get("matched") is False and _has_values(_actual_values(row)):
        return "violated"
    return "unverified"


def hard_condition_fact_status(row: dict[str, Any]) -> HardConditionStatus:
    """Public tri-state fact classifier shared by delivery and validation."""
    return _fact_status(row)


def _condition_dict(condition: Any) -> dict[str, Any]:
    if isinstance(condition, dict):
        return dict(condition)
    if hasattr(condition, "model_dump"):
        return dict(condition.model_dump())
    return {}


def _required_conditions(hard_constraints: list[Any] | None) -> list[dict[str, Any]]:
    conditions: list[dict[str, Any]] = []
    for raw in hard_constraints or []:
        condition = _condition_dict(raw)
        if condition and condition.get("required", True) is not False:
            conditions.append(condition)
    return conditions


def _normalize_fact(
    raw: dict[str, Any],
    *,
    fallback_condition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    condition = fallback_condition or {}
    field = str(raw.get("field") or condition.get("field") or "").strip()
    operator = str(raw.get("operator") or condition.get("operator") or "eq").strip()
    expected = _expected_values(condition) or _expected_values(raw)
    actual = _actual_values(raw)

    fact = dict(raw)
    fact["field"] = field
    fact["operator"] = operator
    fact["expectedValues"] = expected
    fact["values"] = expected
    fact["required"] = bool(raw.get("required", condition.get("required", True)))
    fact["matched"] = raw.get("matched") is True
    if actual is not None:
        fact["actualValues"] = actual
    else:
        fact.pop("actualValues", None)
    fact["status"] = _fact_status(fact)
    return fact


def _fact_pool(tc: ToolCandidate) -> list[dict[str, Any]]:
    rows = tc.hard_matches
    if not rows:
        explanation = tc.match_explanation or {}
        candidate_rows = explanation.get("hardMatches") if isinstance(explanation, dict) else None
        rows = candidate_rows if isinstance(candidate_rows, list) else []
    return [row for row in rows if isinstance(row, dict)]


def _canonical_values(values: list[Any]) -> tuple[str, ...]:
    return tuple(sorted(str(value).strip().casefold() for value in values if value is not None))


def _pick_fact(
    condition: dict[str, Any],
    remaining: list[dict[str, Any]],
) -> dict[str, Any] | None:
    field = str(condition.get("field") or "").strip()
    operator = str(condition.get("operator") or "eq").strip()
    expected = _canonical_values(_expected_values(condition))

    def find(predicate: Any) -> int | None:
        for index, fact in enumerate(remaining):
            if predicate(fact):
                return index
        return None

    index = find(
        lambda fact: (
            str(fact.get("field") or "").strip() == field
            and str(fact.get("operator") or "eq").strip() == operator
            and _canonical_values(_expected_values(fact)) == expected
        )
    )
    if index is None:
        index = find(
            lambda fact: (
                str(fact.get("field") or "").strip() == field
                and str(fact.get("operator") or "eq").strip() == operator
            )
        )
    if index is None:
        index = find(lambda fact: str(fact.get("field") or "").strip() == field)
    return None if index is None else remaining.pop(index)


def _candidate_facts(
    tc: ToolCandidate,
    hard_constraints: list[Any] | None,
) -> list[dict[str, Any]]:
    required = _required_conditions(hard_constraints)
    raw_facts = _fact_pool(tc)
    if not required:
        return [
            _normalize_fact(fact) for fact in raw_facts if fact.get("required", True) is not False
        ]

    remaining = list(raw_facts)
    aligned: list[dict[str, Any]] = []
    for condition in required:
        fact = _pick_fact(condition, remaining)
        if fact is not None:
            aligned.append(_normalize_fact(fact, fallback_condition=condition))
            continue
        aligned.append(
            _normalize_fact(
                {
                    "field": condition.get("field"),
                    "operator": condition.get("operator", "eq"),
                    "matched": False,
                    "required": True,
                    "source": "job_profile",
                },
                fallback_condition=condition,
            )
        )
    return aligned


def candidate_hard_condition_status(
    tc: ToolCandidate,
    hard_constraints: list[Any] | None = None,
) -> CandidateHardConditionStatus:
    facts = _candidate_facts(tc, hard_constraints)
    statuses = {fact.get("status") for fact in facts if fact.get("required", True)}
    if not statuses:
        return "not_applicable"
    if "violated" in statuses:
        return "violated"
    if "unverified" in statuses:
        return "unverified"
    return "satisfied"


def candidate_violation_count(tc: ToolCandidate) -> int:
    """Count explicit required violations only; missing evidence is not one."""
    return sum(
        1
        for fact in _candidate_facts(tc, None)
        if fact.get("required", True) and fact.get("status") == "violated"
    )


def _attach_facts(
    tc: ToolCandidate,
    facts: list[dict[str, Any]],
    status: CandidateHardConditionStatus,
) -> ToolCandidate:
    counts = {
        "satisfied": sum(1 for fact in facts if fact.get("status") == "satisfied"),
        "violated": sum(1 for fact in facts if fact.get("status") == "violated"),
        "unverified": sum(1 for fact in facts if fact.get("status") == "unverified"),
    }
    explanation = dict(tc.match_explanation or {})
    explanation["hardMatches"] = facts
    explanation["hardConditionStatus"] = status
    explanation["hardConditionCounts"] = counts
    item = tc.item.model_copy(update={"matchExplanation": explanation})
    return tc.model_copy(
        update={
            "item": item,
            "hard_matches": facts,
            "match_explanation": explanation,
        }
    )


def _summary(items: list[ToolCandidate], condition_count: int) -> dict[str, Any]:
    counts = {"satisfied": 0, "violated": 0, "unverified": 0, "not_applicable": 0}
    for tc in items:
        status = str((tc.match_explanation or {}).get("hardConditionStatus") or "")
        if status not in counts:
            status = candidate_hard_condition_status(tc)
        counts[status] += 1

    affected = counts["violated"] + counts["unverified"]
    if affected:
        status = "review_required"
    elif counts["satisfied"]:
        status = "clear"
    else:
        status = "not_applicable"
    return {
        "status": status,
        "delivered": len(items),
        "satisfied": counts["satisfied"],
        "violated": counts["violated"],
        "unverified": counts["unverified"],
        "affected": affected,
        "conditionCount": condition_count,
    }


def apply_hard_condition_soft_gate(
    items: list[ToolCandidate],
    hard_constraints: list[Any] | None = None,
) -> tuple[list[ToolCandidate], dict[str, Any]]:
    """Attach tri-state facts, then stably move gaps to the end of the list.

    The returned list always has the same length and membership as ``items``.
    """
    required = _required_conditions(hard_constraints)
    evaluated: list[tuple[int, int, ToolCandidate]] = []
    for index, tc in enumerate(items):
        facts = _candidate_facts(tc, hard_constraints)
        status = candidate_hard_condition_status(tc, hard_constraints)
        evaluated.append((_STATUS_RANK[status], index, _attach_facts(tc, facts, status)))

    evaluated.sort(key=lambda row: (row[0], row[1]))
    ordered = [row[2] for row in evaluated]
    condition_count = len(required)
    if not condition_count:
        condition_count = max(
            (
                len(facts)
                for tc in ordered
                for facts in [
                    (tc.match_explanation or {}).get("hardMatches")
                    if isinstance(tc.match_explanation, dict)
                    else None
                ]
                if isinstance(facts, list)
            ),
            default=0,
        )
    return ordered, _summary(ordered, condition_count)


__all__ = [
    "CandidateHardConditionStatus",
    "HardConditionStatus",
    "apply_hard_condition_soft_gate",
    "candidate_hard_condition_status",
    "candidate_violation_count",
    "hard_condition_fact_status",
]
