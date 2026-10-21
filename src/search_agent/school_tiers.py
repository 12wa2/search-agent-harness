"""Deterministic 985/211 lookup for education rows."""

from __future__ import annotations

import re

_UNDERGRAD_STAGE_PATTERN = r"(?:第一学历|统招本科|全日制本科|本科)"
_EDUCATION_TIER_TOKEN_PATTERN = r"(?:985|211)(?:\s*[/、和或,，]\s*(?:985|211))*"
_EDUCATION_TIER_AFTER_STAGE_RE = re.compile(
    rf"{_UNDERGRAD_STAGE_PATTERN}(?:学历|阶段|期间)?[^，。；,;\n]{{0,8}}?"
    rf"({_EDUCATION_TIER_TOKEN_PATTERN})"
)
_EDUCATION_TIER_BEFORE_STAGE_RE = re.compile(
    rf"({_EDUCATION_TIER_TOKEN_PATTERN})(?:院校|大学)?[^，。；,;\n]{{0,6}}?"
    rf"{_UNDERGRAD_STAGE_PATTERN}"
)
_EDUCATION_TIER_NEGATION = ("不要", "排除", "不是", "非", "不考虑", "不要求")

_SCHOOL_TIERS: dict[str, tuple[str, ...]] = {
    "清华大学": ("985", "211"),
    "北京大学": ("985", "211"),
    "上海交通大学": ("985", "211"),
    "浙江大学": ("985", "211"),
    "复旦大学": ("985", "211"),
    "南京大学": ("985", "211"),
    "华中科技大学": ("985", "211"),
    "武汉大学": ("985", "211"),
    "中山大学": ("985", "211"),
    "同济大学": ("985", "211"),
    "电子科技大学": ("985", "211"),
    "华南理工大学": ("985", "211"),
    "北京理工大学": ("985", "211"),
    "东南大学": ("985", "211"),
    "厦门大学": ("985", "211"),
    "北京邮电大学": ("211",),
    "西安电子科技大学": ("211",),
    "深圳大学": (),
}

_SCHOOL_ALIASES: dict[str, str] = {
    "清华": "清华大学",
    "北大": "北京大学",
    "上海交大": "上海交通大学",
    "上交": "上海交通大学",
    "浙大": "浙江大学",
    "复旦": "复旦大学",
    "南大": "南京大学",
    "华科": "华中科技大学",
    "华中科大": "华中科技大学",
    "武大": "武汉大学",
    "中大": "中山大学",
    "同济": "同济大学",
    "电子科大": "电子科技大学",
    "成电": "电子科技大学",
    "华南理工": "华南理工大学",
    "北理工": "北京理工大学",
    "北邮": "北京邮电大学",
    "西电": "西安电子科技大学",
    "深大": "深圳大学",
    "东南": "东南大学",
    "厦大": "厦门大学",
}


def school_tiers_for(school: str) -> tuple[str, ...]:
    """Return the 985/211 tiers for a school name, empty when unknown."""

    key = re.sub(r"\s+", "", str(school or ""))
    if not key:
        return ()
    canonical = _SCHOOL_ALIASES.get(key, key)
    return _SCHOOL_TIERS.get(canonical, ())


def query_mentions_undergraduate(query: str) -> bool:
    """Return True when the query explicitly refers to an undergraduate degree."""

    return bool(re.search(_UNDERGRAD_STAGE_PATTERN, str(query or "")))


def education_tier_values_from_query(query: str) -> list[str]:
    """Extract affirmative 985/211 values tied to an undergraduate education stage."""

    text = str(query or "").strip()
    if not text:
        return []
    matches = [
        *list(_EDUCATION_TIER_AFTER_STAGE_RE.finditer(text)),
        *list(_EDUCATION_TIER_BEFORE_STAGE_RE.finditer(text)),
    ]
    values: list[str] = []
    for match in matches:
        prefix = text[max(0, match.start() - 8) : match.start()]
        matched_text = match.group(0)
        if any(token in prefix or token in matched_text for token in _EDUCATION_TIER_NEGATION):
            continue
        for tier in re.findall(r"985|211", matched_text):
            if tier not in values:
                values.append(tier)
    return values


__all__ = [
    "education_tier_values_from_query",
    "query_mentions_undergraduate",
    "school_tiers_for",
]
