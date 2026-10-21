"""IR helpers that preserve required education-scope filters across Agent paths."""

from __future__ import annotations

from search_agent.models.ir import CandidateSearchIR, StructuredConditionIR, WhereNode

EDUCATION_SCOPE_FIELDS = frozenset({"degree", "school_tier"})


def _intent_has_field(node: WhereNode, field: str) -> bool:
    if node.type == "leaf":
        return bool(node.condition and node.condition.field == field)
    if node.type == "not":
        return bool(node.child and _intent_has_field(node.child, field))
    return any(_intent_has_field(child, field) for child in node.children)


def preserve_required_education_constraints(
    intent: CandidateSearchIR,
    constraints: list[StructuredConditionIR],
) -> CandidateSearchIR:
    """Re-attach required degree/school-tier filters omitted by an LLM path."""

    required = [
        condition
        for condition in constraints
        if condition.field in EDUCATION_SCOPE_FIELDS and condition.required
    ]
    missing = [
        condition for condition in required if not _intent_has_field(intent.where, condition.field)
    ]
    if not missing:
        return intent

    added = [WhereNode(type="leaf", condition=condition) for condition in missing]
    if intent.where.type == "and":
        where = WhereNode(type="and", children=[*intent.where.children, *added])
    else:
        where = WhereNode(type="and", children=[intent.where, *added])
    return intent.model_copy(update={"where": where})


__all__ = ["EDUCATION_SCOPE_FIELDS", "preserve_required_education_constraints"]
