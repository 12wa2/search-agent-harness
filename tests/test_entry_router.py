"""Tests for the rule-first entry routing review."""

from __future__ import annotations

from search_agent.direct_search import build_direct_search_plan
from search_agent.entry_router import (
    ENTRY_ROUTE_SCHEMA_NAME,
    choose_entry_route,
    is_strong_direct_plan,
)
from search_agent.llm.client import FakeLlmClient


def _review(route: str, confidence: float, reason: str = "review") -> FakeLlmClient:
    return FakeLlmClient(
        {
            ENTRY_ROUTE_SCHEMA_NAME: {
                "route": route,
                "confidence": confidence,
                "reason": reason,
            }
        }
    )


def test_strong_direct_plan_skips_llm_review() -> None:
    plan = build_direct_search_plan("帮我找会 Python 的")
    assert plan is not None
    assert is_strong_direct_plan(plan)
    llm = _review("agent", 0.99)

    decision = choose_entry_route("帮我找会 Python 的", plan, llm)

    assert decision.route == "direct_search"
    assert decision.source == "rule"
    assert decision.llm_used is False
    assert llm.calls == []


def test_weak_direct_plan_can_be_vetoed_by_llm() -> None:
    plan = build_direct_search_plan("帮我找北京的")
    assert plan is not None
    assert is_strong_direct_plan(plan) is False
    llm = _review("agent", 0.91, "缺少岗位或技能")

    decision = choose_entry_route("帮我找北京的", plan, llm)

    assert decision.route == "agent"
    assert decision.source == "llm"
    assert decision.llm_used is True
    assert decision.confidence == 0.91
    assert [name for name, _ in llm.calls] == [ENTRY_ROUTE_SCHEMA_NAME]


def test_weak_direct_plan_can_be_confirmed_by_llm() -> None:
    plan = build_direct_search_plan("帮我找北京的")
    assert plan is not None
    llm = _review("direct_search", 0.88, "地点条件明确")

    decision = choose_entry_route("帮我找北京的", plan, llm)

    assert decision.route == "direct_search"
    assert decision.source == "llm"
    assert decision.llm_used is True


def test_low_confidence_llm_review_falls_back_to_agent() -> None:
    plan = build_direct_search_plan("帮我找北京的")
    assert plan is not None
    llm = _review("direct_search", 0.2, "证据不足")

    decision = choose_entry_route("帮我找北京的", plan, llm)

    assert decision.route == "agent"
    assert decision.source == "fallback"
    assert decision.llm_used is True


def test_invalid_llm_review_keeps_deterministic_direct_route() -> None:
    plan = build_direct_search_plan("帮我找北京的")
    assert plan is not None
    llm = FakeLlmClient()
    llm_responses = FakeLlmClient({ENTRY_ROUTE_SCHEMA_NAME: {}})

    decision = choose_entry_route("帮我找北京的", plan, llm_responses)

    assert llm.calls == []
    assert decision.route == "direct_search"
    assert decision.source == "fallback"
    assert decision.llm_used is True


def test_no_direct_plan_never_calls_llm() -> None:
    llm = _review("direct_search", 0.99)

    decision = choose_entry_route("找能力可迁移的架构师", None, llm)

    assert decision.route == "agent"
    assert decision.source == "rule"
    assert decision.llm_used is False
    assert llm.calls == []


def test_conversation_history_is_included_in_review_payload() -> None:
    plan = build_direct_search_plan("帮我找北京的")
    assert plan is not None
    llm = _review("agent", 0.9)

    choose_entry_route("帮我找北京的", plan, llm, has_history=True)

    assert llm.calls
    assert '"hasConversationHistory": true' in llm.calls[0][1]
