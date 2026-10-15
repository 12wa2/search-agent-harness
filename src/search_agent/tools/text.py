"""Shared text / structured-condition helpers for the bundled backends.

Extracted from the in-memory reference tool so every backend (in-memory,
SQLite FTS5, and anything you plug in) shares the exact same tokenization,
candidate-text extraction and condition-matching semantics. The hard-match
facts a judge sees are therefore identical no matter which backend executed
the path.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from search_agent.models.api import CandidateItem
from search_agent.models.ir import StructuredConditionIR, WhereNode

logger = logging.getLogger(__name__)

__all__ = [
    "as_number",
    "candidate_text",
    "candidate_values",
    "certificate_names",
    "collect_leaves",
    "companies",
    "confidence",
    "coverage",
    "education_values",
    "first_present",
    "leaf_matches",
    "skill_names",
    "titles",
    "tokens",
    "where_matches",
]


NUMERIC_OPS = {"gte", "lte", "range"}
ASCII_RE = re.compile(r"[a-z0-9+#.]+")
CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")


# ── candidate field extraction ─────────────────────────────────────────────


def skill_names(item: CandidateItem) -> list[str]:
    return [str(s.get("name", "")) for s in item.skills or [] if isinstance(s, dict)]


def first_present(entry: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = entry.get(key)
        if value:
            return str(value)
    return ""


def companies(item: CandidateItem) -> list[str]:
    return [
        name
        for name in (
            first_present(we, ("company_name", "companyName"))
            for we in item.workExperiences or []
            if isinstance(we, dict)
        )
        if name
    ]


def titles(item: CandidateItem) -> list[str]:
    return [
        title
        for title in (
            first_present(we, ("job_title", "jobTitle", "position", "positionName"))
            for we in item.workExperiences or []
            if isinstance(we, dict)
        )
        if title
    ]


def education_values(item: CandidateItem, *keys: str) -> list[str]:
    return [
        value
        for value in (
            first_present(edu, keys) for edu in item.educations or [] if isinstance(edu, dict)
        )
        if value
    ]


def certificate_names(item: CandidateItem) -> list[str]:
    return [str(c.get("name", "")) for c in item.certificates or [] if isinstance(c, dict)]


def as_number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def candidate_values(item: CandidateItem, field: str) -> list[str] | float | int | None:
    """Best-effort structured field extraction for demo filters."""
    if field == "name":
        return [item.name] if item.name else []
    if field == "skill":
        return skill_names(item)
    if field == "company":
        return companies(item)
    if field == "latest_company":
        return companies(item)[:1]
    if field in ("position", "latest_work_job"):
        return titles(item)
    if field == "intention_position":
        return list(item.expectedPositions or [])
    if field == "school":
        return education_values(item, "school", "school_name", "schoolName")
    if field == "major":
        return education_values(item, "major", "major_name", "majorName")
    if field == "degree":
        return education_values(item, "degree", "degree_name", "degreeName")
    if field in ("city", "live_city"):
        return [item.currentResidence] if item.currentResidence else []
    if field == "intention_city":
        return list(item.expectedCities or [])
    if field == "certificate":
        return certificate_names(item)
    if field == "language":
        return [
            str(lang.get("name", "")) for lang in item.languages or [] if isinstance(lang, dict)
        ]
    if field == "work_years":
        return item.workYears
    if field == "age":
        return item.age
    return None


# ── WHERE tree evaluation ──────────────────────────────────────────────────


def leaf_matches(cond: StructuredConditionIR, item: CandidateItem) -> bool:
    values = candidate_values(item, cond.field)
    if values is None:
        logger.debug("in-memory tool: unsupported field %r", cond.field)
        return False
    op = cond.operator
    if isinstance(values, (int, float)):
        if op not in NUMERIC_OPS or not cond.values:
            return False
        numbers = [n for n in (as_number(v) for v in cond.values) if n is not None]
        if not numbers:
            return False
        number = float(values)
        if op == "gte":
            return number >= numbers[0]
        if op == "lte":
            return number <= numbers[0]
        low, high = sorted(numbers[:2])
        return low <= number <= high  # range
    wanted = {str(v).strip().lower() for v in cond.values if str(v).strip()}
    if not wanted:
        return False
    candidates = {str(v).strip().lower() for v in values if str(v).strip()}
    if op in ("any", "eq"):
        return bool(candidates & wanted)
    if op == "all":
        return wanted <= candidates
    if op == "not":
        return not (candidates & wanted)
    return False


def where_matches(node: WhereNode | None, item: CandidateItem) -> bool:
    if node is None:
        return True
    if node.type == "and":
        return all(where_matches(child, item) for child in node.children)
    if node.type == "or":
        need = node.minimumShouldMatch or 1
        return sum(1 for child in node.children if where_matches(child, item)) >= need
    if node.type == "not":
        return not where_matches(node.child, item)
    if node.condition is not None:
        return leaf_matches(node.condition, item)
    return True


def collect_leaves(node: WhereNode | None) -> list[StructuredConditionIR]:
    if node is None:
        return []
    if node.type in ("and", "or"):
        out: list[StructuredConditionIR] = []
        for child in node.children:
            out.extend(collect_leaves(child))
        return out
    if node.type == "not":
        return collect_leaves(node.child)
    return [node.condition] if node.condition is not None else []


# ── soft scoring ───────────────────────────────────────────────────────────


def tokens(text: str) -> list[str]:
    """ASCII words + CJK runs and bigrams, good enough for demo scoring."""
    lowered = (text or "").lower()
    tokens = [t for t in ASCII_RE.findall(lowered) if len(t) >= 2]
    for run in CJK_RUN_RE.findall(lowered):
        if len(run) >= 2:
            tokens.append(run)
        if len(run) >= 3:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def candidate_text(item: CandidateItem) -> str:
    parts: list[str] = [item.name or "", item.summary or ""]
    parts.extend(skill_names(item))
    parts.extend(companies(item))
    parts.extend(titles(item))
    parts.extend(item.expectedPositions or [])
    parts.extend(certificate_names(item))
    parts.extend(education_values(item, "school", "school_name", "schoolName"))
    parts.extend(education_values(item, "major", "major_name", "majorName"))
    return " ".join(parts).lower()


def coverage(text: str, blob: str) -> float:
    token_set = set(tokens(text))
    if not token_set:
        return 0.0
    return sum(1 for token in token_set if token in blob) / len(token_set)


def confidence(score: float) -> float:
    return round(min(1.0, score / 2.5), 3)
