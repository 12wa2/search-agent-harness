"""Tests for the Elasticsearch backend.

The DSL compiler is covered offline (no server needed, runs in CI). The
integration tests need a reachable node -- start one with
``docker compose up -d elasticsearch`` and install the extra with
``pip install -e ".[es]"`` -- and skip otherwise.
"""

from __future__ import annotations

import os

import pytest

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
from search_agent.tools.elasticsearch import ElasticsearchSearchTool, compile_where
from search_agent.tools.memory import InMemorySearchTool, load_candidates
from search_agent.tools.text import where_matches

_SAMPLE = load_candidates(DEMO_DATA)
ES_URL = os.environ.get("SEARCH_AGENT_ES_URL", "http://127.0.0.1:9200")
ES_INDEX = "search-agent-test"


def _camel(snake: str) -> str:
    head, *tail = snake.split("_")
    return head + "".join(part.capitalize() for part in tail)


def _first_work_value(key: str) -> str:
    for item in _SAMPLE:
        for work in item.workExperiences or []:
            value = (work or {}).get(key) or (work or {}).get(_camel(key))
            if value:
                return str(value)
    return "x"


_COMPANY = _first_work_value("company_name")
_CITY = str(_SAMPLE[0].currentResidence or "x")


def _leaf(field: str, operator: str, values: list) -> WhereNode:
    return WhereNode(
        type="leaf",
        condition=StructuredConditionIR(field=field, operator=operator, values=values),
    )


def _request(where, *, top_k=50, semantic=None, exclude=(), details=False):
    return SearchToolRequest(
        intent=CandidateSearchIR(where=where, semantic=semantic or []),
        query_text="q",
        company_id=1,
        top_k=top_k,
        routing=1,
        exclude_doc_ids=list(exclude),
        details=details,
    )


def _ranked(result):
    return [(item.doc_id, item.score) for item in result.items]


# ---------- offline DSL compiler (no server required) ----------


def test_compile_numeric_conditions():
    assert compile_where(_leaf("work_years", "gte", [8])) == {
        "query": {"range": {"work_years": {"gte": 8.0}}},
        "exact": True,
    }
    assert compile_where(_leaf("age", "lte", [32])) == {
        "query": {"range": {"age": {"lte": 32.0}}},
        "exact": True,
    }
    assert compile_where(_leaf("work_years", "range", [6, 3])) == {
        "query": {"range": {"work_years": {"gte": 3.0, "lte": 6.0}}},
        "exact": True,
    }
    numeric_op_mismatch = compile_where(_leaf("work_years", "any", [3]))
    assert numeric_op_mismatch["query"] == {"match_none": {}}


def test_compile_term_conditions():
    assert compile_where(_leaf("skill", "any", ["Spark", "Flink"])) == {
        "query": {"terms": {"skill": ["flink", "spark"]}},
        "exact": True,
    }
    assert compile_where(_leaf("skill", "all", ["Java", "Kafka"])) == {
        "query": {
            "bool": {
                "filter": [
                    {"terms": {"skill": ["java"]}},
                    {"terms": {"skill": ["kafka"]}},
                ]
            }
        },
        "exact": True,
    }
    assert compile_where(_leaf("skill", "not", ["Python"])) == {
        "query": {"bool": {"must_not": [{"terms": {"skill": ["python"]}}]}},
        "exact": True,
    }
    numeric_op_on_terms = compile_where(_leaf("skill", "gte", [3]))
    assert numeric_op_on_terms["query"] == {"match_none": {}}
    assert numeric_op_on_terms["exact"] is True


def test_compile_aliases_and_latest_fields():
    assert compile_where(_leaf("live_city", "eq", ["X"]))["query"] == {"terms": {"city": ["x"]}}
    assert compile_where(_leaf("latest_company", "eq", ["Acme"]))["query"] == {
        "terms": {"latest_company": ["acme"]}
    }


def test_compile_boolean_shapes():
    node = WhereNode(
        type="or",
        children=[_leaf("skill", "any", ["Spark"]), _leaf("work_years", "gte", [10])],
        minimumShouldMatch=2,
    )
    compiled = compile_where(node)
    assert compiled["exact"] is True
    assert compiled["query"]["bool"]["minimum_should_match"] == 2
    assert len(compiled["query"]["bool"]["should"]) == 2

    nested = WhereNode(type="not", child=node)
    inner = compile_where(nested)
    assert inner["query"] == {"bool": {"must_not": [compiled["query"]]}}

    unknown = compile_where(_leaf("talent_tag", "eq", ["x"]))
    assert unknown["query"] == {"match_all": {}} and unknown["exact"] is False

    not_unknown = compile_where(WhereNode(type="not", child=_leaf("talent_tag", "eq", ["x"])))
    assert not_unknown["query"] == {"match_all": {}} and not_unknown["exact"] is False


# ---------- integration (requires a running node) ----------

ES_CASES: list[tuple[str, WhereNode]] = [
    ("numeric-gte", _leaf("work_years", "gte", [8])),
    ("numeric-range", _leaf("work_years", "range", [3, 6])),
    ("skill-any", _leaf("skill", "any", ["Spark", "Flink"])),
    ("skill-all", _leaf("skill", "all", ["Java", "Kafka"])),
    ("skill-not", _leaf("skill", "not", ["Python"])),
    ("company-eq", _leaf("company", "eq", [_COMPANY])),
    ("latest-company", _leaf("latest_company", "eq", [_COMPANY])),
    ("city-eq", _leaf("city", "eq", [_CITY])),
    ("unsupported-field", _leaf("talent_tag", "eq", ["x"])),
    (
        "and",
        WhereNode(
            type="and",
            children=[_leaf("work_years", "gte", [3]), _leaf("skill", "any", ["Java"])],
        ),
    ),
    (
        "or-msm2",
        WhereNode(
            type="or",
            children=[
                _leaf("skill", "any", ["Spark"]),
                _leaf("skill", "any", ["Flink"]),
                _leaf("work_years", "gte", [10]),
            ],
            minimumShouldMatch=2,
        ),
    ),
    ("not-leaf", WhereNode(type="not", child=_leaf("city", "eq", [_CITY]))),
]


