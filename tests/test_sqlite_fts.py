"""Tests for the SQLite/FTS5 backend: parity with the in-memory reference.

The bundled backends must return identical rankings for the same intent;
these tests pin that invariant across operators, tree shapes and edge cases.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

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
from search_agent.tools.memory import InMemorySearchTool, load_candidates
from search_agent.tools.sqlite_fts import SqliteFtsSearchTool

_SAMPLE = load_candidates(DEMO_DATA)


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


def _first_education_value(keys: tuple[str, ...]) -> str:
    for item in _SAMPLE:
        for education in item.educations or []:
            for key in keys:
                value = (education or {}).get(key)
                if value:
                    return str(value)
    return "x"


_COMPANY = _first_work_value("company_name")
_CITY = str(_SAMPLE[0].currentResidence or "x")
_DEGREE = _first_education_value(("degree", "degree_name"))


def _leaf(field: str, operator: str, values: list) -> WhereNode:
    return WhereNode(
        type="leaf",
        condition=StructuredConditionIR(field=field, operator=operator, values=values),
    )


CASES: list[tuple[str, WhereNode]] = [
    ("numeric-gte", _leaf("work_years", "gte", [8])),
    ("numeric-lte", _leaf("age", "lte", [32])),
    ("numeric-range", _leaf("work_years", "range", [3, 6])),
    ("skill-any", _leaf("skill", "any", ["Spark", "Flink"])),
    ("skill-all", _leaf("skill", "all", ["Java", "Kafka"])),
    ("skill-not", _leaf("skill", "not", ["Python"])),
    ("skill-gte-on-array", _leaf("skill", "gte", [3])),
    ("company-eq", _leaf("company", "eq", [_COMPANY])),
    ("latest-company-eq", _leaf("latest_company", "eq", [_COMPANY])),
    ("city-eq", _leaf("city", "eq", [_CITY])),
    ("live-city-eq", _leaf("live_city", "eq", [_CITY])),
    ("degree-eq", _leaf("degree", "eq", [_DEGREE])),
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
    ("not-of-unsupported", WhereNode(type="not", child=_leaf("talent_tag", "eq", ["x"]))),
    (
        "not-of-and",
        WhereNode(
            type="not",
            child=WhereNode(
                type="and",
                children=[_leaf("work_years", "gte", [1]), _leaf("skill", "any", ["Java"])],
            ),
        ),
    ),
]

_PERMISSIVE = {"unsupported-field", "not-of-unsupported"}


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


@pytest.mark.parametrize("label,where", CASES, ids=[case[0] for case in CASES])
def test_where_parity_with_in_memory(label: str, where: WhereNode):
    req = _request(where)
    reference = InMemorySearchTool(DEMO_DATA).search_with_intent(req)
    result = SqliteFtsSearchTool(DEMO_DATA).search_with_intent(req)
    assert _ranked(result) == _ranked(reference)
    assert result.total == reference.total
    assert result.quality == reference.quality
    if reference.items:
        assert result.items[0].match_explanation == reference.items[0].match_explanation


def test_compiled_sql_exactness_flags():
    tool = SqliteFtsSearchTool(DEMO_DATA)
    for label, where in CASES:
        compiled = tool.compile_where(where)
        assert compiled["sql"], label
        assert compiled["exact"] is (label not in _PERMISSIVE), label


def test_semantic_scoring_parity():
    semantic = [SemanticConditionIR(text="Spark Flink", fields=["skills_vector"], weight=1.5)]
    req = _request(_leaf("work_years", "gte", [0]), semantic=semantic)
    reference = InMemorySearchTool(DEMO_DATA).search_with_intent(req)
    result = SqliteFtsSearchTool(DEMO_DATA).search_with_intent(req)
    assert _ranked(result) == _ranked(reference)
    assert (
        result.items[0].match_explanation["softSignals"]
        == reference.items[0].match_explanation["softSignals"]
    )


def test_concurrent_searches_share_the_connection_safely():
    tool = SqliteFtsSearchTool(DEMO_DATA)
    req = _request(_leaf("work_years", "gte", [0]))
    reference = _ranked(InMemorySearchTool(DEMO_DATA).search_with_intent(req))
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: tool.search_with_intent(req), range(32)))
        assert all(_ranked(result) == reference for result in results)
    finally:
        tool.close()


def test_exclude_doc_ids_parity():
    req = _request(_leaf("work_years", "gte", [0]), exclude=["c001", "c002"])
    reference = InMemorySearchTool(DEMO_DATA).search_with_intent(req)
    result = SqliteFtsSearchTool(DEMO_DATA).search_with_intent(req)
    assert _ranked(result) == _ranked(reference)
    assert all(item.doc_id not in {"c001", "c002"} for item in result.items)


def test_quality_labels_parity():
    for top_k in (1, 3, 50):
        req = _request(_leaf("work_years", "gte", [0]), top_k=top_k)
        reference = InMemorySearchTool(DEMO_DATA).search_with_intent(req)
        result = SqliteFtsSearchTool(DEMO_DATA).search_with_intent(req)
        assert (result.total, result.quality) == (reference.total, reference.quality)


def test_details_payload_reports_compiled_sql():
    req = _request(_leaf("skill", "any", ["Spark"]), details=True)
    result = SqliteFtsSearchTool(DEMO_DATA).search_with_intent(req)
    assert result.es_query is not None
    assert result.es_query["backend"] == "sqlite-fts5"
    assert "candidate_terms" in result.es_query["whereSql"]
    assert result.es_query["filteredCount"] == result.total


def test_rerank_pool_parity():
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
    result = SqliteFtsSearchTool(DEMO_DATA).rerank_pool(req)
    assert _ranked(result) == _ranked(reference)
    assert result.total == reference.total


def test_search_text_ranks_token_hits():
    tool = SqliteFtsSearchTool(DEMO_DATA)
    target = next(item for item in _SAMPLE if len(item.name or "") >= 2)
    hits = tool.search_text(target.name, top_k=5)
    assert target.docId in [doc_id for doc_id, _ in hits]
    scores = [score for _, score in hits]
    assert scores == sorted(scores, reverse=True)


def test_search_text_requires_fts_index():
    tool = SqliteFtsSearchTool(DEMO_DATA, fts=False)
    with pytest.raises(RuntimeError):
        tool.search_text("spark")
    req = _request(_leaf("work_years", "gte", [0]))
    reference = InMemorySearchTool(DEMO_DATA).search_with_intent(req)
    assert _ranked(tool.search_with_intent(req)) == _ranked(reference)


def test_persistent_database_is_reusable(tmp_path):
    db_path = str(tmp_path / "index.db")
    where = _leaf("skill", "any", ["Spark"])
    first = SqliteFtsSearchTool(DEMO_DATA, db_path=db_path)
    assert first.size == 16
    expected = _ranked(first.search_with_intent(_request(where)))
    first.close()
    second = SqliteFtsSearchTool(DEMO_DATA, db_path=db_path)
    assert _ranked(second.search_with_intent(_request(where))) == expected
    target = next(item for item in _SAMPLE if len(item.name or "") >= 2)
    assert second.search_text(target.name, top_k=3)
    second.close()
