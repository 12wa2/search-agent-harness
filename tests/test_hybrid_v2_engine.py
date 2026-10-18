"""Offline tests for the bundled Hybrid V2 retrieval engine."""

from __future__ import annotations

from dataclasses import replace

from search_agent.contract.tool import SearchToolRequest
from search_agent.hybrid_v2 import HybridV2Config, HybridV2SearchService, hybrid_v2_configured
from search_agent.hybrid_v2.embedding import HashEmbedder
from search_agent.hybrid_v2.hits import hit_to_candidate
from search_agent.models.ir import (
    CandidateSearchIR,
    SemanticConditionIR,
    StructuredConditionIR,
    WhereNode,
)


class FakeEsClient:
    def __init__(self, response: dict) -> None:
        self.response = response
        self.bodies: list[dict] = []

    def search(self, body: dict) -> dict:
        self.bodies.append(body)
        return self.response


def _config() -> HybridV2Config:
    return replace(
        HybridV2Config.from_env(),
        es_url="http://example.invalid:9200",
        tenant_field="",
        entity_join_field="",
        soft_delete_field="",
        rerank_provider="local",
        rerank_min_confidence=0.0,
    )


def _source() -> dict:
    return {
        "candidate_profile_id": 42,
        "overview": {
            "name": "候选人 A",
            "work_years": 5,
            "city": {"name": "上海"},
        },
        "skills": [{"name": "Python"}, {"name": "Elasticsearch"}],
        "work_experiences": [
            {
                "company": "示例科技",
                "job": "后端工程师",
                "description": "负责高并发 Python 服务",
                "start_time": "2021-01-01",
                "end_time": "至今",
            }
        ],
        "intention": {"cities": [{"name": "上海"}]},
        "semantic_summary_text": "高并发后端开发经验",
        "work_stats": {"segment_count": 1, "latest_work_is_current": True},
    }


def test_hybrid_v2_is_opt_in_only_when_an_es_endpoint_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("SEARCH_AGENT_HYBRID_V2_ES_URL", raising=False)
    monkeypatch.delenv("SEARCH_AGENT_ES_URL", raising=False)
    assert hybrid_v2_configured() is False

    monkeypatch.setenv("SEARCH_AGENT_HYBRID_V2_ES_URL", "http://localhost:9200")
    assert hybrid_v2_configured() is True


def test_hit_conversion_supports_payload_and_exposes_structured_fields() -> None:
    item = hit_to_candidate(
        {
            "_id": "doc-1",
            "_score": 1.5,
            "_source": {"payload": '{"candidate_profile_id": 7, "overview": {"name": "B"}}'},
        },
        match_rate_baseline=40,
    )

    assert item.docId == "doc-1"
    assert item.candidateProfileId == "7"
    assert item.name == "B"
    assert item.matchRate == 100


def test_hybrid_v2_service_compiles_ir_recalls_and_attaches_hard_matches() -> None:
    response = {
        "hits": {
            "total": {"value": 1, "relation": "eq"},
            "hits": [
                {
                    "_id": "doc-42",
                    "_score": 1.5,
                    "_source": _source(),
                }
            ],
        }
    }
    es_client = FakeEsClient(response)
    service = HybridV2SearchService(
        config=_config(),
        es_client=es_client,
        embedder=HashEmbedder(dims=16),
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
                text="高并发 Python 后端",
                fields=["work_vector"],
                weight=1.0,
            )
        ],
    )

    result = service.search_with_intent(
        SearchToolRequest(
            intent=intent,
            query_text="找有 3 年以上经验的后端工程师",
            company_id=1,
            top_k=5,
            routing=0,
            details=True,
        )
    )

    assert result.total == 1
    assert result.items[0].doc_id == "doc-42"
    assert result.items[0].item.name == "候选人 A"
    assert result.items[0].hard_matches[0]["matched"] is True
    assert result.es_query is not None
    assert "<vector len=16>" in str(result.es_query)
    assert es_client.bodies
    assert es_client.bodies[0]["size"] >= 5


def test_hybrid_v2_service_can_skip_rerank() -> None:
    es_client = FakeEsClient(
        {
            "hits": {
                "total": {"value": 1, "relation": "eq"},
                "hits": [{"_id": "doc-42", "_score": 1.0, "_source": _source()}],
            }
        }
    )
    service = HybridV2SearchService(
        config=_config(),
        es_client=es_client,
        embedder=HashEmbedder(dims=8),
    )
    intent = CandidateSearchIR(
        where=WhereNode(
            type="leaf",
            condition=StructuredConditionIR(field="work_years", operator="gte", values=[3]),
        )
    )

    result = service.search_with_intent(
        SearchToolRequest(
            intent=intent,
            query_text="后端工程师",
            company_id=1,
            top_k=2,
            routing=0,
            skip_rerank=True,
        )
    )

    assert result.items[0].item.matchRate == 100
    assert result.items[0].item.score == 1.0


def test_search_with_intent_returns_the_recall_pool_not_only_top_k() -> None:
    response = {
        "hits": {
            "total": {"value": 10, "relation": "eq"},
            "hits": [
                {"_id": f"doc-{index}", "_score": 1.0, "_source": _source()}
                for index in range(10)
            ],
        }
    }
    service = HybridV2SearchService(
        config=_config(),
        es_client=FakeEsClient(response),
        embedder=HashEmbedder(dims=8),
    )
    intent = CandidateSearchIR(
        where=WhereNode(
            type="leaf",
            condition=StructuredConditionIR(field="work_years", operator="gte", values=[3]),
        )
    )

    result = service.search_with_intent(
        SearchToolRequest(
            intent=intent,
            query_text="后端工程师",
            company_id=1,
            top_k=2,
            recall_pool_size=8,
            routing=0,
            skip_rerank=True,
        )
    )

    assert len(result.items) == 8
    assert result.total == 10
