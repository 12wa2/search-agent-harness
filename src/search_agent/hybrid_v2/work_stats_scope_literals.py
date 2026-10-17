"""Literal scope anchors for latest-segment rewrite/prune (no entity capture)."""

from __future__ import annotations

# Longest-match wins when checking; keep sorted by length descending at use site.
LATEST_COMPANY_SCOPE_ANCHORS: tuple[str, ...] = (
    "最近一份工作经验在",
    "最近一份工作经历在",
    "最近一份在",
    "最近一段在",
    "最近工作在",
    "最近工作于",
)

LATEST_JOB_SCOPE_ANCHORS: tuple[str, ...] = (
    "最近职位是",
    "最近职位为",
    "最近岗位是",
    "最近岗位为",
    "最近职务是",
    "最近一份是",
    "最近一段是",
)


def query_has_latest_company_scope(query: str) -> bool:
    text = str(query or "").strip()
    return any(anchor in text for anchor in LATEST_COMPANY_SCOPE_ANCHORS)


def query_has_latest_job_scope(query: str) -> bool:
    text = str(query or "").strip()
    return any(anchor in text for anchor in LATEST_JOB_SCOPE_ANCHORS)


__all__ = [
    "LATEST_COMPANY_SCOPE_ANCHORS",
    "LATEST_JOB_SCOPE_ANCHORS",
    "query_has_latest_company_scope",
    "query_has_latest_job_scope",
]
