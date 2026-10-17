"""Compile work_stats structured IR fields to Elasticsearch filters."""

from __future__ import annotations

import re
from typing import Any

from .schemas import StructuredConditionIR
from .work_stats_config import (
    AVG_TENURE_LONG_MIN_MONTHS,
    CAREER_DISCONTINUITY_V1,
    EMPLOYMENT_GAP_PRESET_NEGATIONS,
    EMPLOYMENT_GAP_PRESETS,
    GAP_APPROX_TOLERANCE_MONTHS,
    MAX_TENURE_STRONG_MIN_MONTHS,
    SHORT_JOB_FREQUENT_MIN,
    STABILITY_STRONG_V1,
    resolve_role_tenure_titles,
)

_MONTHS_VALUE_RE = re.compile(r"^(\d+)\s*([mMyY月年])?$")


def _string_values(values: list[Any]) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def _int_values(values: list[Any]) -> list[int]:
    out: list[int] = []
    for value in values:
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)):
            out.append(int(value))
            continue
        text = str(value).strip()
        if text.isdigit():
            out.append(int(text))
    return out


def _parse_months_token(raw: str) -> int | None:
    text = raw.strip()
    if not text:
        return None
    if text in EMPLOYMENT_GAP_PRESETS:
        return None
    match = _MONTHS_VALUE_RE.match(text)
    if not match:
        return None
    amount = int(match.group(1))
    unit = (match.group(2) or "m").lower()
    if unit in {"y", "年"}:
        return amount * 12
    return amount


def _es_now_months_offset(months: int) -> str:
    return f"now-{months}M/d"


