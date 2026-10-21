"""Tests for the standalone Hybrid V2 NL-to-IR parser."""

from __future__ import annotations

from datetime import datetime

from search_agent.hybrid_v2.parser import (
    HYBRID_V2_IR_JSON_SCHEMA,
    IrParseResult,
    build_hybrid_v2_system_prompt,
    parse_query_to_ir,
)
from search_agent.llm.client import FakeLlmClient


def _ir_payload() -> dict:
    return {
        "where": {
            "type": "and",
            "children": [
                {
                    "type": "leaf",
                    "condition": {
                        "field": "position",
                        "operator": "eq",
                        "values": ["Python工程师"],
                        "required": True,
                        "weight": 1.0,
                    },
                },
                {
                    "type": "leaf",
                    "condition": {
                        "field": "skill",
                        "operator": "any",
                        "values": ["Python"],
                        "required": True,
                        "weight": 1.0,
                    },
                },
            ],
        },
        "semantic": [
            {
                "text": "Python 后端项目经验",
                "fields": ["work_vector", "skills_vector"],
                "weight": 1.2,
            }
        ],
        "meta": {
            "needClarification": False,
            "clarifyQuestions": [],
            "notes": ["来自测试模型"],
            "tenureIntent": "title",
        },
    }


def test_parser_uses_injected_llm_and_preserves_v2_fields() -> None:
    llm = FakeLlmClient(responses={"hybrid_search_v2_ir": _ir_payload()})

    result = parse_query_to_ir(
        "找 Python 工程师，本科学历",
        llm_client=llm,
        as_of=datetime(2026, 1, 2, 3, 4, 5),
    )

    assert isinstance(result, IrParseResult)
    assert result.ok is True
    assert result.attempts == 1
    assert result.intent is not None
    assert result.intent.semantic[0].fields == ["work_vector", "skills_vector"]
    assert result.intent.meta.tenureIntent == "title"
    assert result.intent.meta.notes == ["来自测试模型", "auto_add_degree_from_query"]
    assert llm.calls and llm.calls[0][0] == "hybrid_search_v2_ir"


def test_parser_prompt_includes_system_time_and_tenant_tags() -> None:
    prompt = build_hybrid_v2_system_prompt(
        as_of=datetime(2026, 10, 19, 12, 0, 0),
        tag_name_to_id={"名企背景": 11},
    )

    assert "2026-10-19 12:00:00" in prompt
    assert "名企背景" in prompt
    assert "hybrid_search_v2_ir" not in prompt
    assert HYBRID_V2_IR_JSON_SCHEMA["required"] == ["where", "semantic", "meta"]


def test_parser_rewrites_undergraduate_tier_to_school_tier() -> None:
    payload = {
        "where": {
            "type": "and",
            "children": [
                {
                    "type": "leaf",
                    "condition": {
                        "field": "degree",
                        "operator": "eq",
                        "values": ["本科"],
                        "required": True,
                        "weight": 1.0,
                    },
                },
                {
                    "type": "leaf",
                    "condition": {
                        "field": "talent_tag",
                        "operator": "eq",
                        "values": ["211"],
                        "required": True,
                        "weight": 1.0,
                    },
                },
            ],
        },
        "semantic": [],
        "meta": {"needClarification": False, "clarifyQuestions": [], "notes": []},
    }
    llm = FakeLlmClient(responses={"hybrid_search_v2_ir": payload})

    result = parse_query_to_ir("本科是211", llm_client=llm)

    assert result.intent is not None
    conditions = [
        child.condition for child in result.intent.where.children if child.condition is not None
    ]
    fields = {condition.field: condition for condition in conditions}
    assert fields["degree"].values == ["本科"]
    assert fields["school_tier"].values == ["211"]
    assert "talent_tag" not in fields
    assert "rewrite_talent_tag_to_school_tier" in result.intent.meta.notes
    assert "auto_add_school_tier_from_query" in result.intent.meta.notes


def test_parser_keeps_plain_985_211_as_talent_tag_without_education_scope() -> None:
    payload = {
        "where": {
            "type": "leaf",
            "condition": {
                "field": "talent_tag",
                "operator": "eq",
                "values": ["211"],
                "required": True,
                "weight": 1.0,
            },
        },
        "semantic": [],
        "meta": {"needClarification": False, "clarifyQuestions": [], "notes": []},
    }
    llm = FakeLlmClient(responses={"hybrid_search_v2_ir": payload})

    result = parse_query_to_ir("找211人才", llm_client=llm)

    assert result.intent is not None
    assert result.intent.where.condition is not None
    assert result.intent.where.condition.field == "talent_tag"
    assert result.intent.where.condition.values == ["211"]


def test_parser_does_not_convert_negated_undergraduate_tier() -> None:
    payload = {
        "where": {
            "type": "leaf",
            "condition": {
                "field": "talent_tag",
                "operator": "eq",
                "values": ["211"],
                "required": True,
                "weight": 1.0,
            },
        },
        "semantic": [],
        "meta": {"needClarification": False, "clarifyQuestions": [], "notes": []},
    }
    llm = FakeLlmClient(responses={"hybrid_search_v2_ir": payload})

    result = parse_query_to_ir("本科不是211", llm_client=llm)

    assert result.intent is not None
    assert result.intent.where.type == "and"
    fields = {
        child.condition.field
        for child in result.intent.where.children
        if child.condition is not None
    }
    assert "talent_tag" in fields
    assert "school_tier" not in fields


def test_parser_retries_once_when_where_is_missing() -> None:
    calls = {"n": 0}

    def respond(schema_name: str, user: str) -> dict:
        del schema_name, user
        calls["n"] += 1
        return {} if calls["n"] == 1 else _ir_payload()

    llm = FakeLlmClient(responses=respond)
    result = parse_query_to_ir("找 Python 工程师", llm_client=llm)

    assert result.ok is True
    assert result.attempts == 2
    assert calls["n"] == 2
    assert len(llm.calls) == 2


def test_parser_returns_error_after_bounded_retries() -> None:
    llm = FakeLlmClient(responses={"hybrid_search_v2_ir": {}})

    result = parse_query_to_ir("找 Python 工程师", llm_client=llm)

    assert result.ok is False
    assert result.attempts == 2
    assert result.error is not None and "missing where" in result.error
    assert len(llm.calls) == 2
