"""Tests for the in-memory reference SearchTool."""

from __future__ import annotations

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    ToolCandidate,
)
from search_agent.demo import DEMO_DATA
from search_agent.models.ir import (
    CandidateSearchIR,
    SemanticConditionIR,
    StructuredConditionIR,
    WhereNode,
)
from search_agent.tools.memory import InMemorySearchTool, load_candidates


def _tool() -> InMemorySearchTool:
    return InMemorySearchTool(DEMO_DATA)


def _leaf(field: str, operator: str, values: list) -> WhereNode:
    return WhereNode(
        type="leaf",
        condition=StructuredConditionIR(field=field, operator=operator, values=values),
    )


def test_load_demo_corpus():
    items = load_candidates(DEMO_DATA)
    assert len(items) == 16
    assert {item.docId for item in items} >= {"c001", "c016"}


def test_hard_filter_work_years():
    req = SearchToolRequest(
        intent=CandidateSearchIR(where=_leaf("work_years", "gte", [8])),
        query_text="q",
        company_id=1,
        top_k=20,
        routing=1,
    )
    result = _tool().search_with_intent(req)
    assert result.total == 4
    assert all((item.item.workYears or 0) >= 8 for item in result.items)
    assert all(
        item.hard_matches[0]["actualValues"] == [item.item.workYears] for item in result.items
    )


def test_recall_pool_size_returns_more_than_top_k():
    req = SearchToolRequest(
        intent=CandidateSearchIR(where=_leaf("work_years", "gte", [0])),
        query_text="q",
        company_id=1,
        top_k=3,
        recall_pool_size=8,
        routing=1,
    )
    result = _tool().search_with_intent(req)
    assert len(result.items) == 8
    assert result.total == 16


def test_skill_filter_and_exclude_doc_ids():
    req = SearchToolRequest(
        intent=CandidateSearchIR(where=_leaf("skill", "any", ["Spark"])),
        query_text="q",
        company_id=1,
        top_k=20,
        routing=1,
        exclude_doc_ids=["c008"],
    )
    result = _tool().search_with_intent(req)
    doc_ids = {item.doc_id for item in result.items}
    assert "c008" not in doc_ids
    assert {"c009", "c013"} <= doc_ids


def test_semantic_scoring_ranks_relevant_first():
    intent = CandidateSearchIR(
        where=_leaf("work_years", "gte", [0]),
        semantic=[
            SemanticConditionIR(text="推荐系统 召回排序", fields=["work_vector"], weight=1.5)
        ],
    )
    req = SearchToolRequest(intent=intent, query_text="q", company_id=1, top_k=5, routing=1)
    result = _tool().search_with_intent(req)
    top_names = [item.item.name for item in result.items[:3]]
    assert "陈杰" in top_names and "刘洋" in top_names
    assert result.items[0].hard_matches
    assert 0 < result.items[0].match_explanation["confidence"] <= 1.0


def test_rerank_pool_scores_by_abilities():
    items = load_candidates(DEMO_DATA)
    pool = [
        ToolCandidate(doc_id=item.docId, score=1.0, item=item)
        for item in items
        if item.docId in {"c001", "c005", "c008"}
    ]
    req = RerankToolRequest(
        query_text="高并发后端",
        candidates=pool,
        job_context={"abilities": ["高并发系统设计"], "hard_constraints": []},
        top_k=2,
        routing=1,
    )
    result = _tool().rerank_pool(req)
    assert result.total == 3
    assert result.items[0].doc_id == "c001"
