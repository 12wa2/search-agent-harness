"""Offline recruiting catalogs used by the Hybrid V2 compiler.

The public engine deliberately ships deterministic defaults instead of calling
a hosted dictionary service. Deployments that already maintain their own
salary or work-state dictionaries can provide JSON files through environment
variables; no provider URL or credential is built into the package.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SalaryBand:
    """One salary bucket stored as ``intention.expected_salary_code``."""

    code: int
    name: str
    min_salary: int
    max_salary: int


_DEFAULT_SALARY_BANDS: tuple[tuple[int, str, int, int], ...] = (
    (1, "2k以下", 0, 2_000),
    (2, "2k-4k", 2_000, 4_000),
    (3, "4k-6k", 4_000, 6_000),
    (4, "6k-8k", 6_000, 8_000),
    (5, "8k-10k", 8_000, 10_000),
    (6, "10k-15k", 10_000, 15_000),
    (7, "15k-25k", 15_000, 25_000),
    (8, "25k-35k", 25_000, 35_000),
    (9, "35k-50k", 35_000, 50_000),
    (10, "50k以上", 50_000, 10**9),
)

_DEFAULT_WORK_STATES: tuple[tuple[str, str], ...] = (
    ("1", "在职，看看新机会"),
    ("2", "在职，急寻新工作"),
    ("3", "在职，暂无跳槽打算"),
    ("4", "离职，正在找工作"),
    ("5", "应届毕业生"),
)

_WORK_STATE_ALIASES: dict[str, tuple[str, ...]] = {
    "在职": ("1", "2", "3"),
    "在职看机会": ("1",),
    "在职，看看新机会": ("1",),
    "在职，急寻新工作": ("2",),
    "在职，暂无跳槽打算": ("3",),
    "离职": ("4",),
    "离职，正在找工作": ("4",),
    "求职": ("4",),
    "应届": ("5",),
    "应届生": ("5",),
    "应届毕业生": ("5",),
    "校招": ("5",),
}


def _to_salary_int(value: str, unit: str | None) -> int | None:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return None
    normalized_unit = (unit or "").lower()
    if normalized_unit == "k":
        return int(round(amount * 1_000))
    if normalized_unit in {"w", "万"}:
        return int(round(amount * 10_000))
    if amount <= 1_000:
        return int(round(amount * 1_000))
    return int(round(amount))


def _parse_salary_band(name: str, code: int) -> SalaryBand | None:
    text = str(name or "").lower().replace(" ", "")
    range_match = re.search(
        r"(\d+(?:\.\d+)?)(k|w|万)?[-~至到](\d+(?:\.\d+)?)(k|w|万)?",
        text,
    )
    if range_match:
        low = _to_salary_int(range_match.group(1), range_match.group(2))
        high = _to_salary_int(range_match.group(3), range_match.group(4))
        if low is not None and high is not None:
            return SalaryBand(
                code=code, name=name, min_salary=min(low, high), max_salary=max(low, high)
            )

    upper_match = re.search(
        r"(?:不高于|不超过|低于)?(\d+(?:\.\d+)?)(k|w|万)?(?:以下|以内|及以下)?$",
        text,
    )
    if upper_match and ("以下" in text or "以内" in text):
        high = _to_salary_int(upper_match.group(1), upper_match.group(2))
        if high is not None:
            return SalaryBand(code=code, name=name, min_salary=0, max_salary=high)

    lower_match = re.search(
        r"(?:高于|不低于|不少于|至少)?(\d+(?:\.\d+)?)(k|w|万)?(?:以上|及以上|起)?$",
        text,
    )
    if lower_match and ("以上" in text or "起" in text):
        low = _to_salary_int(lower_match.group(1), lower_match.group(2))
        if low is not None:
            return SalaryBand(code=code, name=name, min_salary=low, max_salary=10**9)
    return None


def _load_json_rows(path: str) -> list[dict[str, Any]]:
    target = Path(path).expanduser()
    if not target.is_file():
        raise FileNotFoundError(f"catalog file not found: {target}")
    raw = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("items") or raw.get("bands") or raw.get("work_states") or []
    if not isinstance(raw, list):
        raise ValueError(f"catalog file must contain a JSON list: {target}")
    return [row for row in raw if isinstance(row, dict)]


@lru_cache(maxsize=8)
def _load_salary_bands(path: str) -> tuple[SalaryBand, ...]:
    if not path:
        return tuple(
            SalaryBand(code=code, name=name, min_salary=low, max_salary=high)
            for code, name, low, high in _DEFAULT_SALARY_BANDS
        )

    bands: list[SalaryBand] = []
    for row in _load_json_rows(path):
        try:
            code = int(row.get("code"))
        except (TypeError, ValueError):
            continue
        name = str(row.get("name") or row.get("label") or "").strip()
        if not name:
            continue
        low_raw = row.get("min_salary", row.get("minSalary"))
        high_raw = row.get("max_salary", row.get("maxSalary"))
        if low_raw is not None and high_raw is not None:
            try:
                low, high = int(low_raw), int(high_raw)
            except (TypeError, ValueError):
                continue
            bands.append(SalaryBand(code=code, name=name, min_salary=low, max_salary=high))
            continue
        parsed = _parse_salary_band(name, code)
        if parsed is not None:
            bands.append(parsed)
    return tuple(sorted(bands, key=lambda band: (band.min_salary, band.code)))


def get_salary_bands() -> list[SalaryBand]:
    """Return salary buckets, optionally overriding them with a local JSON file."""

    path = os.environ.get("SEARCH_AGENT_HYBRID_V2_SALARY_BANDS", "").strip()
    bands = _load_salary_bands(path)
    if not bands:
        raise RuntimeError("salary catalog is empty; check SEARCH_AGENT_HYBRID_V2_SALARY_BANDS")
    return list(bands)


def _parse_salary_expr(text: str) -> tuple[str, int, int | None] | None:
    normalized = str(text or "").lower().replace(" ", "")
    if not normalized:
        return None

    range_match = re.search(
        r"(\d+(?:\.\d+)?)(k|w|万)?[-~至到](\d+(?:\.\d+)?)(k|w|万)?",
        normalized,
    )
    if range_match:
        low = _to_salary_int(range_match.group(1), range_match.group(2))
        high = _to_salary_int(range_match.group(3), range_match.group(4))
        if low is not None and high is not None:
            return ("range", min(low, high), max(low, high))

    upper_match = re.search(
        r"(?:不高于|不超过|低于)(\d+(?:\.\d+)?)(k|w|万)?|"
        r"(\d+(?:\.\d+)?)(k|w|万)?(?:以内|以下|及以下)",
        normalized,
    )
    if upper_match:
        high = _to_salary_int(
            upper_match.group(1) or upper_match.group(3),
            upper_match.group(2) or upper_match.group(4),
        )
        if high is not None:
            return ("lte", high, None)

    lower_match = re.search(
        r"(?:高于|不低于|不少于|至少)(\d+(?:\.\d+)?)(k|w|万)?|"
        r"(\d+(?:\.\d+)?)(k|w|万)?(?:以上|及以上|起)",
        normalized,
    )
    if lower_match:
        low = _to_salary_int(
            lower_match.group(1) or lower_match.group(3),
            lower_match.group(2) or lower_match.group(4),
        )
        if low is not None:
            return ("gte", low, None)
    return None


def resolve_salary_codes_strict(text: str) -> list[int]:
    """Map an upper/lower-bound expression to fully contained salary buckets."""

    parsed = _parse_salary_expr(text)
    if not parsed or parsed[0] == "range":
        return []
    mode, threshold, _ = parsed
    out: list[int] = []
    for band in get_salary_bands():
        if mode == "lte" and band.max_salary <= threshold:
            out.append(band.code)
        elif mode == "gte" and band.min_salary >= threshold:
            out.append(band.code)
    return sorted(set(out))


def resolve_salary_codes_range_overlap(text: str) -> list[int]:
    """Map a range expression to every salary bucket that overlaps it."""

    parsed = _parse_salary_expr(text)
    if not parsed or parsed[0] != "range" or parsed[2] is None:
        return []
    _, low, high = parsed
    return sorted(
        band.code
        for band in get_salary_bands()
        if band.min_salary <= high and band.max_salary >= low
    )


@lru_cache(maxsize=8)
def _load_work_states(path: str) -> tuple[tuple[str, str], ...]:
    if not path:
        return _DEFAULT_WORK_STATES
    rows: list[tuple[str, str]] = []
    for row in _load_json_rows(path):
        code = str(row.get("code") or row.get("id") or "").strip()
        name = str(row.get("name") or row.get("label") or "").strip()
        if code and name:
            rows.append((code, name))
    return tuple(rows)


def get_work_state_catalog() -> list[tuple[str, str]]:
    """Return ``(code, name)`` pairs from built-ins or a local JSON override."""

    path = os.environ.get("SEARCH_AGENT_HYBRID_V2_WORK_STATES", "").strip()
    return list(_load_work_states(path))


def _alias_codes(text: str) -> set[str]:
    key = str(text or "").strip().casefold()
    if not key:
        return set()
    for alias, codes in _WORK_STATE_ALIASES.items():
        if key == alias.casefold():
            return set(codes)
    if len(key) < 2:
        return set()
    matched: set[str] = set()
    for alias, codes in _WORK_STATE_ALIASES.items():
        if len(alias) >= 2 and alias.casefold() in key:
            matched.update(codes)
    return matched


def resolve_work_state_codes(values: list[str]) -> list[str]:
    """Resolve dictionary names, short aliases, or explicit numeric codes."""

    catalog = get_work_state_catalog()
    matched: set[str] = set()
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        if text.isdigit():
            matched.add(text)
            continue
        normalized = text.casefold()
        exact = {code for code, name in catalog if name.casefold() == normalized}
        if exact:
            matched.update(exact)
            continue
        matched.update(_alias_codes(text))
    return sorted(matched)


def clear_catalog_caches() -> None:
    """Clear file-backed caches after tests or runtime reconfiguration."""

    _load_salary_bands.cache_clear()
    _load_work_states.cache_clear()


__all__ = [
    "SalaryBand",
    "clear_catalog_caches",
    "get_salary_bands",
    "get_work_state_catalog",
    "resolve_salary_codes_range_overlap",
    "resolve_salary_codes_strict",
    "resolve_work_state_codes",
]