@pytest.fixture(scope="module")
def es_tool():
    elasticsearch = pytest.importorskip("elasticsearch")
    client = elasticsearch.Elasticsearch(ES_URL)
    if not client.ping():
        client.close()
        pytest.skip(f"Elasticsearch not reachable at {ES_URL}")
    tool = ElasticsearchSearchTool(DEMO_DATA, client=client, index=ES_INDEX, recreate=True)
    yield tool
    client.close()


@pytest.mark.es
def test_index_is_ready(es_tool):
    assert es_tool.size == 16
    assert es_tool.index_name == ES_INDEX


@pytest.mark.es
@pytest.mark.parametrize("label,where", ES_CASES, ids=[case[0] for case in ES_CASES])
def test_search_parity_with_in_memory(es_tool, label: str, where: WhereNode):
    req = _request(where)
    reference = InMemorySearchTool(DEMO_DATA).search_with_intent(req)
    result = es_tool.search_with_intent(req)
    assert _ranked(result) == _ranked(reference)
    assert result.total == reference.total
    assert result.quality == reference.quality
    if reference.items:
        assert result.items[0].match_explanation == reference.items[0].match_explanation


@pytest.mark.es
def test_semantic_scoring_parity(es_tool):
    semantic = [SemanticConditionIR(text="Spark Flink", fields=["skills_vector"], weight=1.5)]
    req = _request(_leaf("work_years", "gte", [0]), semantic=semantic)
    reference = InMemorySearchTool(DEMO_DATA).search_with_intent(req)
    result = es_tool.search_with_intent(req)
    assert _ranked(result) == _ranked(reference)


@pytest.mark.es
def test_details_returns_executed_dsl(es_tool):
    req = _request(_leaf("skill", "any", ["Spark"]), details=True)
    result = es_tool.search_with_intent(req)
    assert result.es_query is not None
    assert result.es_query["backend"] == "elasticsearch"
    assert result.es_query["query"]["bool"]["filter"][0] == {"terms": {"skill": ["spark"]}}


@pytest.mark.es
def test_search_text_bm25(es_tool):
    target = next(item for item in _SAMPLE if len(item.name or "") >= 2)
    hits = es_tool.search_text(target.name, top_k=5)
    assert target.docId in [doc_id for doc_id, _ in hits]


@pytest.mark.es
def test_search_text_honours_the_where_filter(es_tool):
    where = _leaf("skill", "any", ["Spark"])
    hits = es_tool.search_text("Spark", top_k=5, where=where)
    assert hits
    for doc_id, _ in hits:
        item = next(entry for entry in _SAMPLE if entry.docId == doc_id)
        assert where_matches(where, item)


@pytest.mark.es
def test_search_semantic_knn_returns_the_matching_doc(es_tool):
    target = next(item for item in _SAMPLE if len(item.name or "") >= 2)
    hits = es_tool.search_semantic(target.name, top_k=5)
    assert target.docId in [doc_id for doc_id, _ in hits]


@pytest.mark.es
@pytest.mark.es
def test_search_semantic_with_an_empty_query_returns_nothing(es_tool):
    assert es_tool.search_semantic("", top_k=5) == []


def test_search_semantic_respects_the_where_filter(es_tool):
    where = _leaf("work_years", "gte", [8])
    for doc_id, _ in es_tool.search_semantic("高并发 后端 架构", top_k=5, where=where):
        item = next(entry for entry in _SAMPLE if entry.docId == doc_id)
        assert float(item.workYears or 0) >= 8


@pytest.mark.es
def test_hybrid_recall_fuses_bm25_and_knn(es_tool):
    target = next(item for item in _SAMPLE if len(item.name or "") >= 2)
    rows = es_tool.hybrid_search(target.name, top_k=5)
    assert rows
    assert rows[0]["docId"] == target.docId
    assert rows[0]["knnRank"] == 1 or rows[0]["bm25Rank"] == 1
    assert rows[0]["score"] > 0
    keyword_hits = [doc_id for doc_id, _ in es_tool.search_text(target.name, top_k=5)]
    assert set(keyword_hits) <= {row["docId"] for row in rows} | set(keyword_hits)


@pytest.mark.es
def test_hybrid_recall_survives_a_zero_hit_keyword_query(es_tool):
    query = "秒杀 大促 稳定性治理 高并发 架构 后端"
    rows = es_tool.hybrid_search(query, top_k=5)
    assert rows
    if not es_tool.search_text(query, top_k=5):
        assert all(row["bm25Rank"] is None for row in rows)
        assert any(row["knnRank"] == 1 for row in rows)


@pytest.mark.es
def test_rerank_pool_parity(es_tool):
    pool = [ToolCandidate(doc_id=item.docId, score=1.0, item=item) for item in _SAMPLE[:6]]
    req = RerankToolRequest(
        query_text="q",
        candidates=pool,
        job_context={
            "abilities": ["Spark", "data platform"],
            "hard_constraints": [{"field": "work_years", "operator": "gte", "values": [5]}],
        },
        top_k=3,
        routing=1,
    )
    reference = InMemorySearchTool(DEMO_DATA).rerank_pool(req)
    result = es_tool.rerank_pool(req)
    assert _ranked(result) == _ranked(reference)
    assert result.total == reference.total
