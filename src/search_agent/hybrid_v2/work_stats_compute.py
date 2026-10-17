"""Compute profile-level work_stats and per-segment tenure from work_experiences."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .work_experience_dates import (
    format_es_date,
    gap_months_between,
    tenure_months_between,
    work_row_company,
    work_row_job,
    work_row_start_end,
)
from .work_stats_config import (
    RECENT_TENURE_K,
    SHORT_JOB_MONTHS,
    WORK_STATS_SCHEMA_VERSION,
    normalize_role_tenure_title,
)


@dataclass
class _ParsedSegment:
    source_index: int
    row: dict[str, Any]
    start: datetime | None
    end: datetime | None
    tenure_months: int | None
    is_current: bool


def _is_valid_segment(start: datetime | None, end: datetime | None) -> bool:
    return start is not None or end is not None


def _sort_key(segment: _ParsedSegment, *, as_of: datetime) -> datetime:
    return segment.end or segment.start or as_of


def _compute_role_tenure_summaries(segments: list[_ParsedSegment]) -> list[dict[str, Any]]:
    totals: dict[str, int] = {}
    for segment in segments:
        role = normalize_role_tenure_title(work_row_job(segment.row))
        if not role or segment.tenure_months is None:
            continue
        totals[role] = totals.get(role, 0) + segment.tenure_months
    return [{"role": role, "tenure_months": months} for role, months in sorted(totals.items())]


def compute_work_stats(
    work_experiences: list[dict[str, Any]] | None,
    *,
    as_of: datetime | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Return ``(work_stats, enriched work_experiences)``."""
    as_of = as_of or datetime.now().astimezone()
    rows = [row for row in (work_experiences or []) if isinstance(row, dict)]

    parsed: list[_ParsedSegment] = []
    for index, row in enumerate(rows):
        start, end = work_row_start_end(row, current_time=as_of)
        if not _is_valid_segment(start, end):
            continue
        is_current = end is not None and abs((as_of - end).total_seconds()) < 5
        parsed.append(
            _ParsedSegment(
                source_index=index,
                row=row,
                start=start,
                end=end,
                tenure_months=tenure_months_between(start, end),
                is_current=is_current,
            )
        )

    parsed.sort(key=lambda item: _sort_key(item, as_of=as_of), reverse=True)
    seq_by_index: dict[int, tuple[int, int | None]] = {
        segment.source_index: (seq, segment.tenure_months) for seq, segment in enumerate(parsed)
    }

    enriched_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        copy = dict(row)
        if index in seq_by_index:
            seq, tenure = seq_by_index[index]
            copy["seq"] = seq
            copy["tenure_months"] = tenure
        enriched_rows.append(copy)

    if not parsed:
        return (
            {
                "schema_version": WORK_STATS_SCHEMA_VERSION,
                "has_work_rows": False,
                "segment_count": 0,
            },
            enriched_rows,
        )

    tenures = [segment.tenure_months for segment in parsed if segment.tenure_months is not None]
    recent_tenures = tenures[:RECENT_TENURE_K]
    latest = parsed[0]

    inter_gaps: list[int] = []
    for index in range(len(parsed) - 1):
        newer = parsed[index]
        older = parsed[index + 1]
        if older.end is None or newer.start is None:
            continue
        inter_gaps.append(gap_months_between(older.end, newer.start))

    max_inter_gap = max(inter_gaps) if inter_gaps else 0
    gap_count = sum(1 for gap in inter_gaps if gap > 0)
    gap_count_gt_6m = sum(1 for gap in inter_gaps if gap > 6)
    gap_count_gt_12m = sum(1 for gap in inter_gaps if gap > 12)
    short_job_count = sum(
        1 for tenure in tenures if tenure is not None and tenure < SHORT_JOB_MONTHS
    )
    avg_tenure = int(round(sum(tenures) / len(tenures))) if tenures else 0
    max_tenure = max(tenures) if tenures else 0

    latest_end = latest.end
    latest_is_current = latest.is_current or (
        latest_end is not None and abs((as_of - latest_end).total_seconds()) < 5
    )

    stats: dict[str, Any] = {
        "schema_version": WORK_STATS_SCHEMA_VERSION,
        "has_work_rows": True,
        "segment_count": len(parsed),
        "latest_work_company": work_row_company(latest.row),
        "latest_work_job": work_row_job(latest.row),
        "latest_work_start": format_es_date(latest.start),
        "latest_work_is_current": latest_is_current,
        "recent_tenure_months": recent_tenures,
        "avg_tenure_months": avg_tenure,
        "max_tenure_months": max_tenure,
        "short_job_count": short_job_count,
        "inter_segment_gap_months": inter_gaps,
        "max_inter_segment_gap_months": max_inter_gap,
        "inter_segment_gap_count": gap_count,
        "inter_segment_gap_count_gt_6m": gap_count_gt_6m,
        "inter_segment_gap_count_gt_12m": gap_count_gt_12m,
        "role_tenure": _compute_role_tenure_summaries(parsed),
    }
    if recent_tenures:
        stats["recent_tenure_1_months"] = recent_tenures[0]
    if len(recent_tenures) > 1:
        stats["recent_tenure_2_months"] = recent_tenures[1]
    if not latest_is_current and latest_end is not None:
        stats["latest_work_end"] = format_es_date(latest_end)

    return stats, enriched_rows


def enrich_profile_work_stats(
    source: dict[str, Any], *, as_of: datetime | None = None
) -> dict[str, Any]:
    work_experiences = source.get("work_experiences")
    if not isinstance(work_experiences, list):
        work_experiences = []
    stats, enriched = compute_work_stats(work_experiences, as_of=as_of)
    source["work_stats"] = stats
    source["work_experiences"] = enriched
    return source


__all__ = ["compute_work_stats", "enrich_profile_work_stats"]
