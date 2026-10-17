"""Configurable thresholds for work_stats compiler rules."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

WORK_STATS_SCHEMA_VERSION = 2
RECENT_TENURE_K = 3
SHORT_JOB_MONTHS = 12
GAP_APPROX_TOLERANCE_MONTHS = 6

_ROLE_TENURE_TITLE_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class StabilityStrongConfig:
    recent_tenure_1_min_months: int = 24
    recent_tenure_2_min_months: int = 24
    avg_tenure_min_months: int = 12
    max_short_job_count: int | None = None


@dataclass(frozen=True)
class CareerDiscontinuityConfig:
    trailing_gap_min_months: int = 6
    max_inter_segment_gap_min_months: int = 6
    inter_segment_gap_count_gt_6m_min: int = 1


STABILITY_STRONG_V1 = StabilityStrongConfig()
CAREER_DISCONTINUITY_V1 = CareerDiscontinuityConfig()

# §8.2 acceptance defaults (configurable)
MAX_TENURE_STRONG_MIN_MONTHS = 36
AVG_TENURE_LONG_MIN_MONTHS = 24
SHORT_JOB_FREQUENT_MIN = 2

# Preset trailing gap windows: name -> (gte_months_back, lte_months_back) relative to now.
EMPLOYMENT_GAP_PRESETS: dict[str, tuple[int | None, int | None]] = {
    "trailing_le_6m": (None, 6),
    "trailing_gt_6m": (6, None),
    "trailing_gt_12m": (12, None),
    "trailing_le_3m": (None, 3),
    "approx_12m": (14, 10),
    "approx_24m": (26, 22),
}

# Preset name -> inverted preset for ``operator=not`` on employment_gap.
EMPLOYMENT_GAP_PRESET_NEGATIONS: dict[str, str] = {
    "trailing_le_6m": "trailing_gt_6m",
    "trailing_gt_6m": "trailing_le_6m",
    "trailing_gt_12m": "trailing_le_6m",
    "trailing_le_3m": "trailing_gt_6m",
    "approx_12m": "trailing_gt_6m",
    "approx_24m": "trailing_gt_12m",
}


def normalize_role_tenure_title(title: str | None) -> str:
    """Return a light canonical job/title key for role_tenure.

    This intentionally only removes surface noise such as surrounding whitespace,
    internal whitespace, and width/case variants. It does not collapse titles by
    job family or synonym dictionary.
    """
    text = unicodedata.normalize("NFKC", str(title or "")).strip()
    if not text:
        return ""
    text = _ROLE_TENURE_TITLE_WHITESPACE_RE.sub("", text)
    return text.casefold()


def resolve_role_tenure_titles(role_query: str) -> list[str]:
    """Expand query title via shared position expander; normalize for sidecar keys.

    Single-bucket semantics: any expanded title whose sidecar tenure meets threshold.
    """
    from .position_titles import expand_position_titles

    text = str(role_query or "").strip()
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for term in expand_position_titles(text):
        key = normalize_role_tenure_title(term)
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    if not out:
        key = normalize_role_tenure_title(text)
        if key:
            out.append(key)
    return out
