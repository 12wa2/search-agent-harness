"""Lightweight intent helpers for work tenure queries.

Separates generic tenure (``work_years``) from title tenure (``role_tenure``).
Title slots accept job/position phrases only; industry/domain modifiers are
split out and must not be compiled into ``role_tenure.softText``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from .work_stats_config import normalize_role_tenure_title

WorkTenureIntent = Literal["generic", "title", "recent"]

_RECENT_TENURE_RE = re.compile(
    r"最近(?:一份|一段|一次)?(?:工作)?.{0,8}(?:任职|在职|工作).{0,8}"
    r"(?:超过|不少于|至少|≥|>=?)?\s*([一二两三四五六七八九十\d]+)\s*年"
)
_TITLE_YEARS_FIRST_RE = re.compile(
    r"(?<![工作年限])"
    r"([一二两三四五六七八九十\d]+)\s*年(?:及)?以上\s*([^，,。；;\s但且和]{2,}?)(?:经验|工作经历|从业经历)|"
    r"(?<![工作年限])"
    r"([一二两三四五六七八九十\d]+)\s*年(?:及)?以上\s*([^，,。；;\s但且和]{2,}?)(?:工作)?经历|"
    r"([一二两三四五六七八九十\d]+)\s*年(?:及)?以上\s*([^，,。；;\s但且和]{2,}?)(?:工作)?经验"
)
_TITLE_CUMULATIVE_RE = re.compile(
    r"累计\s*([^，,。；;\s但且和]{2,}?)(?:经验|工作经历|从业经历)(?:≥|>=|不少于|至少|超过)?\s*([一二两三四五六七八九十\d]+)\s*年"
)

_DOMAIN_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^(.+?)领域的(.+)$"),
    re.compile(r"^(.+?)领域(.+)$"),
    re.compile(r"^(.+?)行业的(.+)$"),
    re.compile(r"^(.+?)行业(.+)$"),
)
_DOMAIN_ONLY_RE = re.compile(r"^(.+?)(?:领域|行业)$")

# Only the obviously capability / management style phrases below are treated as
# "generic tenure" blockers. Everything else is left available for title tenure.
_GENERIC_TENURE_BLOCKERS = (
    "团队管理",
    "带队",
    "领导力",
    "管理",
    "管理经验",
    "管理能力",
    "项目管理",
    "业务经验",
    "行业经验",
    "客户管理",
    "组织协调",
    "沟通协调",
    "执行力",
    "团队经验",
    "带团队",
)


@dataclass(frozen=True)
class WorkTenureTarget:
    """Structured tenure target extracted from a query phrase."""

    years: int
    title: str
    domain_tags: tuple[str, ...] = ()
    raw_phrase: str = ""


def _years_value(raw: str) -> int | None:
    text = str(raw or "").strip()
    if not text:
        return None
    chinese_digits = {
        "零": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if text.isdigit():
        return int(text)
    if text in chinese_digits:
        return chinese_digits[text]
    if "十" in text:
        left, right = text.split("十", 1)
        tens = 1 if not left else chinese_digits.get(left)
        ones = 0 if not right else chinese_digits.get(right)
        if tens is not None and ones is not None:
            return tens * 10 + ones
    return None


def parse_tenure_role_phrase(phrase: str) -> tuple[str, tuple[str, ...]]:
    """Split a tenure role phrase into canonical title key and domain tags.

    Examples:
        医药领域的销售 -> (销售, (医药,))
        销售 -> (销售, ())
        医药领域 -> ("", (医药,))
    """
    text = str(phrase or "").strip()
    if not text:
        return "", ()

    for pattern in _DOMAIN_TITLE_PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        domain = str(match.group(1) or "").strip()
        title = str(match.group(2) or "").strip()
        if domain and title:
            return normalize_role_tenure_title(title), (domain,)

    domain_only = _DOMAIN_ONLY_RE.match(text)
    if domain_only:
        domain = str(domain_only.group(1) or "").strip()
        if domain:
            return "", (domain,)

    return normalize_role_tenure_title(text), ()


def _target_from_role_phrase(years: int, role_phrase: str) -> WorkTenureTarget | None:
    title, domains = parse_tenure_role_phrase(role_phrase)
    if years is None:
        return None
    if not title and not domains:
        return None
    return WorkTenureTarget(
        years=years,
        title=title,
        domain_tags=domains,
        raw_phrase=role_phrase,
    )


def extract_work_tenure_target(query: str) -> WorkTenureTarget | None:
    """Return structured tenure target for year + role style queries."""
    q = str(query or "").strip()
    if not q:
        return None

    match = _TITLE_YEARS_FIRST_RE.search(q)
    if match:
        years = _years_value(match.group(1) or match.group(3) or match.group(5) or "")
        role = (match.group(2) or match.group(4) or match.group(6) or "").strip()
        if years is not None and role:
            return _target_from_role_phrase(years, role)

    match = _TITLE_CUMULATIVE_RE.search(q)
    if match:
        years = _years_value(match.group(2))
        role = match.group(1).strip()
        if years is not None and role:
            return _target_from_role_phrase(years, role)

    return None


def classify_work_tenure_intent(query: str) -> WorkTenureIntent:
    """Classify a tenure query into generic / title / recent."""
    q = str(query or "").strip()
    if not q:
        return "generic"
    if _RECENT_TENURE_RE.search(q):
        return "recent"

    target = extract_work_tenure_target(q)
    if target is None or not target.title:
        return "generic"

    if any(blocker in target.title for blocker in _GENERIC_TENURE_BLOCKERS):
        return "generic"
    return "title"


__all__ = [
    "WorkTenureIntent",
    "WorkTenureTarget",
    "classify_work_tenure_intent",
    "extract_work_tenure_target",
    "parse_tenure_role_phrase",
]
