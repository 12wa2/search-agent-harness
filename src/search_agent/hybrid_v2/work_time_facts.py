"""Build rerank timeFacts aligned with ES work_stats / shared date parsing."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from search_agent.models.api import CandidateItem

from .work_experience_dates import (
    parse_date_like,
    work_row_company,
    work_row_job,
    work_row_start_end,
)
from .work_stats_compute import compute_work_stats


def iter_work_rows_from_candidate(item: CandidateItem) -> list[dict[str, Any]]:
    snapshot = item.profileSnapshot if isinstance(item.profileSnapshot, dict) else None
    if snapshot and isinstance(snapshot.get("workExperiences"), list):
        rows = [row for row in snapshot.get("workExperiences", []) if isinstance(row, dict)]
        if rows:
            return rows
    if isinstance(item.workExperiences, list):
        return [row for row in item.workExperiences if isinstance(row, dict)]
    return []


def _normalize_stats_keys(stats: dict[str, Any]) -> dict[str, Any]:
    if "schema_version" in stats or "segment_count" in stats:
        return stats
    mapping = {
        "schemaVersion": "schema_version",
        "segmentCount": "segment_count",
        "latestWorkCompany": "latest_work_company",
        "latestWorkJob": "latest_work_job",
        "latestWorkStart": "latest_work_start",
        "latestWorkEnd": "latest_work_end",
        "latestWorkIsCurrent": "latest_work_is_current",
        "recentTenure1Months": "recent_tenure_1_months",
        "recentTenure2Months": "recent_tenure_2_months",
        "avgTenureMonths": "avg_tenure_months",
        "maxTenureMonths": "max_tenure_months",
        "shortJobCount": "short_job_count",
        "maxInterSegmentGapMonths": "max_inter_segment_gap_months",
        "interSegmentGapCountGt6m": "inter_segment_gap_count_gt_6m",
        "interSegmentGapCountGt12m": "inter_segment_gap_count_gt_12m",
        "roleTenure": "role_tenure",
        "hasWorkRows": "has_work_rows",
    }
    out = dict(stats)
    for camel, snake in mapping.items():
        if camel in stats and snake not in out:
            out[snake] = stats.get(camel)
    return out


def _work_stats_from_item(item: CandidateItem) -> dict[str, Any] | None:
    snapshot = item.profileSnapshot if isinstance(item.profileSnapshot, dict) else None
    if not isinstance(snapshot, dict):
        return None
    stats = snapshot.get("workStats")
    if not isinstance(stats, dict):
        stats = snapshot.get("work_stats")
    if isinstance(stats, dict) and stats:
        return _normalize_stats_keys(stats)
    return None


def resolve_candidate_work_stats(
    item: CandidateItem, *, current_time: datetime
) -> dict[str, Any] | None:
    """Resolved work_stats for rerank matching (indexed snapshot or on-the-fly compute)."""
    return _resolve_work_stats(item, current_time=current_time)


def _indexed_work_stats(stats: dict[str, Any]) -> bool:
    if stats.get("schema_version") is not None or stats.get("has_work_rows") is not None:
        return True
    substantive_keys = (
        "segment_count",
        "latest_work_company",
        "latest_work_is_current",
        "recent_tenure_1_months",
        "recent_tenure_2_months",
        "avg_tenure_months",
        "short_job_count",
        "max_inter_segment_gap_months",
        "inter_segment_gap_count_gt_6m",
        "inter_segment_gap_count_gt_12m",
        "role_tenure",
    )
    return any(stats.get(key) is not None for key in substantive_keys)


def _resolve_work_stats(item: CandidateItem, *, current_time: datetime) -> dict[str, Any] | None:
    stats = _work_stats_from_item(item)
    if isinstance(stats, dict) and _indexed_work_stats(stats):
        return stats
    rows = iter_work_rows_from_candidate(item)
    if not rows:
        return None
    stats, _ = compute_work_stats(rows, as_of=current_time)
    return stats


def _iso_or_none(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="seconds")


def _stats_to_work_stats_payload(stats: dict[str, Any]) -> dict[str, Any]:
    """CamelCase subset for LLM rerank payload."""
    payload: dict[str, Any] = {
        "schemaVersion": stats.get("schema_version"),
        "segmentCount": stats.get("segment_count"),
        "latestWorkCompany": stats.get("latest_work_company"),
        "latestWorkJob": stats.get("latest_work_job"),
        "latestWorkStart": stats.get("latest_work_start"),
        "latestWorkEnd": stats.get("latest_work_end"),
        "latestWorkIsCurrent": stats.get("latest_work_is_current"),
        "recentTenure1Months": stats.get("recent_tenure_1_months"),
        "recentTenure2Months": stats.get("recent_tenure_2_months"),
        "avgTenureMonths": stats.get("avg_tenure_months"),
        "maxTenureMonths": stats.get("max_tenure_months"),
        "shortJobCount": stats.get("short_job_count"),
        "maxInterSegmentGapMonths": stats.get("max_inter_segment_gap_months"),
        "interSegmentGapCountGt6m": stats.get("inter_segment_gap_count_gt_6m"),
        "interSegmentGapCountGt12m": stats.get("inter_segment_gap_count_gt_12m"),
        "roleTenure": stats.get("role_tenure"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _stats_has_work_rows(stats: dict[str, Any]) -> bool:
    if stats.get("has_work_rows") is False:
        return False
    return bool(
        stats.get("has_work_rows")
        or stats.get("segment_count")
        or stats.get("latest_work_company")
        or stats.get("latest_work_job")
    )


def build_candidate_time_facts(item: CandidateItem, *, current_time: datetime) -> dict[str, Any]:
    """Unified timeFacts for LLM rerank; prefers indexed work_stats, else compute on the fly."""
    stats = _resolve_work_stats(item, current_time=current_time)
    if isinstance(stats, dict) and _stats_has_work_rows(stats):
        latest_is_current = bool(stats.get("latest_work_is_current"))
        latest_end = parse_date_like(stats.get("latest_work_end"), current_time=current_time)
        latest_start = parse_date_like(stats.get("latest_work_start"), current_time=current_time)
        gap_days: int | None
        if latest_is_current:
            gap_days = 0
        elif latest_end is not None:
            gap_days = max(0, int((current_time - latest_end).days))
        else:
            gap_days = None
        facts: dict[str, Any] = {
            "hasWorkRows": True,
            "latestWorkStart": stats.get("latest_work_start"),
            "latestWorkEnd": stats.get("latest_work_end"),
            "latestCompany": stats.get("latest_work_company"),
            "latestPosition": stats.get("latest_work_job"),
            "latestIsCurrent": latest_is_current,
            "gapDays": gap_days,
            "workStats": _stats_to_work_stats_payload(stats),
            "timeFactsSource": "work_stats",
        }
        if latest_start is not None and not facts.get("latestWorkStart"):
            facts["latestWorkStart"] = _iso_or_none(latest_start)
        if latest_end is not None and not facts.get("latestWorkEnd"):
            facts["latestWorkEnd"] = _iso_or_none(latest_end)
        return facts

    latest_row = candidate_latest_work_row(item, current_time=current_time)
    if latest_row is None:
        return {
            "hasWorkRows": False,
            "latestWorkStart": None,
            "latestWorkEnd": None,
            "latestCompany": None,
            "latestPosition": None,
            "latestIsCurrent": False,
            "gapDays": None,
            "workStats": None,
            "timeFactsSource": "none",
        }

    start_dt, end_dt = work_row_start_end(latest_row, current_time=current_time)
    latest_point = end_dt or start_dt
    gap_days = max(0, int((current_time - latest_point).days)) if latest_point is not None else None
    latest_is_current = bool(
        end_dt is not None and abs((current_time - end_dt).total_seconds()) < 5
    )
    return {
        "hasWorkRows": True,
        "latestWorkStart": _iso_or_none(start_dt),
        "latestWorkEnd": _iso_or_none(end_dt),
        "latestCompany": work_row_company(latest_row),
        "latestPosition": work_row_job(latest_row),
        "latestIsCurrent": latest_is_current,
        "gapDays": gap_days,
        "workStats": None,
        "timeFactsSource": "work_rows_fallback",
    }


def candidate_latest_work_row(
    item: CandidateItem, *, current_time: datetime
) -> dict[str, Any] | None:
    return _candidate_latest_work_row(item, current_time=current_time)


def _candidate_latest_work_row(
    item: CandidateItem, *, current_time: datetime
) -> dict[str, Any] | None:
    stats = _work_stats_from_item(item)
    if isinstance(stats, dict) and _stats_has_work_rows(stats):
        company = stats.get("latest_work_company")
        job = stats.get("latest_work_job")
        if company or job:
            return {
                "company": company,
                "company_name": company,
                "job": job,
                "job_title": job,
                "start_time": stats.get("latest_work_start"),
                "end_time": stats.get("latest_work_end"),
                "end_until_now": 1 if stats.get("latest_work_is_current") else 0,
            }

    latest_row: dict[str, Any] | None = None
    latest_dt: datetime | None = None
    for row in iter_work_rows_from_candidate(item):
        start_dt, end_dt = work_row_start_end(row, current_time=current_time)
        chosen = end_dt or start_dt
        if chosen is None:
            continue
        if latest_dt is None or chosen > latest_dt:
            latest_dt = chosen
            latest_row = row
    return latest_row


def candidate_latest_work_end(item: CandidateItem, *, current_time: datetime) -> datetime | None:
    stats = _resolve_work_stats(item, current_time=current_time)
    if isinstance(stats, dict) and _stats_has_work_rows(stats):
        if stats.get("latest_work_is_current"):
            return current_time
        end_dt = parse_date_like(stats.get("latest_work_end"), current_time=current_time)
        if end_dt is not None:
            return end_dt
        start_dt = parse_date_like(stats.get("latest_work_start"), current_time=current_time)
        return start_dt

    latest: datetime | None = None
    for row in iter_work_rows_from_candidate(item):
        start_dt, end_dt = work_row_start_end(row, current_time=current_time)
        chosen = end_dt or start_dt
        if chosen is None:
            continue
        if latest is None or chosen > latest:
            latest = chosen
    return latest


__all__ = [
    "build_candidate_time_facts",
    "candidate_latest_work_end",
    "candidate_latest_work_row",
    "iter_work_rows_from_candidate",
    "resolve_candidate_work_stats",
]
