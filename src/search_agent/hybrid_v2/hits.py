"""Elasticsearch hit conversion and explainable hard-condition facts."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from search_agent.models.api import CandidateItem
from search_agent.models.ir import CandidateSearchIR, StructuredConditionIR, WhereNode
from search_agent.tools.text import candidate_values, leaf_matches

from .catalogs import get_work_state_catalog
from .work_stats_hard_match import (
    WORK_STATS_FIELD_LABELS,
    WORK_STATS_HARD_FIELDS,
    describe_work_stats_hard_match,
    match_work_stats_hard_condition,
    work_stats_field_values,
)

_VECTOR_KEYS = frozenset(
    {
        "overall_vector",
        "work_vector",
        "project_vector",
        "education_vector",
        "self_evaluation_vector",
        "skills_vector",
        "vector",
    }
)
_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(k|w|万|千)?", re.IGNORECASE)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _label(value: Any) -> str:
    if isinstance(value, dict):
        return _first_text(
            value.get("name"),
            value.get("name_not_analyzed"),
            value.get("nameNotAnalyzed"),
            value.get("label"),
        )
    return _text(value)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _source_from_hit(hit: dict[str, Any]) -> dict[str, Any]:
    source = _mapping(hit.get("_source"))
    payload = source.get("payload")
    if isinstance(payload, str) and payload.strip():
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, dict):
        merged = dict(source)
        merged.update(payload)
        merged.pop("payload", None)
        return merged
    return source


def _names(items: Any, *, limit: int = 20) -> list[str]:
    if not isinstance(items, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in items[:limit]:
        name = _label(raw) if isinstance(raw, dict) else _text(raw)
        if not name or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        out.append(name)
    return out


def _tag_names(source: dict[str, Any], tag_id_to_name: dict[int, str] | None) -> list[str]:
    labels = _names(source.get("tags"))
    for raw in source.get("talent_tags") or []:
        if not isinstance(raw, dict):
            continue
        tag_id = _safe_int(raw.get("tag_id", raw.get("tagId", raw.get("id"))))
        mapped = (tag_id_to_name or {}).get(tag_id, "") if tag_id is not None else ""
        name = _label(raw) or mapped
        if name and name not in labels:
            labels.append(name)
    return labels[:20]


def _skills(source: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in (source.get("skills") or [])[:30]:
        if isinstance(raw, str) and raw.strip():
            out.append({"name": raw.strip()})
            continue
        if not isinstance(raw, dict):
            continue
        name = _label(raw.get("name", raw.get("skill")))
        if not name:
            continue
        row: dict[str, Any] = {"name": name}
        level = _safe_float(raw.get("level"))
        if level is not None:
            row["level"] = level
        out.append(row)
    return out


def _named_rows(source: dict[str, Any], key: str, *, limit: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in (source.get(key) or [])[:limit]:
        if isinstance(raw, str) and raw.strip():
            out.append({"name": raw.strip()})
        elif isinstance(raw, dict):
            row = dict(raw)
            name = _label(row.get("name", row.get("label")))
            if name:
                row.setdefault("name", name)
            out.append(row)
    return out


def _work_rows(source: dict[str, Any], key: str, *, limit: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in (source.get(key) or [])[:limit]:
        if not isinstance(raw, dict):
            continue
        out.append(
            {
                "company_name": _first_text(raw.get("company"), raw.get("company_name")),
                "job_title": _first_text(raw.get("job"), raw.get("job_title"), raw.get("title")),
                "description": _first_text(raw.get("description"), raw.get("job_description")),
                "start_date": _first_text(raw.get("start_time"), raw.get("start_date")),
                "end_date": _first_text(raw.get("end_time"), raw.get("end_date")),
            }
        )
    return out


def _project_rows(source: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in (source.get("project_experiences") or [])[:5]:
        if not isinstance(raw, dict):
            continue
        out.append(
            {
                "project_name": _first_text(raw.get("project_name"), raw.get("name")),
                "description": _first_text(raw.get("description"), raw.get("project_description")),
                "role": _first_text(raw.get("role"), raw.get("job_title")),
                "start_date": _first_text(raw.get("start_time"), raw.get("start_date")),
                "end_date": _first_text(raw.get("end_time"), raw.get("end_date")),
            }
        )
    return out


def _education_rows(source: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in (source.get("educations") or [])[:5]:
        if not isinstance(raw, dict):
            continue
        college = _mapping(raw.get("college"))
        major = _mapping(raw.get("major"))
        degree = _mapping(raw.get("degree"))
        out.append(
            {
                "school_name": _first_text(
                    college.get("name"), raw.get("school_name"), raw.get("school")
                ),
                "major": _first_text(major.get("name"), raw.get("major_name"), raw.get("major")),
                "degree": _first_text(
                    degree.get("name"), raw.get("degree_name"), raw.get("degree")
                ),
                "start_date": _first_text(raw.get("start_time"), raw.get("start_date")),
                "end_date": _first_text(raw.get("end_time"), raw.get("end_date")),
            }
        )
    return out


def _intention_fields(source: dict[str, Any]) -> dict[str, Any]:
    intention = _mapping(source.get("intention"))
    work_type = intention.get("work_type") or intention.get("workType")
    work_state = intention.get("work_state") or intention.get("workState")
    return {
        "expectedPositions": _names(intention.get("positions")),
        "expectedIndustries": _names(intention.get("industries")),
        "expectedCities": _names(intention.get("cities")),
        "expectedSalary": _first_text(
            intention.get("expected_salary"), intention.get("expectedSalary")
        )
        or None,
        "expectedWorkType": _label(work_type) or None,
        "workState": _label(work_state) or None,
    }


def _overview_fields(source: dict[str, Any]) -> dict[str, Any]:
    overview = _mapping(source.get("overview"))
    gender = overview.get("gender") or source.get("gender")
    if isinstance(gender, dict):
        gender = gender.get("code")
    city = _label(overview.get("city") or source.get("current_residence"))
    return {
        "name": _first_text(
            overview.get("name"), overview.get("candidate_name"), overview.get("candidateName")
        )
        or None,
        "workYears": _safe_float(overview.get("work_years", overview.get("workYears"))),
        "age": _safe_int(overview.get("age")),
        "gender": _safe_int(gender),
        "currentResidence": city or None,
        "avatar": _first_text(
            overview.get("profilePictureUrl"),
            overview.get("profile_picture_url"),
            overview.get("wechatAvatar"),
            overview.get("wechat_avatar"),
        ),
    }


def _strip_vectors(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_vectors(raw) for key, raw in value.items() if key not in _VECTOR_KEYS}
    if isinstance(value, list):
        return [_strip_vectors(raw) for raw in value]
    return value


def _truncate(value: Any, limit: int = 400) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _slim_rows(rows: Any, *, limit: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for raw in (rows if isinstance(rows, list) else [])[:limit]:
        if not isinstance(raw, dict):
            continue
        row = _strip_vectors(dict(raw))
        for key in ("description", "job_description", "project_description"):
            if key in row:
                row[key] = _truncate(row[key])
        out.append(row)
    return out


def _profile_snapshot(source: dict[str, Any], tags: list[str]) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "candidateProfileId": source.get("candidate_profile_id"),
        "overview": _strip_vectors(_mapping(source.get("overview"))),
        "workExperiences": _slim_rows(source.get("work_experiences"), limit=5),
        "projectExperiences": _slim_rows(source.get("project_experiences"), limit=3),
        "educations": _slim_rows(source.get("educations"), limit=3),
        "skills": _strip_vectors((source.get("skills") or [])[:20]),
        "tags": tags,
        "intention": _strip_vectors(_mapping(source.get("intention"))),
    }
    work_stats = source.get("work_stats")
    if isinstance(work_stats, dict) and work_stats:
        snapshot["workStats"] = _strip_vectors(work_stats)
    return {key: value for key, value in snapshot.items() if value not in (None, [], {}, "")}


def _collect_knn_details(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            if "within top k" in _text(value.get("description")):
                found.append(value)
                return
            for child in value.get("details") or []:
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(node)
    return found


def _soft_breakdown(
    explanation: Any,
    soft_clauses: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    if not soft_clauses or not isinstance(explanation, dict):
        return None
    details = _collect_knn_details(explanation)
    out: list[dict[str, Any]] = []
    for index, meta in enumerate(soft_clauses):
        boost = float(meta.get("boost") or 0.0)
        contribution = float(details[index].get("value") or 0.0) if index < len(details) else 0.0
        cosine = contribution / boost if boost > 0 else contribution
        out.append(
            {
                "text": meta.get("text"),
                "field": meta.get("field"),
                "boost": boost,
                "cosine": round(max(0.0, min(1.0, cosine)), 4),
                "contribution": round(contribution, 4),
            }
        )
    return out


def hit_to_candidate(
    hit: dict[str, Any],
    *,
    score_max: float = 0.0,
    soft_clauses: list[dict[str, Any]] | None = None,
    match_rate_baseline: int = 40,
    tag_id_to_name: dict[int, str] | None = None,
) -> CandidateItem:
    """Convert one ES hit into the public candidate model."""

    source = _source_from_hit(hit)
    doc_id = _first_text(
        hit.get("_id"), source.get("doc_id"), source.get("candidate_profile_id"), "unknown"
    )
    score = _safe_float(hit.get("_score")) or 0.0
    baseline = max(0, min(100, int(match_rate_baseline)))
    if score_max > 0:
        ratio = max(0.0, min(1.0, score / score_max))
        match_rate = round(baseline + ratio * (100 - baseline))
    else:
        match_rate = 100 if score > 0 else baseline

    tags = _tag_names(source, tag_id_to_name)
    overview = _overview_fields(source)
    intention = _intention_fields(source)
    profile_id = _first_text(source.get("candidate_profile_id"), source.get("candidateProfileId"))
    summary = _first_text(source.get("semantic_summary_text"))
    summary_en = _first_text(source.get("semantic_summary_text_en"))
    talent_pool = _mapping(source.get("talent_pool"))
    return CandidateItem(
        id=profile_id or None,
        docId=doc_id,
        candidateProfileId=profile_id or None,
        userId=_safe_int(source.get("user_id")),
        newestAppId=_safe_int(talent_pool.get("newest_app_id")),
        score=score,
        matchRate=match_rate,
        name=overview["name"],
        workYears=overview["workYears"],
        summaryEn=summary_en or None,
        avatar=overview["avatar"],
        summary=summary,
        gender=overview["gender"],
        age=overview["age"],
        currentResidence=overview["currentResidence"],
        tags=tags,
        skills=_skills(source),
        certificates=_named_rows(source, "certificates"),
        languages=_named_rows(source, "languages"),
        workExperiences=_work_rows(source, "work_experiences"),
        internshipExperiences=_work_rows(source, "internship_experiences"),
        projectExperiences=_project_rows(source),
        educations=_education_rows(source),
        expectedPositions=intention["expectedPositions"],
        expectedIndustries=intention["expectedIndustries"],
        expectedCities=intention["expectedCities"],
        expectedSalary=intention["expectedSalary"],
        expectedWorkType=intention["expectedWorkType"],
        workState=intention["workState"],
        softMatchBreakdown=_soft_breakdown(hit.get("_explanation"), list(soft_clauses or [])),
        profileSnapshot=_profile_snapshot(source, tags) or None,
    )


def hits_to_items(
    hits: list[dict[str, Any]],
    *,
    score_max: float = 0.0,
    soft_clauses: list[dict[str, Any]] | None = None,
    match_rate_baseline: int = 40,
    tag_id_to_name: dict[int, str] | None = None,
) -> list[CandidateItem]:
    return [
        hit_to_candidate(
            hit,
            score_max=score_max,
            soft_clauses=soft_clauses,
            match_rate_baseline=match_rate_baseline,
            tag_id_to_name=tag_id_to_name,
        )
        for hit in hits
        if isinstance(hit, dict)
    ]


def extract_hard_conditions(ir: CandidateSearchIR) -> list[dict[str, Any]]:
    """Flatten required leaves while preserving the negated branch."""

    out: list[dict[str, Any]] = []

    def walk(node: WhereNode, *, negated: bool = False) -> None:
        if node.type == "leaf" and node.condition is not None:
            condition = node.condition
            if not condition.required:
                return
            row: dict[str, Any] = {
                "field": condition.field,
                "operator": condition.operator,
                "values": list(condition.values),
                "required": True,
            }
            if condition.softText:
                row["softText"] = condition.softText
            if negated:
                row["negated"] = True
            out.append(row)
            return
        if node.type in {"and", "or"}:
            for child in node.children:
                walk(child, negated=negated)
            return
        if node.type == "not" and node.child is not None:
            walk(node.child, negated=not negated)

    walk(ir.where)
    return out


def _salary_range(value: Any) -> tuple[int, int] | None:
    text = _text(value).lower().replace(" ", "")
    if not text:
        return None
    matches = list(_NUMBER_RE.finditer(text))
    if not matches:
        return None
    amounts: list[int] = []
    inferred_unit = 1.0
    if any(match.group(2) in {"k", "千"} for match in matches):
        inferred_unit = 1_000.0
    if any(match.group(2) in {"w", "万"} for match in matches):
        inferred_unit = 10_000.0
    for match in matches[:2]:
        amount = float(match.group(1))
        unit = match.group(2)
        multiplier = 1.0
        if unit in {"k", "千"}:
            multiplier = 1_000.0
        elif unit in {"w", "万"}:
            multiplier = 10_000.0
        else:
            multiplier = inferred_unit if inferred_unit > 1 else (1_000.0 if amount <= 200 else 1.0)
        if "元" in text and unit is None:
            multiplier = 1.0
        amounts.append(round(amount * multiplier))
    if not amounts:
        return None
    return min(amounts), max(amounts)


def _salary_matches(
    actual: Any, expected_values: list[Any], operator: str
) -> tuple[bool, list[Any]]:
    actual_range = _salary_range(actual)
    expected_ranges = [value for value in map(_salary_range, expected_values) if value]
    if actual_range is None or not expected_ranges:
        return False, []
    if operator == "not":
        disjoint = all(
            actual_range[1] < expected[0] or actual_range[0] > expected[1]
            for expected in expected_ranges
        )
        return disjoint, []
    if operator == "gte":
        threshold = min(expected[0] for expected in expected_ranges)
        hits = [actual] if actual_range[0] >= threshold else []
        return bool(hits), hits
    if operator == "lte":
        threshold = max(expected[1] for expected in expected_ranges)
        hits = [actual] if actual_range[1] <= threshold else []
        return bool(hits), hits
    if operator == "range":
        low = min(expected[0] for expected in expected_ranges)
        high = max(expected[1] for expected in expected_ranges)
        hit = actual_range[0] <= high and actual_range[1] >= low
        return hit, [actual] if hit else []
    hits = [
        actual
        for expected in expected_ranges
        if actual_range[0] <= expected[1] and actual_range[1] >= expected[0]
    ]
    if operator == "all":
        return all(
            actual_range[0] <= expected[1] and actual_range[1] >= expected[0]
            for expected in expected_ranges
        ), hits
    return bool(hits), hits


def _work_state_matches(actual: Any, expected_values: list[Any], operator: str) -> bool:
    actual_text = _text(actual).casefold()
    catalog = {code: name.casefold() for code, name in get_work_state_catalog()}
    expected = [
        catalog.get(_text(value), _text(value)).casefold()
        for value in expected_values
        if _text(value)
    ]
    if not actual_text or not expected:
        return False
    matched = any(
        value == actual_text or value in actual_text or actual_text in value for value in expected
    )
    return not matched if operator == "not" else matched


def _gender_bucket(value: Any) -> str:
    text = _text(value).casefold()
    if text in {"1", "男", "男性", "male", "m"}:
        return "male"
    if text in {"2", "女", "女性", "female", "f"}:
        return "female"
    return text


def _gender_matches(actual: Any, expected_values: list[Any], operator: str) -> bool:
    actual_bucket = _gender_bucket(actual)
    expected = {_gender_bucket(value) for value in expected_values if _gender_bucket(value)}
    if not actual_bucket or not expected:
        return False
    matched = actual_bucket in expected
    return not matched if operator == "not" else matched


def _actual_birth_year(item: CandidateItem, current_time: datetime) -> int | None:
    overview = _mapping((item.profileSnapshot or {}).get("overview"))
    explicit = _safe_int(overview.get("birth_year", overview.get("birthYear")))
    if explicit is not None:
        return explicit
    return current_time.year - item.age if item.age is not None else None


def _match_scalar(
    field: str,
    actual_values: list[Any],
    expected_values: list[Any],
    operator: str,
) -> tuple[bool, list[Any]]:
    if not actual_values:
        return False, []
    if operator in {"gte", "lte", "range"}:
        actual_nums = [number for number in map(_safe_float, actual_values) if number is not None]
        expected_nums = [
            number for number in map(_safe_float, expected_values) if number is not None
        ]
        if not actual_nums or not expected_nums:
            return False, []
        if operator == "gte":
            hits = [value for value in actual_nums if value >= expected_nums[0]]
        elif operator == "lte":
            hits = [value for value in actual_nums if value <= expected_nums[0]]
        else:
            if len(expected_nums) < 2:
                return False, []
            low, high = sorted(expected_nums[:2])
            hits = [value for value in actual_nums if low <= value <= high]
        return bool(hits), hits

    actual = {_text(value).casefold() for value in actual_values if _text(value)}
    expected = {_text(value).casefold() for value in expected_values if _text(value)}
    if operator == "all":
        matched = expected <= actual
    elif operator == "not":
        matched = not bool(actual & expected)
    else:
        matched = bool(actual & expected)
    return matched, list(actual & expected)


def _hard_fact_for_condition(
    item: CandidateItem,
    condition: dict[str, Any],
    *,
    current_time: datetime,
) -> dict[str, Any] | None:
    field = _text(condition.get("field"))
    if not field:
        return None
    operator = _text(condition.get("operator")) or "eq"
    expected = list(condition.get("values") or [])
    negated = bool(condition.get("negated"))
    soft_text = _text(condition.get("softText") or condition.get("soft_text")) or None

    if field in WORK_STATS_HARD_FIELDS:
        matched, matched_values = match_work_stats_hard_condition(
            item,
            field=field,
            operator=operator,
            values=expected,
            soft_text=soft_text,
            negated=negated,
            current_time=current_time,
        )
        actual = work_stats_field_values(item, field, current_time=current_time)
        return {
            "field": field,
            "fieldLabel": WORK_STATS_FIELD_LABELS.get(field, field),
            "operator": operator,
            "expectedValues": expected,
            "actualValues": actual or matched_values,
            "matched": bool(matched),
            "matchedValues": matched_values,
            "matchHint": describe_work_stats_hard_match(
                field=field,
                operator=operator,
                expected_values=expected,
                matched=bool(matched),
                matched_values=matched_values,
                soft_text=soft_text,
            ),
            **({"negated": True} if negated else {}),
        }

    if field == "expected_salary":
        actual_values = [item.expectedSalary] if item.expectedSalary else []
        matched, matched_values = _salary_matches(item.expectedSalary, expected, operator)
        if negated:
            matched = not matched
    elif field == "gender":
        actual_values = [item.gender] if item.gender is not None else []
        matched = _gender_matches(item.gender, expected, operator)
        matched_values = actual_values if matched else []
        if negated:
            matched = not matched
    elif field == "work_state":
        actual_values = [item.workState] if item.workState else []
        matched = _work_state_matches(item.workState, expected, operator)
        matched_values = actual_values if matched else []
        if negated:
            matched = not matched
    elif field == "birth_year":
        birth_year = _actual_birth_year(item, current_time)
        actual_values = [birth_year] if birth_year is not None else []
        matched, matched_values = _match_scalar(field, actual_values, expected, operator)
        if negated:
            matched = not matched
    elif field == "talent_tag":
        actual_values = list(item.tags)
        matched, matched_values = _match_scalar(field, actual_values, expected, operator)
        if negated:
            matched = not matched
    else:
        raw_values = candidate_values(item, field)
        if raw_values is None:
            actual_values = []
        elif isinstance(raw_values, list):
            actual_values = list(raw_values)
        else:
            actual_values = [raw_values]
        try:
            leaf = StructuredConditionIR(
                field=field,
                operator=operator,
                values=expected,
                required=True,
            )
        except (ValidationError, ValueError):
            return None
        matched = leaf_matches(leaf, item)
        matched_values = actual_values if matched else []
        if negated:
            matched = not matched

    return {
        "field": field,
        "operator": operator,
        "expectedValues": expected,
        "actualValues": actual_values,
        "matched": bool(matched),
        "matchedValues": matched_values,
        **({"negated": True} if negated else {}),
    }


def hard_facts_for_candidate(
    item: CandidateItem,
    hard_conditions: list[dict[str, Any]],
    *,
    current_time: datetime | None = None,
) -> list[dict[str, Any]]:
    now = current_time or datetime.now().astimezone()
    return [
        fact
        for condition in hard_conditions
        if isinstance(condition, dict)
        for fact in [_hard_fact_for_condition(item, condition, current_time=now)]
        if fact is not None
    ]


def condition_matches(
    *,
    field: str,
    operator: str,
    expected_values: list[Any],
    actual_values: list[Any],
) -> tuple[bool, list[Any]]:
    """Reusable scalar matcher for callers that already extracted actual values."""

    if field == "expected_salary":
        actual = actual_values[0] if actual_values else None
        return _salary_matches(actual, expected_values, operator)
    if field == "work_state":
        actual = actual_values[0] if actual_values else None
        return _work_state_matches(actual, expected_values, operator), []
    return _match_scalar(field, actual_values, expected_values, operator)


__all__ = [
    "condition_matches",
    "extract_hard_conditions",
    "hard_facts_for_candidate",
    "hit_to_candidate",
    "hits_to_items",
]
