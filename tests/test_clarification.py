"""Boundary tests for deterministic skill-only clarification."""

from __future__ import annotations

import pytest

from search_agent.core.clarification import (
    detect_skill_only_clarification,
    is_actionable_recruitment_query,
)


@pytest.mark.parametrize(
    "query",
    [
        "懂 Java",
        "熟悉 Spark",
    ],
)
def test_bare_skill_queries_ask_for_a_role(query: str) -> None:
    clarification = detect_skill_only_clarification(query)

    assert clarification is not None
    assert clarification.questions
    assert len(clarification.options) == 3
    assert all(option.query for option in clarification.options)


@pytest.mark.parametrize(
    "query",
    [
        "找会 python 的",
        "Find someone who knows Python",
        "帮我找会 Python 的",
    ],
)
def test_explicit_search_action_makes_skill_query_actionable(query: str) -> None:
    assert detect_skill_only_clarification(query) is None


@pytest.mark.parametrize(
    "query",
    [
        "找 Python 后端工程师",
        "Python 工程师，3 年经验",
        "招聘机器学习工程师，北京",
        "负责数据平台的 Spark 工程师",
        "推荐算法工程师，要求熟悉推荐系统",
    ],
)
def test_complete_recruiting_queries_do_not_require_clarification(query: str) -> None:
    assert detect_skill_only_clarification(query) is None


def test_follow_up_turn_does_not_reinterrupt_the_conversation() -> None:
    assert detect_skill_only_clarification("找会 Python 的", has_history=True) is None


def test_generic_search_verb_does_not_block_a_skill_search() -> None:
    clarification = detect_skill_only_clarification("帮我找会 Python 的")

    assert clarification is None


def test_java_does_not_match_javascript() -> None:
    assert detect_skill_only_clarification("JavaScript") is not None
    assert detect_skill_only_clarification("Java") is not None
    assert detect_skill_only_clarification("JavaScript 工程师") is None


@pytest.mark.parametrize(
    "query",
    [
        "找有8年以上经验的系统架构师，必须同时具备系统架构设计和微服务架构经验",
        "Python 工程师",
        "帮我找会 Python 的",
        "招聘 Java 后端工程师，北京",
    ],
)
def test_actionable_recruitment_query_has_enough_search_context(query: str) -> None:
    assert is_actionable_recruitment_query(query) is True


@pytest.mark.parametrize("query", ["Java", "懂 Spark", "北京", "3年以上经验"])
def test_underspecified_query_is_not_actionable(query: str) -> None:
    assert is_actionable_recruitment_query(query) is False
