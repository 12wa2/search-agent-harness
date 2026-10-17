"""Public contract tests for the Hybrid V2 adapter seam."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    ToolCandidate,
)
from search_agent.integrations import hybrid_v2
from search_agent.models.api import CandidateItem
from search_agent.models.ir import CandidateSearchIR, StructuredConditionIR, WhereNode


class InternalRequest(BaseModel):
    intent: dict[str, Any]
    query_text: str
    top_k: int


class InternalRerankRequest(BaseModel):
    query_text: str
    top_k: int
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class InternalResult(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    total: int = 0
    quality: str = "rich"


class FakeService:
    def search_with_intent(self, req: InternalRequest) -> InternalResult:
        assert req.query_text == "找后端工程师"
        assert req.intent["where"]["condition"]["field"] == "work_years"
        return InternalResult(
            items=[
                {
                    "doc_id": "c001",
                    "score": 0.93,
                    "item": {"docId": "c001", "score": 0.93, "name": "候选人 A"},
                }
            ],
            total=1,
        )

    def rerank_pool(self, req: InternalRerankRequest) -> InternalResult:
        assert req.query_text == "找后端工程师"
        assert req.candidates[0]["doc_id"] == "c001"
        return InternalResult(items=list(req.candidates), total=len(req.candidates))


def _ir() -> CandidateSearchIR:
    return CandidateSearchIR(
        where=WhereNode(
            type="leaf",
            condition=StructuredConditionIR(field="work_years", operator="gte", values=[3]),
        )
    )


def _request() -> SearchToolRequest:
    return SearchToolRequest(
        intent=_ir(),
        query_text="找后端工程师",
        company_id=1,
        top_k=5,
        routing=0,
    )


def test_adapter_translates_search_models_in_both_directions() -> None:
    adapter = hybrid_v2.HybridV2ToolAdapter(
        service=FakeService(),
        request_model=InternalRequest,
        rerank_request_model=InternalRerankRequest,
    )

    result = adapter.search_with_intent(_request())

    assert result.total == 1
    assert result.items[0].doc_id == "c001"
    assert result.items[0].item.name == "候选人 A"


def test_adapter_translates_rerank_models() -> None:
    adapter = hybrid_v2.HybridV2ToolAdapter(
        service=FakeService(),
        request_model=InternalRequest,
        rerank_request_model=InternalRerankRequest,
    )
    candidate = ToolCandidate(
        doc_id="c001",
        score=0.93,
        item=CandidateItem(docId="c001", score=0.93, name="候选人 A"),
    )

    result = adapter.rerank_pool(
        RerankToolRequest(
            query_text="找后端工程师",
            candidates=[candidate],
            job_context={},
            top_k=5,
            routing=0,
        )
    )

    assert result.total == 1
    assert result.items[0].doc_id == "c001"


def test_missing_hybrid_backend_error_is_actionable(monkeypatch) -> None:
    def fail_import(name: str) -> None:
        raise ImportError(name)

    monkeypatch.setattr(hybrid_v2.importlib, "import_module", fail_import)
    with pytest.raises(RuntimeError, match="Hybrid V2 service"):
        hybrid_v2.build_hybrid_v2_tool(no_graph=False)