def compile_work_segment_count(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    values = _int_values(cond.values)
    if not values:
        return []
    value = values[0]
    field = "work_stats.segment_count"
    if cond.operator == "gte":
        return [{"range": {field: {"gte": value}}}]
    if cond.operator == "lte":
        return [{"range": {field: {"lte": value}}}]
    if cond.operator == "eq":
        return [{"term": {field: value}}]
    if cond.operator == "range" and len(values) >= 2:
        return [{"range": {field: {"gte": values[0], "lte": values[1]}}}]
    if cond.operator == "any":
        return [{"range": {field: {"gt": value}}}]
    return []


def compile_latest_work_job(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    values = _string_values(cond.values)
    if not values:
        return []
    field = "work_stats.latest_work_job"
    if cond.operator in {"eq", "any"}:
        if len(values) == 1:
            return [{"match_phrase": {field: values[0]}}]
        return [
            {
                "bool": {
                    "should": [{"match_phrase": {field: value}} for value in values],
                    "minimum_should_match": 1,
                }
            }
        ]
    if cond.operator == "all":
        return [{"bool": {"filter": [{"match_phrase": {field: value}} for value in values]}}]
    return []


def compile_recent_tenure_1(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    values = _int_values(cond.values)
    if not values:
        return []
    value = values[0]
    field = "work_stats.recent_tenure_1_months"
    if cond.operator == "gte":
        return [{"range": {field: {"gte": value}}}]
    if cond.operator == "lte":
        return [{"range": {field: {"lte": value}}}]
    if cond.operator == "eq":
        return [{"term": {field: value}}]
    if cond.operator == "range" and len(values) >= 2:
        return [{"range": {field: {"gte": values[0], "lte": values[1]}}}]
    return []


def compile_max_tenure(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    values = _int_values(cond.values)
    threshold = values[0] if values else MAX_TENURE_STRONG_MIN_MONTHS
    field = "work_stats.max_tenure_months"
    if cond.operator == "gte":
        return [{"range": {field: {"gte": threshold}}}]
    if cond.operator == "lte":
        return [{"range": {field: {"lte": threshold}}}]
    if cond.operator == "eq":
        return [{"term": {field: threshold}}]
    return []


def compile_avg_tenure(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    values = _int_values(cond.values)
    threshold = values[0] if values else AVG_TENURE_LONG_MIN_MONTHS
    field = "work_stats.avg_tenure_months"
    if cond.operator == "gte":
        return [{"range": {field: {"gte": threshold}}}]
    if cond.operator == "lte":
        return [{"range": {field: {"lte": threshold}}}]
    if cond.operator == "eq":
        return [{"term": {field: threshold}}]
    return []


def compile_short_job_count(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    values = _int_values(cond.values)
    threshold = values[0] if values else SHORT_JOB_FREQUENT_MIN
    field = "work_stats.short_job_count"
    if cond.operator == "gte":
        return [{"range": {field: {"gte": threshold}}}]
    if cond.operator == "lte":
        return [{"range": {field: {"lte": threshold}}}]
    if cond.operator == "eq":
        return [{"term": {field: threshold}}]
    return []


def compile_max_inter_segment_gap(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    values = _int_values(cond.values)
    if not values:
        return []
    value = values[0]
    field = "work_stats.max_inter_segment_gap_months"
    if cond.operator == "gte":
        return [{"range": {field: {"gte": value}}}]
    if cond.operator == "lte":
        return [{"range": {field: {"lte": value}}}]
    if cond.operator == "eq":
        return [{"term": {field: value}}]
    return []


def _expand_latest_company_terms(values: list[str]) -> list[str]:
    if not values:
        return []

    expanded: list[str] = []
    seen: set[str] = set()
    for value in values:
        term = str(value).strip()
        key = term.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        expanded.append(term)
    return expanded or values


def compile_latest_company(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    values = _string_values(cond.values)
    if not values:
        return []
    field = "work_stats.latest_work_company.keyword"
    if cond.operator in {"eq", "any"}:
        expanded = _expand_latest_company_terms(values)
        if len(expanded) == 1:
            return [{"term": {field: expanded[0]}}]
        return [{"terms": {field: expanded}}]
    if cond.operator == "all":
        filters: list[dict[str, Any]] = []
        for value in values:
            terms = _expand_latest_company_terms([value])
            if len(terms) == 1:
                filters.append({"term": {field: terms[0]}})
            else:
                filters.append({"terms": {field: terms}})
        return [{"bool": {"filter": filters}}]
    return []


def compile_job_stability(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    cfg = STABILITY_STRONG_V1
    values = _string_values(cond.values)
    if values and values[0].lower() not in {"strong", "strong_v1", "稳定性强", "稳定性高"}:
        return []

    def _multi_segment_branch() -> dict[str, Any]:
        filters: list[dict[str, Any]] = [
            {"range": {"work_stats.segment_count": {"gte": 2}}},
            {
                "range": {
                    "work_stats.recent_tenure_1_months": {"gte": cfg.recent_tenure_1_min_months}
                }
            },
            {
                "range": {
                    "work_stats.recent_tenure_2_months": {"gte": cfg.recent_tenure_2_min_months}
                }
            },
            {"range": {"work_stats.avg_tenure_months": {"gte": cfg.avg_tenure_min_months}}},
        ]
        if cfg.max_short_job_count is not None:
            filters.append(
                {"range": {"work_stats.short_job_count": {"lte": cfg.max_short_job_count}}}
            )
        return {"bool": {"filter": filters}}

    def _single_segment_branch() -> dict[str, Any]:
        filters: list[dict[str, Any]] = [
            {"term": {"work_stats.segment_count": 1}},
            {
                "range": {
                    "work_stats.recent_tenure_1_months": {"gte": cfg.recent_tenure_1_min_months}
                }
            },
            {"range": {"work_stats.avg_tenure_months": {"gte": cfg.avg_tenure_min_months}}},
        ]
        if cfg.max_short_job_count is not None:
            filters.append(
                {"range": {"work_stats.short_job_count": {"lte": cfg.max_short_job_count}}}
            )
        return {"bool": {"filter": filters}}

    return [
        {
            "bool": {
                "should": [_multi_segment_branch(), _single_segment_branch()],
                "minimum_should_match": 1,
            }
        }
    ]


def compile_inter_segment_gap(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    values = _int_values(cond.values)
    if not values:
        values = [2]
    threshold = values[0]
    field = "work_stats.inter_segment_gap_count_gt_6m"
    if cond.operator == "gte":
        return [{"range": {field: {"gte": threshold}}}]
    if cond.operator == "lte":
        return [{"range": {field: {"lte": threshold}}}]
    if cond.operator == "eq":
        return [{"term": {field: threshold}}]
    return []


def compile_career_discontinuity(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    cfg = CAREER_DISCONTINUITY_V1
    should: list[dict[str, Any]] = [
        {
            "bool": {
                "filter": [
                    {"term": {"work_stats.latest_work_is_current": False}},
                    {
                        "range": {
                            "work_stats.latest_work_end": {
                                "lte": _es_now_months_offset(cfg.trailing_gap_min_months)
                            }
                        }
                    },
                ]
            }
        },
        {
            "range": {
                "work_stats.max_inter_segment_gap_months": {
                    "gte": cfg.max_inter_segment_gap_min_months
                }
            }
        },
        {
            "range": {
                "work_stats.inter_segment_gap_count_gt_6m": {
                    "gte": cfg.inter_segment_gap_count_gt_6m_min
                }
            }
        },
    ]
    return [{"bool": {"should": should, "minimum_should_match": 1}}]


def compile_employment_gap(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    preset_names = [str(value).strip() for value in cond.values if str(value).strip()]
    if len(preset_names) == 1 and preset_names[0] in EMPLOYMENT_GAP_PRESETS:
        return _compile_trailing_gap_preset(preset_names[0])

    months_values: list[int] = []
    for raw in preset_names:
        parsed = _parse_months_token(raw)
        if parsed is not None:
            months_values.append(parsed)
    if not months_values and cond.softText:
        parsed = _parse_months_token(str(cond.softText))
        if parsed is not None:
            months_values.append(parsed)

    if cond.operator == "range" and len(months_values) >= 2:
        center = months_values[0]
        tol = months_values[1] if len(months_values) > 1 else GAP_APPROX_TOLERANCE_MONTHS
        return _trailing_gap_range(
            lower_months=max(0, center - tol),
            upper_months=center + tol,
        )

    if not months_values:
        if cond.operator == "lte" and any(str(value).strip() == "0" for value in cond.values):
            return [{"term": {"work_stats.latest_work_is_current": True}}]
        return []

    months = months_values[0]
    if cond.operator == "gte":
        return _trailing_gap_range(lower_months=months, upper_months=None)
    if cond.operator == "lte":
        return _trailing_gap_short(max_months=months)
    if cond.operator == "eq":
        return _trailing_gap_range(
            lower_months=max(0, months - GAP_APPROX_TOLERANCE_MONTHS),
            upper_months=months + GAP_APPROX_TOLERANCE_MONTHS,
        )
    return []


def _compile_trailing_gap_preset(preset: str) -> list[dict[str, Any]]:
    if preset == "trailing_le_6m":
        return _trailing_gap_short(max_months=6)
    if preset == "trailing_le_3m":
        return _trailing_gap_short(max_months=3)
    if preset == "trailing_gt_6m":
        return _trailing_gap_range(lower_months=6, upper_months=None)
    if preset == "trailing_gt_12m":
        return _trailing_gap_range(lower_months=12, upper_months=None)
    if preset == "approx_12m":
        return _trailing_gap_range(lower_months=10, upper_months=14)
    if preset == "approx_24m":
        return _trailing_gap_range(lower_months=22, upper_months=26)
    gte_months_back, lte_months_back = EMPLOYMENT_GAP_PRESETS[preset]
    return _trailing_gap_range(lower_months=lte_months_back, upper_months=gte_months_back)


def _trailing_gap_short(*, max_months: int) -> list[dict[str, Any]]:
    return [
        {
            "bool": {
                "should": [
                    {"term": {"work_stats.latest_work_is_current": True}},
                    {
                        "range": {
                            "work_stats.latest_work_end": {"gte": _es_now_months_offset(max_months)}
                        }
                    },
                ],
                "minimum_should_match": 1,
            }
        }
    ]


def _trailing_gap_range(
    *,
    lower_months: int | None,
    upper_months: int | None,
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = [{"term": {"work_stats.latest_work_is_current": False}}]
    range_body: dict[str, Any] = {}
    if lower_months is not None:
        range_body["lte"] = _es_now_months_offset(lower_months)
    if upper_months is not None:
        range_body["gte"] = _es_now_months_offset(upper_months)
    if range_body:
        filters.append({"range": {"work_stats.latest_work_end": range_body}})
    elif lower_months is None and upper_months is None:
        return [{"term": {"work_stats.latest_work_is_current": True}}]
    return [{"bool": {"filter": filters}}]


def compile_role_tenure(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    month_values = _int_values(cond.values)
    if not month_values:
        return []
    min_months = month_values[0]
    role_query = (cond.softText or "").strip()
    if not role_query and len(cond.values) > 1:
        role_query = str(cond.values[1]).strip()
    role_keys = resolve_role_tenure_titles(role_query)
    if not role_keys:
        return []
    should = [
        {
            "bool": {
                "filter": [
                    {"match_phrase": {"work_stats.role_tenure.role": role_key}},
                    {"range": {"work_stats.role_tenure.tenure_months": {"gte": min_months}}},
                ]
            }
        }
        for role_key in role_keys
    ]
    if len(should) == 1:
        nested_query: dict[str, Any] = should[0]
    else:
        nested_query = {"bool": {"should": should, "minimum_should_match": 1}}
    return [
        {
            "nested": {
                "path": "work_stats.role_tenure",
                "query": nested_query,
            }
        }
    ]


def compile_work_stats_negated(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    """Compile leaf with ``operator=not`` into positive clauses for ``must_not`` wrapping."""
    month_values = _int_values(cond.values)
    preset_names = [
        name for name in _string_values(list(cond.values)) if name in EMPLOYMENT_GAP_PRESETS
    ]

    if cond.field == "employment_gap":
        if preset_names:
            inverted = EMPLOYMENT_GAP_PRESET_NEGATIONS.get(preset_names[0])
            if inverted:
                return _compile_trailing_gap_preset(inverted)
        if month_values:
            return compile_employment_gap(
                cond.model_copy(update={"operator": "lte", "values": month_values})
            )
        if any(str(value).strip() == "0" for value in cond.values):
            return _trailing_gap_range(lower_months=1, upper_months=None)

    if cond.field == "work_segment_count" and month_values:
        threshold = max(0, month_values[0] - 1)
        return compile_work_segment_count(
            cond.model_copy(update={"operator": "lte", "values": [threshold]})
        )

    if cond.field == "inter_segment_gap" and month_values:
        threshold = max(0, month_values[0] - 1)
        return compile_inter_segment_gap(
            cond.model_copy(update={"operator": "lte", "values": [threshold]})
        )

    if cond.field == "latest_company":
        return compile_latest_company(cond.model_copy(update={"operator": "eq"}))

    if cond.field == "latest_work_job":
        return compile_latest_work_job(cond.model_copy(update={"operator": "eq"}))

    if cond.field in {"job_stability", "career_discontinuity", "role_tenure"}:
        positive = compile_work_stats_condition(cond.model_copy(update={"operator": "eq"}))
        return positive

    positive = compile_work_stats_condition(cond)
    return positive


def compile_work_stats_condition(cond: StructuredConditionIR) -> list[dict[str, Any]]:
    if cond.field == "work_segment_count":
        return compile_work_segment_count(cond)
    if cond.field == "employment_gap":
        return compile_employment_gap(cond)
    if cond.field == "latest_company":
        return compile_latest_company(cond)
    if cond.field == "latest_work_job":
        return compile_latest_work_job(cond)
    if cond.field == "recent_tenure_1":
        return compile_recent_tenure_1(cond)
    if cond.field == "max_tenure":
        return compile_max_tenure(cond)
    if cond.field == "avg_tenure":
        return compile_avg_tenure(cond)
    if cond.field == "short_job_count":
        return compile_short_job_count(cond)
    if cond.field == "job_stability":
        return compile_job_stability(cond)
    if cond.field == "inter_segment_gap":
        return compile_inter_segment_gap(cond)
    if cond.field == "max_inter_segment_gap":
        return compile_max_inter_segment_gap(cond)
    if cond.field == "career_discontinuity":
        return compile_career_discontinuity(cond)
    if cond.field == "role_tenure":
        return compile_role_tenure(cond)
    return []


__all__ = ["compile_work_stats_condition", "compile_work_stats_negated"]
