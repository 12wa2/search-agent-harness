"""Programmatic hard-match for work_stats IR fields (rerank pre-rank / hardMatches)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from search_agent.models.api import CandidateItem

from .work_experience_dates import (
    floor_calendar_month_diff,
    parse_date_like,
)
from .work_stats_config import (
    AVG_TENURE_LONG_MIN_MONTHS,
    CAREER_DISCONTINUITY_V1,
    EMPLOYMENT_GAP_PRESETS,
    GAP_APPROX_TOLERANCE_MONTHS,
    MAX_TENURE_STRONG_MIN_MONTHS,
    SHORT_JOB_FREQUENT_MIN,
    STABILITY_STRONG_V1,
    normalize_role_tenure_title,
    resolve_role_tenure_titles,
)
from .work_time_facts import resolve_candidate_work_stats

WORK_STATS_FIELD_LABELS: dict[str, str] = {
    "work_segment_count": "工作段数",
    "employment_gap": "尾部空窗(月)",
    "latest_company": "最近一段公司",
    "latest_work_job": "最近一段职位",
    "recent_tenure_1": "最近一段任职(月)",
    "max_tenure": "最长单段司龄(月)",
    "avg_tenure": "平均司龄(月)",
    "short_job_count": "短职次数",
    "job_stability": "稳定性强",
    "inter_segment_gap": "段间>6月空档次数",
    "max_inter_segment_gap": "段间最大空窗(月)",
    "career_discontinuity": "履历断层",
    "role_tenure": "职位累计任职(月)",
}

WORK_STATS_HARD_FIELDS: frozenset[str] = frozenset(
    {
        "work_segment_count",
        "employment_gap",
        "latest_company",
        "latest_work_job",
        "recent_tenure_1",
        "max_tenure",
        "avg_tenure",
        "short_job_count",
        "job_stability",
        "inter_segment_gap",
        "max_inter_segment_gap",
        "career_discontinuity",
        "role_tenure",
    }
)


def _num(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _trailing_gap_months(stats: dict[str, Any], *, current_time: datetime) -> int | None:
    if stats.get("latest_work_is_current"):
        return 0
    end = parse_date_like(stats.get("latest_work_end"), current_time=current_time)
    if end is None:
        return None
    if end > current_time:
        return 0
    return floor_calendar_month_diff(end, current_time)


def _match_gap_preset(stats: dict[str, Any], preset: str, *, current_time: datetime) -> bool:
    gap = _trailing_gap_months(stats, current_time=current_time)
    if preset == "trailing_le_6m":
        return gap is not None and gap <= 6
    if preset == "trailing_le_3m":
        return gap is not None and gap <= 3
    if preset == "trailing_gt_6m":
        return gap is not None and gap > 6
    if preset == "trailing_gt_12m":
        return gap is not None and gap > 12
    if preset in {"approx_12m", "approx_24m"}:
        center = 12 if preset == "approx_12m" else 24
        if gap is None:
            return False
        return abs(gap - center) <= GAP_APPROX_TOLERANCE_MONTHS
    lower, upper = EMPLOYMENT_GAP_PRESETS.get(preset, (None, None))
    if gap is None:
        return False
    if lower is not None and gap < lower:
        return False
    if upper is not None and gap > upper:
        return False
    return lower is not None or upper is not None


def _match_job_stability_strong(stats: dict[str, Any]) -> bool:
    cfg = STABILITY_STRONG_V1
    segment_count = _num(stats.get("segment_count"))
    recent_1 = _num(stats.get("recent_tenure_1_months"))
    recent_2 = _num(stats.get("recent_tenure_2_months"))
    avg_tenure = _num(stats.get("avg_tenure_months"))
    short_jobs = _num(stats.get("short_job_count"))
    if segment_count is None or recent_1 is None or avg_tenure is None:
        return False
    if (
        cfg.max_short_job_count is not None
        and short_jobs is not None
        and short_jobs > cfg.max_short_job_count
    ):
        return False
    if segment_count == 1:
        return (
            recent_1 >= cfg.recent_tenure_1_min_months and avg_tenure >= cfg.avg_tenure_min_months
        )
    if segment_count >= 2:
        if recent_2 is None:
            return False
        return (
            recent_1 >= cfg.recent_tenure_1_min_months
            and recent_2 >= cfg.recent_tenure_2_min_months
            and avg_tenure >= cfg.avg_tenure_min_months
        )
    return False


def _match_career_discontinuity(stats: dict[str, Any], *, current_time: datetime) -> bool:
    cfg = CAREER_DISCONTINUITY_V1
    gap = _trailing_gap_months(stats, current_time=current_time)
    if (
        gap is not None
        and not stats.get("latest_work_is_current")
        and gap >= cfg.trailing_gap_min_months
    ):
        return True
    max_inter = _num(stats.get("max_inter_segment_gap_months"))
    if max_inter is not None and max_inter >= cfg.max_inter_segment_gap_min_months:
        return True
    count_gt_6 = _num(stats.get("inter_segment_gap_count_gt_6m"))
    if count_gt_6 is not None and count_gt_6 >= cfg.inter_segment_gap_count_gt_6m_min:
        return True
    return False


def _role_title_phrase_matches(row_role: str, expanded_title: str) -> bool:
    """Align with ES ``match_phrase`` on ``work_stats.role_tenure.role``."""
    if not row_role or not expanded_title:
        return False
    return row_role == expanded_title or expanded_title in row_role


def _role_tenure_best_bucket(
    stats: dict[str, Any],
    expanded_titles: list[str],
) -> tuple[int, str] | None:
    """Best single-bucket tenure among sidecar rows matching any expanded title."""
    rows = stats.get("role_tenure")
    if not isinstance(rows, list) or not expanded_titles:
        return None
    best_months: int | None = None
    best_role = ""
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_role = normalize_role_tenure_title(row.get("role"))
        if not row_role:
            continue
        if not any(_role_title_phrase_matches(row_role, title) for title in expanded_titles):
            continue
        tenure = _num(row.get("tenure_months")) or _num(row.get("tenureMonths"))
        if tenure is None:
            continue
        if best_months is None or tenure > best_months:
            best_months = tenure
            best_role = str(row.get("role") or row_role).strip() or row_role
    if best_months is None:
        return None
    return best_months, best_role


def _role_tenure_months(stats: dict[str, Any], role_query: str) -> int | None:
    expanded = resolve_role_tenure_titles(role_query)
    best = _role_tenure_best_bucket(stats, expanded)
    return best[0] if best is not None else None


def work_stats_field_values(
    item: CandidateItem,
    field: str,
    *,
    current_time: datetime | None = None,
) -> list[str | int | float]:
    """Scalar actuals for rerank hardMatches (aligned with match_work_stats_hard_condition)."""
    current_time = current_time or datetime.now().astimezone()
    stats = resolve_candidate_work_stats(item, current_time=current_time)
    if not stats:
        return []
    if field == "work_segment_count":
        value = _num(stats.get("segment_count"))
        return [value] if value is not None else []
    if field == "latest_company":
        company = str(stats.get("latest_work_company") or "").strip()
        return [company] if company else []
    if field == "latest_work_job":
        job = str(stats.get("latest_work_job") or "").strip()
        return [job] if job else []
    if field == "recent_tenure_1":
        value = _num(stats.get("recent_tenure_1_months"))
        return [value] if value is not None else []
    if field == "max_tenure":
        value = _num(stats.get("max_tenure_months"))
        return [value] if value is not None else []
    if field == "avg_tenure":
        value = _num(stats.get("avg_tenure_months"))
        return [value] if value is not None else []
    if field == "short_job_count":
        value = _num(stats.get("short_job_count"))
        return [value] if value is not None else []
    if field == "max_inter_segment_gap":
        value = _num(stats.get("max_inter_segment_gap_months"))
        return [value] if value is not None else []
    if field == "inter_segment_gap":
        value = _num(stats.get("inter_segment_gap_count_gt_6m"))
        return [value] if value is not None else []
    if field == "employment_gap":
        gap = _trailing_gap_months(stats, current_time=current_time)
        return [gap if gap is not None else 0]
    if field == "job_stability":
        return ["strong"] if _match_job_stability_strong(stats) else []
    if field == "career_discontinuity":
        return [True] if _match_career_discontinuity(stats, current_time=current_time) else []
    if field == "role_tenure":
        rows = stats.get("role_tenure")
        if not isinstance(rows, list):
            return []
        out: list[str | int | float] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            role = normalize_role_tenure_title(row.get("role"))
            tenure = _num(row.get("tenure_months"))
            if role and tenure is not None:
                out.extend([tenure, role])
        return out
    return []


def _match_state_prefix(matched: bool) -> str:
    return "满足" if matched else "不满足"


_MONTH_COUNT_IN_TEXT_RE = re.compile(r"(约)?(\d+)个月")


def format_duration_months_phrase(value: Any) -> str:
    """Product-facing duration: <12 → X个月; ≥12 → X年 or X年Y个月 (rule B)."""
    months = _num(value)
    if months is None:
        return str(value)
    if months < 0:
        months = 0
    if months < 12:
        return f"{months}个月"
    years = months // 12
    rem = months % 12
    if rem == 0:
        return f"{years}年"
    return f"{years}年{rem}个月"


def _format_months_phrase(value: Any) -> str:
    return format_duration_months_phrase(value)


def _format_months_range_phrase(lo: int, hi: int) -> str:
    return f"{_format_months_phrase(lo)}–{_format_months_phrase(hi)}"


def humanize_month_count_in_text(text: str) -> str:
    """Rewrite ≥12-month counts in user-facing copy (e.g. summary) to 年/年+月."""

    def _replace(match: re.Match[str]) -> str:
        prefix = match.group(1) or ""
        months = int(match.group(2))
        if months < 12:
            return match.group(0)
        phrase = _format_months_phrase(months)
        if prefix == "约":
            return f"约{phrase}"
        return phrase if not prefix else f"{prefix}{phrase}"

    return _MONTH_COUNT_IN_TEXT_RE.sub(_replace, str(text or ""))


def _describe_count_threshold(
    *,
    subject: str,
    operator: str,
    threshold: Any,
    unit: str,
) -> str:
    op = str(operator or "eq")
    if op in {"gte", "any"}:
        return f"{subject}不少于{threshold}{unit}"
    if op == "lte":
        return f"{subject}不超过{threshold}{unit}"
    if op == "eq":
        return f"{subject}为{threshold}{unit}"
    return f"{subject}{threshold}{unit}"


def _describe_months_threshold(*, subject: str, operator: str, threshold: Any) -> str:
    threshold_phrase = _format_months_phrase(threshold)
    op = str(operator or "eq")
    if op in {"gte", "any"}:
        return f"{subject}不少于{threshold_phrase}"
    if op == "lte":
        return f"{subject}不超过{threshold_phrase}"
    if op == "eq":
        return f"{subject}为{threshold_phrase}"
    return f"{subject}{threshold_phrase}"


def describe_work_stats_hard_match(
    *,
    field: str,
    operator: str,
    expected_values: list[Any],
    matched: bool,
    matched_values: list[Any],
    soft_text: str | None = None,
) -> str:
    """Human-readable hint for LLM rerank reasons (product-facing, not ES jargon)."""
    label = WORK_STATS_FIELD_LABELS.get(field, field)
    op = str(operator or "eq")
    if field == "employment_gap":
        preset = str(expected_values[0]).strip() if len(expected_values) == 1 else ""
        preset_hint = {
            "approx_12m": f"约{_format_months_range_phrase(10, 14)}",
            "approx_24m": f"约{_format_months_range_phrase(22, 26)}",
            "trailing_gt_6m": "超过6个月",
            "trailing_gt_12m": "超过1年",
            "trailing_le_6m": "不超过6个月",
        }.get(preset, "")
        if preset_hint:
            expect = f"尾部空窗{preset_hint}"
        elif op == "gte" and expected_values:
            expect = _describe_months_threshold(
                subject="尾部空窗",
                operator=op,
                threshold=expected_values[0],
            )
        elif op == "lte" and any(str(v).strip() == "0" for v in expected_values):
            expect = "当前在职或空窗为0"
        else:
            expect = _describe_months_threshold(
                subject="尾部空窗",
                operator=op,
                threshold=expected_values[0] if expected_values else "?",
            )
        actual = matched_values[0] if matched_values else "未知"
        state = _match_state_prefix(matched)
        return f"{state}{expect}；实际约{_format_months_phrase(actual)}"
    if field == "role_tenure":
        role = (soft_text or "").strip() or (
            str(expected_values[1]).strip() if len(expected_values) > 1 else "岗位"
        )
        min_months = expected_values[0] if expected_values else "?"
        actual = matched_values[0] if matched_values else "未知"
        state = _match_state_prefix(matched)
        expect = _describe_months_threshold(
            subject=f"{role}累计任职",
            operator=op,
            threshold=min_months,
        )
        matched_title = str(matched_values[1]).strip() if len(matched_values) > 1 else ""
        title_suffix = f"（{matched_title}）" if matched and matched_title else ""
        return f"{state}{expect}；实际约{_format_months_phrase(actual)}{title_suffix}"
    if field == "work_segment_count":
        threshold = expected_values[0] if expected_values else "?"
        actual = matched_values[0] if matched_values else "未知"
        state = _match_state_prefix(matched)
        expect = _describe_count_threshold(
            subject="工作经历",
            operator=op,
            threshold=threshold,
            unit="段",
        )
        return f"{state}{expect}；实际{actual}段"
    if field == "recent_tenure_1":
        threshold = expected_values[0] if expected_values else "?"
        actual = matched_values[0] if matched_values else "未知"
        state = _match_state_prefix(matched)
        expect = _describe_months_threshold(
            subject="最近一段任职",
            operator=op,
            threshold=threshold,
        )
        return f"{state}{expect}；实际约{_format_months_phrase(actual)}"
    if field == "max_tenure":
        threshold = expected_values[0] if expected_values else "?"
        actual = matched_values[0] if matched_values else "未知"
        state = _match_state_prefix(matched)
        expect = _describe_months_threshold(
            subject="最长单段司龄",
            operator=op,
            threshold=threshold,
        )
        return f"{state}{expect}；实际约{_format_months_phrase(actual)}"
    if field == "avg_tenure":
        threshold = expected_values[0] if expected_values else "?"
        actual = matched_values[0] if matched_values else "未知"
        state = _match_state_prefix(matched)
        expect = _describe_months_threshold(
            subject="平均司龄",
            operator=op,
            threshold=threshold,
        )
        return f"{state}{expect}；实际约{_format_months_phrase(actual)}"
    if field == "short_job_count":
        threshold = expected_values[0] if expected_values else "?"
        actual = matched_values[0] if matched_values else "未知"
        state = _match_state_prefix(matched)
        expect = _describe_count_threshold(
            subject="短职次数",
            operator=op,
            threshold=threshold,
            unit="次",
        )
        return f"{state}{expect}；实际{actual}次"
    if field == "inter_segment_gap":
        threshold = expected_values[0] if expected_values else "?"
        actual = matched_values[0] if matched_values else "未知"
        state = _match_state_prefix(matched)
        expect = _describe_count_threshold(
            subject="段间超过6月的空档",
            operator=op,
            threshold=threshold,
            unit="次",
        )
        return f"{state}{expect}；实际{actual}次"
    if field == "max_inter_segment_gap":
        threshold = expected_values[0] if expected_values else "?"
        actual = matched_values[0] if matched_values else "未知"
        state = _match_state_prefix(matched)
        expect = _describe_months_threshold(
            subject="段间最大空窗",
            operator=op,
            threshold=threshold,
        )
        return f"{state}{expect}；实际约{_format_months_phrase(actual)}"
    if field == "latest_company":
        expect = " / ".join(str(v) for v in expected_values if str(v).strip()) or "目标公司"
        actual = matched_values[0] if matched_values else "未知"
        state = _match_state_prefix(matched)
        return f"{state}最近一段公司为「{expect}」；实际「{actual}」"
    if field == "latest_work_job":
        expect = " / ".join(str(v) for v in expected_values if str(v).strip()) or "目标职位"
        actual = matched_values[0] if matched_values else "未知"
        state = _match_state_prefix(matched)
        return f"{state}最近一段职位含「{expect}」；实际「{actual}」"
    if field == "job_stability":
        state = _match_state_prefix(matched)
        return f"{state}稳定性强（最近段司龄与平均司龄达标）"
    if field == "career_discontinuity":
        state = _match_state_prefix(matched)
        return f"{state}存在履历断层（尾部或段间大空窗）"
    threshold = expected_values[0] if expected_values else "?"
    actual = matched_values[0] if matched_values else "未知"
    state = _match_state_prefix(matched)
    return f"{state}{label}为{threshold}；实际{actual}"


def match_work_stats_hard_condition(
    item: CandidateItem,
    *,
    field: str,
    operator: str,
    values: list[Any],
    soft_text: str | None = None,
    negated: bool = False,
    current_time: datetime | None = None,
) -> tuple[bool, list[Any]]:
    current_time = current_time or datetime.now().astimezone()
    stats = resolve_candidate_work_stats(item, current_time=current_time)
    if not stats:
        matched = False
        return (not matched) if negated else matched, []

    op = str(operator or "eq")
    matched = False
    matched_values: list[Any] = []

    if field == "work_segment_count":
        actual = _num(stats.get("segment_count"))
        threshold = _num(values[0]) if values else None
        if actual is not None and threshold is not None:
            if op in {"gte", "any"}:
                matched = actual >= threshold
            elif op == "lte":
                matched = actual <= threshold
            elif op == "eq":
                matched = actual == threshold
            if matched:
                matched_values = [actual]

    elif field == "latest_company":
        company = str(stats.get("latest_work_company") or "").strip()
        expected = [str(v).strip() for v in values if str(v).strip()]
        if company and expected:
            if op in {"eq", "any", "all"}:
                matched = any(company == e or e in company or company in e for e in expected)
            if matched:
                matched_values = [company]

    elif field == "latest_work_job":
        job = str(stats.get("latest_work_job") or "").strip()
        expected = [str(v).strip() for v in values if str(v).strip()]
        if job and expected:
            if op in {"eq", "any", "all"}:
                matched = any(job == e or e in job or job in e for e in expected)
            if matched:
                matched_values = [job]

    elif field == "recent_tenure_1":
        actual = _num(stats.get("recent_tenure_1_months"))
        threshold = _num(values[0]) if values else None
        if actual is not None and threshold is not None:
            if op in {"gte", "any"}:
                matched = actual >= threshold
            elif op == "lte":
                matched = actual <= threshold
            elif op == "eq":
                matched = actual == threshold
            if matched:
                matched_values = [actual]

    elif field == "max_tenure":
        actual = _num(stats.get("max_tenure_months"))
        threshold = _num(values[0]) if values else MAX_TENURE_STRONG_MIN_MONTHS
        if actual is not None and threshold is not None:
            if op in {"gte", "any"}:
                matched = actual >= threshold
            elif op == "lte":
                matched = actual <= threshold
            elif op == "eq":
                matched = actual == threshold
            if matched:
                matched_values = [actual]

    elif field == "avg_tenure":
        actual = _num(stats.get("avg_tenure_months"))
        threshold = _num(values[0]) if values else AVG_TENURE_LONG_MIN_MONTHS
        if actual is not None and threshold is not None:
            if op in {"gte", "any"}:
                matched = actual >= threshold
            elif op == "lte":
                matched = actual <= threshold
            elif op == "eq":
                matched = actual == threshold
            if matched:
                matched_values = [actual]

    elif field == "short_job_count":
        actual = _num(stats.get("short_job_count"))
        threshold = _num(values[0]) if values else SHORT_JOB_FREQUENT_MIN
        if actual is not None and threshold is not None:
            if op in {"gte", "any"}:
                matched = actual >= threshold
            elif op == "lte":
                matched = actual <= threshold
            elif op == "eq":
                matched = actual == threshold
            if matched:
                matched_values = [actual]

    elif field == "max_inter_segment_gap":
        actual = _num(stats.get("max_inter_segment_gap_months"))
        threshold = _num(values[0]) if values else None
        if actual is not None and threshold is not None:
            if op in {"gte", "any"}:
                matched = actual >= threshold
            elif op == "lte":
                matched = actual <= threshold
            elif op == "eq":
                matched = actual == threshold
            if matched:
                matched_values = [actual]

    elif field == "employment_gap":
        preset = str(values[0]).strip() if len(values) == 1 and isinstance(values[0], str) else ""
        if preset in EMPLOYMENT_GAP_PRESETS:
            matched = _match_gap_preset(stats, preset, current_time=current_time)
            gap = _trailing_gap_months(stats, current_time=current_time)
            if gap is not None:
                matched_values = [gap]
        elif op == "lte" and any(str(v).strip() == "0" for v in values):
            gap = _trailing_gap_months(stats, current_time=current_time)
            matched = bool(stats.get("latest_work_is_current")) or (gap is not None and gap <= 0)
            matched_values = [gap if gap is not None else 0] if matched else []
        else:
            month_vals = [_num(v) for v in values]
            month_vals = [v for v in month_vals if v is not None]
            gap = _trailing_gap_months(stats, current_time=current_time)
            if month_vals:
                threshold = month_vals[0]
                if op == "gte":
                    matched = gap is not None and gap >= threshold
                elif op == "lte":
                    matched = bool(stats.get("latest_work_is_current")) or (
                        gap is not None and gap <= threshold
                    )
                elif op == "eq":
                    matched = (
                        gap is not None and abs(gap - threshold) <= GAP_APPROX_TOLERANCE_MONTHS
                    )
                if matched:
                    matched_values = [gap if gap is not None else 0]

    elif field == "job_stability":
        token = str(values[0]).strip().lower() if values else "strong"
        if token in {"strong", "strong_v1", "稳定性强", "稳定性高"}:
            matched = _match_job_stability_strong(stats)
            if matched:
                matched_values = ["strong"]

    elif field == "inter_segment_gap":
        actual = _num(stats.get("inter_segment_gap_count_gt_6m"))
        threshold = _num(values[0]) if values else 2
        if actual is not None and threshold is not None:
            if op in {"gte", "any"}:
                matched = actual >= threshold
            elif op == "lte":
                matched = actual <= threshold
            elif op == "eq":
                matched = actual == threshold
            if matched:
                matched_values = [actual]

    elif field == "career_discontinuity":
        matched = _match_career_discontinuity(stats, current_time=current_time)
        if matched:
            matched_values = [True]

    elif field == "role_tenure":
        month_vals = [_num(v) for v in values]
        month_vals = [v for v in month_vals if v is not None]
        min_months = month_vals[0] if month_vals else None
        role_query = (soft_text or "").strip()
        if not role_query and len(values) > 1:
            role_query = str(values[1]).strip()
        expanded = resolve_role_tenure_titles(role_query)
        if min_months is not None and expanded:
            best = _role_tenure_best_bucket(stats, expanded)
            if best is not None and best[0] >= min_months:
                matched = True
                matched_values = [best[0], best[1]]

    if op == "not":
        matched = not matched
        matched_values = []

    if negated:
        matched = not matched
        if not matched:
            matched_values = []

    return matched, matched_values


__all__ = [
    "WORK_STATS_FIELD_LABELS",
    "WORK_STATS_HARD_FIELDS",
    "describe_work_stats_hard_match",
    "format_duration_months_phrase",
    "humanize_month_count_in_text",
    "match_work_stats_hard_condition",
    "work_stats_field_values",
]
