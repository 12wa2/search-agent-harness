"""Capability phrases must not become exact-title tenure filters."""

from __future__ import annotations

import pytest

from search_agent.hybrid_v2.work_tenure_intent import classify_work_tenure_intent


@pytest.mark.parametrize(
    "query",
    [
        "找有3年以上后端经验的工程师",
        "5年以上推荐系统经验",
        "3年以上数据工程经验",
        "3年以上机器学习经验",
        "8年以上系统架构经验",
        "5年以上分布式系统经验",
    ],
)
def test_capability_tenure_is_generic(query: str) -> None:
    assert classify_work_tenure_intent(query) == "generic"


def test_explicit_job_title_tenure_stays_title() -> None:
    assert classify_work_tenure_intent("5年以上后端工程师经验") == "title"
