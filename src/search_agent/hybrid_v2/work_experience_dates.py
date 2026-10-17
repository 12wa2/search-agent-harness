"""Shared work-experience date parsing for write path (work_stats) and rerank."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_DATE_RE = re.compile(
    r"(?P<y>20\d{2}|19\d{2})\D?(?P<m>1[0-2]|0?[1-9])?\D?(?P<d>3[01]|[12]\d|0?[1-9])?"
)
_DATE_RANGE_RE = re.compile(
    r"(?P<start>(?:19|20)\d{2}(?:[./-](?:1[0-2]|0?[1-9]))?)\s*(?:到|至|~|～|-)\s*"
    r"(?P<end>至今|现在|目前|current|present|now|(?:19|20)\d{2}(?:[./-](?:1[0-2]|0?[1-9]))?)",
    re.IGNORECASE,
)


def parse_date_like(value: Any, *, current_time: datetime) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 10_000_000_000:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=current_time.tzinfo)
        except Exception:
            return None
    text = str(value).strip()
    if not text:
        return None
    lowered = text.lower()
    lowered_compact = re.sub(r"\s+", "", lowered)
    if any(k in lowered for k in ("至今", "现在", "目前", "current", "present", "now")):
        return current_time
    if any(k in lowered_compact for k in ("至今", "现在", "目前", "current", "present", "now")):
        return current_time
    match = _DATE_RE.search(text)
    if not match:
        return None
    year = int(match.group("y"))
    month = int(match.group("m") or 1)
    day = int(match.group("d") or 1)
    try:
        return current_time.replace(year=year, month=month, day=day)
    except ValueError:
        try:
            return current_time.replace(year=year, month=month, day=1)
        except ValueError:
            return None


def is_current_marker(value: Any) -> bool:
    if value is None:
        return False
    text = str(value).strip().lower()
    if not text:
        return False
    if text in {"1", "true", "yes", "y"}:
        return True
    compact = re.sub(r"\s+", "", text)
    return compact in {"至今", "现在", "目前", "current", "present", "now"}


def row_date_candidates(row: dict[str, Any], *, is_start: bool) -> list[Any]:
    if is_start:
        keys = (
            "start_time",
            "startTime",
            "start_date",
            "startDate",
            "from_time",
            "fromTime",
            "begin_time",
            "beginTime",
            "start",
            "开始时间",
            "起始时间",
        )
    else:
        keys = (
            "end_time",
            "endTime",
            "end_date",
            "endDate",
            "to_time",
            "toTime",
            "finish_time",
            "finishTime",
            "end",
            "结束时间",
        )
    values: list[Any] = []
    for key in keys:
        if key in row and row.get(key) is not None:
            values.append(row.get(key))
    return values


def parse_date_range_text(
    value: Any, *, current_time: datetime
) -> tuple[datetime | None, datetime | None]:
    text = str(value or "").strip()
    if not text:
        return None, None
    match = _DATE_RANGE_RE.search(text)
    if not match:
        return None, None
    start_dt = parse_date_like(match.group("start"), current_time=current_time)
    end_dt = parse_date_like(match.group("end"), current_time=current_time)
    return start_dt, end_dt


def work_row_start_end(
    row: dict[str, Any],
    *,
    current_time: datetime,
) -> tuple[datetime | None, datetime | None]:
    start_dt: datetime | None = None
    end_dt: datetime | None = None
    for raw in row_date_candidates(row, is_start=True):
        start_dt = parse_date_like(raw, current_time=current_time)
        if start_dt is not None:
            break
    for raw in row_date_candidates(row, is_start=False):
        end_dt = parse_date_like(raw, current_time=current_time)
        if end_dt is not None:
            break
    if start_dt is None or end_dt is None:
        for key in ("date_range", "dateRange", "duration", "period", "time_range", "timeRange"):
            if key not in row:
                continue
            range_start, range_end = parse_date_range_text(row.get(key), current_time=current_time)
            if start_dt is None and range_start is not None:
                start_dt = range_start
            if end_dt is None and range_end is not None:
                end_dt = range_end
            if start_dt is not None and end_dt is not None:
                break
    if end_dt is None and any(
        is_current_marker(row.get(key))
        for key in (
            "to_now",
            "toNow",
            "is_current",
            "isCurrent",
            "still_working",
            "stillWorking",
            "current",
            "end_until_now",
            "endUntilNow",
        )
    ):
        end_dt = current_time
    return start_dt, end_dt


def floor_calendar_month_diff(start: datetime, end: datetime) -> int:
    """Calendar month difference (floor); end must be >= start."""
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return max(0, months)


def tenure_months_between(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    if end < start:
        return None
    months = floor_calendar_month_diff(start, end)
    return max(1, months)


def gap_months_between(older_end: datetime, newer_start: datetime) -> int:
    if older_end >= newer_start:
        return 0
    return floor_calendar_month_diff(older_end, newer_start)


def format_es_date(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.strftime("%Y-%m-%d")


def work_row_company(row: dict[str, Any]) -> str | None:
    company = row.get("company") or row.get("company_name")
    if company is None:
        return None
    text = str(company).strip()
    return text or None


def work_row_job(row: dict[str, Any]) -> str | None:
    job = row.get("job") or row.get("job_title")
    if job is None:
        return None
    text = str(job).strip()
    return text or None


__all__ = [
    "floor_calendar_month_diff",
    "format_es_date",
    "gap_months_between",
    "is_current_marker",
    "parse_date_like",
    "parse_date_range_text",
    "row_date_candidates",
    "tenure_months_between",
    "work_row_company",
    "work_row_job",
    "work_row_start_end",
]
