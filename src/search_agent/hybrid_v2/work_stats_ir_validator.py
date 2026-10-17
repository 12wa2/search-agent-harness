"""Validate/repair work_stats IR leaves (no NL entity extraction)."""

from __future__ import annotations

import re

from .schemas import StructuredConditionIR, WhereNode
from .work_stats_config import normalize_role_tenure_title
from .work_tenure_intent import (
    classify_work_tenure_intent,
)

_DIRTY_VALUE_RE = re.compile(r"(且|但|和|或|≥|>=|≤|<=|以上|以下|经验|经历|候选人|稳定性|空窗|年)")
_JOB_TITLE_SUFFIXES = ("经理", "工程师", "专员", "总监", "主管", "顾问", "代表", "助理")


def _string_values(values: list[object]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def _is_dirty_value(value: str) -> bool:
    text = value.strip()
    if not text:
        return True
    if len(text) > 20:
        return True
    return bool(_DIRTY_VALUE_RE.search(text))


def _looks_like_job_title(value: str) -> bool:
    text = value.strip()
    if not text or _is_dirty_value(text):
        return False
    canonical = normalize_role_tenure_title(text)
    if not canonical:
        return False
    return any(text.endswith(suffix) for suffix in _JOB_TITLE_SUFFIXES)


def _repair_leaf(
    cond: StructuredConditionIR,
    *,
    query: str,
    notes: list[str],
) -> StructuredConditionIR | None:
    values = _string_values(list(cond.values))
    if cond.field in {"latest_company", "latest_work_job", "company", "position"}:
        if values and all(_is_dirty_value(value) for value in values):
            notes.append(f"work_stats_validate:drop_dirty_{cond.field}")
            return None
        if cond.field == "latest_company" and values:
            primary = values[0]
            if _looks_like_job_title(primary):
                notes.append("work_stats_validate:retype_latest_company_to_job")
                return cond.model_copy(update={"field": "latest_work_job", "values": values})
        if cond.field == "latest_work_job" and values and _is_dirty_value(values[0]):
            notes.append("work_stats_validate:drop_dirty_latest_work_job")
            return None

    if cond.field == "work_years" and classify_work_tenure_intent(query) == "title":
        notes.append("work_stats_validate:drop_work_years_for_role_tenure_cue")
        return None

    return cond


def _dedupe_latest_scope_leaves(
    children: list[WhereNode],
    *,
    notes: list[str],
) -> list[WhereNode]:
    """Drop spurious latest_company when it duplicates latest_work_job value."""
    job_values: set[str] = set()
    company_values: set[str] = set()
    for child in children:
        if child.type != "leaf" or child.condition is None:
            continue
        if child.condition.field == "latest_work_job":
            job_values.update(_string_values(list(child.condition.values)))
        if child.condition.field == "latest_company":
            company_values.update(_string_values(list(child.condition.values)))

    if not job_values or not company_values:
        return children

    out: list[WhereNode] = []
    for child in children:
        if (
            child.type == "leaf"
            and child.condition is not None
            and child.condition.field == "latest_company"
        ):
            vals = set(_string_values(list(child.condition.values)))
            if vals & job_values:
                notes.append("work_stats_validate:drop_duplicate_latest_company")
                continue
        out.append(child)
    return out


def validate_work_stats_where(
    node: WhereNode,
    *,
    query: str,
    notes: list[str],
) -> WhereNode | None:
    tenure_intent = classify_work_tenure_intent(query)

    def walk(current: WhereNode) -> WhereNode | None:
        if current.type == "leaf" and current.condition is not None:
            cond = current.condition
            if cond.field == "work_years" and tenure_intent == "title":
                notes.append("work_stats_validate:drop_work_years_for_role_tenure")
                return None
            repaired = _repair_leaf(cond, query=query, notes=notes)
            if repaired is None:
                return None
            return current.model_copy(update={"condition": repaired})

        if current.type == "not" and current.child is not None:
            child = walk(current.child)
            if child is None:
                return None
            return current.model_copy(update={"child": child})

        if current.type in {"and", "or"}:
            children: list[WhereNode] = []
            for child in current.children:
                if not isinstance(child, WhereNode):
                    continue
                mapped = walk(child)
                if mapped is not None:
                    children.append(mapped)
            if current.type == "and":
                children = _dedupe_latest_scope_leaves(children, notes=notes)
            if not children:
                return None
            if len(children) == 1 and current.type == "and":
                return children[0]
            return current.model_copy(update={"children": children})
        return current

    return walk(node)


__all__ = ["validate_work_stats_where"]
