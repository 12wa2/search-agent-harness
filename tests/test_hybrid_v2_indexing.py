"""Tests for the Hybrid V2 index builder, converter, and doctor checks."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import pytest

from search_agent.contract.tool import SearchToolRequest
from search_agent.demo import DEMO_DATA
from search_agent.hybrid_v2.config import HybridV2Config
from search_agent.hybrid_v2.embedding import HashEmbedder
from search_agent.hybrid_v2.es_client import ElasticsearchClient
from search_agent.hybrid_v2.field_policy import FIELD_POLICIES
from search_agent.hybrid_v2.hits import hit_to_candidate
from search_agent.hybrid_v2.indexing import (
    HybridV2Indexer,
    _resolve_property,
    build_candidate_index_mapping,
    convert_candidates_to_documents,
)
from search_agent.hybrid_v2.service import HybridV2SearchService
from search_agent.models.ir import (
    CandidateSearchIR,
    SemanticConditionIR,
    StructuredConditionIR,
    WhereNode,
)


def _config(*, vector_mode: str = "flat", dims: int = 8) -> HybridV2Config:
    return replace(
        HybridV2Config.from_env(),
        es_url="http://example.invalid:9200",
        es_index="candidate-test",
        embedding_provider="hash",
        embedding_dims=dims,
        vector_mode=vector_mode,
        tenant_field="company_id",
        entity_join_field="entity_join",
        soft_delete_field="deleted",
    )


def _record() -> dict[str, Any]:
    return {
        "docId": "c-index-1",
        "name": "Candidate A",
        "workYears": 6,
        "currentResidence": "Shanghai",
        "summary": "Backend engineer with high concurrency experience",
        "skills": [{"name": "Python"}, {"name": "Elasticsearch"}],
        "workExperiences": [
            {
                "company_name": "Acme",
                "job_title": "Backend Engineer",
                "description": "Built high concurrency services",
                "start_time": "2021-01-01",
                "end_time": "2023-12-31",
            },
            {
                "company_name": "Beta",
                "job_title": "Senior Backend Engineer",
                "description": "Owned search platform",
                "start_time": "2024-01-01",
                "end_time": "至今",
            },
        ],
        "projectExperiences": [
            {
                "project_name": "Search Platform",
                "description": "Hybrid retrieval with Elasticsearch",
            }
        ],
        "educations": [
            {
                "school": "Example University",
                "major": "Computer Science",
                "degree": "本科",
            }
        ],
        "certificates": [{"name": "Cloud Architect"}],
        "languages": [{"name": "English"}],
        "expectedPositions": ["Backend Engineer"],
        "expectedCities": ["Shanghai"],
        "expectedSalary": "25k-35k",
    }


def test_mapping_covers_every_query_field_policy() -> None:
    properties = build_candidate_index_mapping(embedding_dims=8)["mappings"]["properties"]
    paths: list[str] = []
    for policy in FIELD_POLICIES.values():
        paths.extend([policy.es_field, policy.keyword_field or ""])
    missing = [
        path
        for path in dict.fromkeys(paths)
        if path and _resolve_property(properties, path) is None
    ]
    assert missing == []


def test_mapping_contains_indexed_vectors_and_mode_specific_fields() -> None:
    nested = build_candidate_index_mapping(embedding_dims=16, vector_mode="nested")
    flat = build_candidate_index_mapping(embedding_dims=16, vector_mode="flat")

    assert "knn" not in nested["settings"]["index"]
    assert (
        nested["mappings"]["properties"]["work_segments"]["properties"]["vector"]["index"] is True
    )
    assert nested["mappings"]["properties"]["work_segments"]["type"] == "nested"
    assert "work_vector" in flat["mappings"]["properties"]
    assert "work_segments" not in flat["mappings"]["properties"]


def test_nested_conversion_builds_segments_and_work_stats() -> None:
    config = _config(vector_mode="nested")
    documents = convert_candidates_to_documents(
        [_record()],
        company_id=9,
        config=config,
        embedder=HashEmbedder(8),
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    document = documents[0]

    assert document["company_id"] == 9
    assert document["work_stats"]["segment_count"] == 2
    assert document["work_stats"]["latest_work_company"] == "Beta"
    assert len(document["work_segments"]) == 2
    assert len(document["project_segments"]) == 1
    assert len(document["education_segments"]) == 1
    assert len(document["work_segments"][0]["vector"]) == 8
    assert len(document["skills_vector"]) == 8
    assert document["educations"][0]["degree"]["code"] == 5

    item = hit_to_candidate(
        {"_id": document["_id"], "_score": 1.0, "_source": document},
        score_max=1.0,
    )
    assert item.name == "Candidate A"
    assert item.workYears == 6
    assert item.expectedPositions == ["Backend Engineer"]
    assert item.educations[0]["degree"] == "本科"


def test_flat_conversion_averages_vectors() -> None:
    documents = convert_candidates_to_documents(
        [_record()],
        company_id=1,
        config=_config(vector_mode="flat"),
        embedder=HashEmbedder(8),
        as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    document = documents[0]

    assert len(document["work_vector"]) == 8
    assert len(document["project_vector"]) == 8
    assert len(document["education_vector"]) == 8
    assert "work_segments" not in document


class FakeIndices:
    def __init__(self, owner: "FakeElasticsearch") -> None:
        self.owner = owner

    def exists(self, *, index: str) -> bool:
        return self.owner.exists and index == self.owner.index

    def create(
        self,
        *,
        index: str,
        settings: dict[str, Any],
        mappings: dict[str, Any],
        aliases: dict[str, Any] | None,
    ) -> None:
        del aliases
        self.owner.exists = True
        self.owner.index = index
        self.owner.settings = settings
        self.owner.mapping = mappings

    def delete(self, *, index: str) -> None:
        assert index == self.owner.index
        self.owner.exists = False
        self.owner.documents.clear()

    def refresh(self, *, index: str) -> None:
        assert index == self.owner.index
        self.owner.refreshes += 1

    def get_mapping(self, *, index: str) -> dict[str, Any]:
        assert index == self.owner.index
        return {index: {"mappings": self.owner.mapping}}


class FakeElasticsearch:
    def __init__(self) -> None:
        self.exists = False
        self.index = ""
        self.settings: dict[str, Any] = {}
        self.mapping: dict[str, Any] = {}
        self.documents: dict[str, dict[str, Any]] = {}
        self.bulk_calls = 0
        self.refreshes = 0
        self.indices = FakeIndices(self)

    def info(self) -> dict[str, Any]:
        return {"cluster_name": "test", "version": {"number": "8.15.0"}}

    def bulk(
        self,
        *,
        index: str,
        operations: list[dict[str, Any]],
        refresh: str | bool,
    ) -> dict[str, Any]:
        assert index == self.index
        assert len(operations) % 2 == 0
        self.bulk_calls += 1
        items: list[dict[str, Any]] = []
        for offset in range(0, len(operations), 2):
            action = operations[offset]["index"]
            source = operations[offset + 1]
            self.documents[str(action["_id"])] = dict(source)
            items.append({"index": {"status": 201}})
        if refresh:
            self.refreshes += 1
        return {"errors": False, "items": items}

    def count(self, *, index: str) -> dict[str, int]:
        assert index == self.index
        return {"count": len(self.documents)}

    def close(self) -> None:
        return None


class FakeSearchClient:
    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []

    def search(self, *, index: str, body: dict[str, Any]) -> dict[str, Any]:
        assert index == "candidate-test"
        self.bodies.append(body)
        return {"hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}}


def test_search_client_keeps_track_total_hits_in_the_request_body() -> None:
    fake = FakeSearchClient()
    client = ElasticsearchClient(_config(), client=fake)

    client.search({"query": {"match_all": {}}, "track_total_hits": True})

    assert fake.bodies == [{"query": {"match_all": {}}, "track_total_hits": True}]


def test_search_client_adds_track_total_hits_when_absent() -> None:
    fake = FakeSearchClient()
    client = ElasticsearchClient(_config(), client=fake)

    client.search({"query": {"match_all": {}}})

    assert fake.bodies[0]["track_total_hits"] is True


def test_indexer_creates_bulk_loads_and_reports_healthy() -> None:
    fake = FakeElasticsearch()
    config = _config(vector_mode="flat")
    admin = ElasticsearchClient(config, client=fake)
    indexer = HybridV2Indexer(config, client=admin, embedder=HashEmbedder(8))

    report = indexer.index_records([_record()], company_id=17, batch_size=1)
    doctor = indexer.doctor()

    assert report.created is True
    assert report.documents == 1
    assert fake.settings["index"]["number_of_shards"] == 1
    assert fake.bulk_calls == 1
    assert fake.documents["c-index-1"]["company_id"] == 17
    assert doctor.ok is True
    assert {check.name for check in doctor.checks} == {
        "cluster",
        "index",
        "mapping",
        "vectors",
        "documents",
    }


def test_indexer_refreshes_once_after_all_batches() -> None:
    fake = FakeElasticsearch()
    config = _config(vector_mode="flat")
    indexer = HybridV2Indexer(
        config,
        client=ElasticsearchClient(config, client=fake),
        embedder=HashEmbedder(8),
    )
    first = _record()
    second = _record()
    second["docId"] = "c-index-2"
    second["name"] = "Candidate B"

    report = indexer.index_records(
        [first, second],
        batch_size=1,
        refresh="wait_for",
    )

    assert report.documents == 2
    assert fake.bulk_calls == 2
    assert fake.refreshes == 1


def test_indexer_detects_vector_dimension_mismatch() -> None:
    fake = FakeElasticsearch()
    config = _config(vector_mode="flat", dims=8)
    admin = ElasticsearchClient(config, client=fake)
    indexer = HybridV2Indexer(config, client=admin, embedder=HashEmbedder(8))
    indexer.create_index()
    fake.mapping["properties"]["work_vector"]["dims"] = 4

    report = indexer.doctor()

    assert report.ok is False
    vectors = next(check for check in report.checks if check.name == "vectors")
    assert vectors.ok is False
    assert "work_vector dims=4" in vectors.detail


def test_indexer_loads_the_bundled_jsonl_corpus() -> None:
    fake = FakeElasticsearch()
    config = _config(vector_mode="flat")
    indexer = HybridV2Indexer(
        config,
        client=ElasticsearchClient(config, client=fake),
        embedder=HashEmbedder(8),
    )

    report = indexer.index_file(DEMO_DATA, company_id=1)

    assert report.documents == 16
    assert len(fake.documents) == 16
    assert fake.documents["c001"]["overview"]["name"]


@pytest.mark.es
def test_real_elasticsearch_hybrid_v2_roundtrip() -> None:
    elasticsearch = pytest.importorskip("elasticsearch")
    es_url = os.environ.get("SEARCH_AGENT_ES_URL", "http://127.0.0.1:9200")
    client = elasticsearch.Elasticsearch(es_url)
    if not client.ping():
        client.close()
        pytest.skip(f"Elasticsearch not reachable at {es_url}")

    index_name = f"hybrid-v2-roundtrip-{os.getpid()}"
    config = replace(
        _config(vector_mode="flat", dims=16),
        es_url=es_url,
        es_index=index_name,
    )
    admin = ElasticsearchClient(config, client=client)
    indexer = HybridV2Indexer(config, client=admin, embedder=HashEmbedder(16))
    try:
        report = indexer.index_file(DEMO_DATA, recreate=True)
        assert report.documents == 16
        assert indexer.doctor().ok is True

        service = HybridV2SearchService(
            config=config,
            es_client=admin,
            embedder=HashEmbedder(16),
        )
        intent = CandidateSearchIR(
            where=WhereNode(
                type="leaf",
                condition=StructuredConditionIR(
                    field="work_years",
                    operator="gte",
                    values=[3],
                ),
            ),
            semantic=[
                SemanticConditionIR(
                    text="高并发 后端 架构",
                    fields=["work_vector"],
                    weight=1.0,
                )
            ],
        )
        result = service.search_with_intent(
            SearchToolRequest(
                intent=intent,
                query_text="找 3 年以上高并发后端工程师",
                company_id=1,
                top_k=5,
                routing=0,
            )
        )
        assert result.total > 0
        assert result.items
    finally:
        admin.delete_index(index_name)
        client.close()
