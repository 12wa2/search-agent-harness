"""Deterministic direct-search routing and IR compilation tests."""

from __future__ import annotations

from io import StringIO

import pytest

from search_agent.cli import make_event_printer
from search_agent.direct_search import (
    build_direct_search_plan,
    should_use_direct_search,
)
from search_agent.models.ir import (
    CandidateSearchIR,
    IRMeta,
    SemanticConditionIR,
    StructuredConditionIR,
    WhereNode,
)


@pytest.mark.parametrize(
    "query",
    [
        "帮我找会 Python 的",
        "寻找有 3 年经验的 Python 工程师",
        "招聘 Java 后端工程师，北京",
        "Python 工程师",
    ],
)
def test_explicit_simple_queries_use_direct_search(query: str) -> None:
    assert should_use_direct_search(query) is True
    assert build_direct_search_plan(query) is not None


@pytest.mark.parametrize(
    "query",
    [
        "找能力可迁移的架构师",
        "寻找 Python 后端工程师，重点考察服务端 API、微服务和系统设计经验",
        "那北京的呢",
        "帮我推荐一首歌",
    ],
)
def test_ambiguous_or_non_recruiting_queries_stay_on_agent_route(query: str) -> None:
    assert should_use_direct_search(query) is False
    assert build_direct_search_plan(query) is None


def test_direct_plan_compiles_skill_years_and_role_into_ir() -> None:
    plan = build_direct_search_plan("寻找有 3 年经验的 Python 工程师")

    assert plan is not None
    assert plan.skills == ("Python",)
    assert plan.role == "工程师"
    assert plan.years == (3.0, 3.0)
    conditions = plan.job_profile.hard_constraints
    assert any(c.field == "skill" and c.values == ["Python"] for c in conditions)
    assert any(c.field == "work_years" and c.values == [3.0] for c in conditions)
    assert plan.intent.meta.notes == ["direct_search:deterministic_parse"]


def test_direct_plan_compiles_city_filter() -> None:
    plan = build_direct_search_plan("招聘 Java 后端工程师，北京")

    assert plan is not None
    assert plan.cities == ("北京",)
    assert plan.skills == ("Java",)
    assert any(
        c.field == "city" and c.values == ["北京"] for c in plan.job_profile.hard_constraints
    )


def test_direct_plan_event_payload_exposes_parsed_ir() -> None:
    plan = build_direct_search_plan("帮我找会 Python 的")

    assert plan is not None
    payload = plan.event_payload()
    assert payload["skills"] == ["Python"]
    assert payload["ir"]["meta"]["notes"] == ["direct_search:deterministic_parse"]


def test_direct_plan_presentation_uses_parser_ir() -> None:
    plan = build_direct_search_plan("帮我找会 Python 的")
    assert plan is not None
    intent = CandidateSearchIR(
        where=WhereNode(
            type="and",
            children=[
                WhereNode(
                    type="leaf",
                    condition=StructuredConditionIR(
                        field="skill", operator="any", values=["Go", "Rust"]
                    ),
                ),
                WhereNode(
                    type="leaf",
                    condition=StructuredConditionIR(field="city", operator="any", values=["上海"]),
                ),
            ],
        ),
        semantic=[SemanticConditionIR(text="服务端开发", fields=["work_vector"], weight=1.0)],
        meta=IRMeta(notes=["from_parser"]),
    )

    effective = plan.with_intent(
        intent,
        ir_source="hybrid_v2_parser",
        parser_attempts=1,
    )

    payload = effective.event_payload()
    assert payload["jobTitle"] == "Go 工程师"
    assert payload["skills"] == ["Go", "Rust"]
    assert payload["cities"] == ["上海"]
    assert payload["irSource"] == "hybrid_v2_parser"
    assert effective.job_profile.must_abilities[0].name == "Go"


def test_cli_direct_search_prints_three_stage_timeline() -> None:
    plan = build_direct_search_plan("帮我找会 Python 的")
    assert plan is not None
    output = StringIO()
    sink = make_event_printer(output)
    state = {
        "difficulty_route": "direct_search",
        "direct_search": {
            "plan": plan.event_payload(),
            "recalled": 3,
            "quality": "rich",
        },
        "final_search_total": 3,
        "final_total": 2,
    }

    sink("direct_search", state)
    sink("execute", state)
    sink("finalize", state)

    text = output.getvalue()
    assert "[1/3 direct_search]" in text
    assert "[2/3 execute] 直连 SearchTool" in text
    assert "[3/3 finalize] 直连交付 2 人" in text
    assert "/8" not in text
