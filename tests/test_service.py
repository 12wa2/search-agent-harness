"""SearchAgentService adapter tests: req -> Harness -> SearchResponse."""

from __future__ import annotations

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.llm.client import FakeLlmClient
from search_agent.models.api import CandidateItem, SearchRequest, SearchResponse
from search_agent.models.ir import (
    CandidateSearchIR,
    StructuredConditionIR,
    WhereNode,
)
from search_agent.runner import LoopRunner
from search_agent.service import SearchAgentService


def _ir():
    return CandidateSearchIR(
        where=WhereNode(
            type="leaf",
            condition=StructuredConditionIR(field="skill", operator="any", values=["Python"]),
        )
    )


def _candidate(doc_id, score=0.8):
    return ToolCandidate(
        doc_id=doc_id,
        score=score,
        item=CandidateItem(docId=doc_id, score=score, name=f"Candidate {doc_id}"),
        hard_matches=[{"field": "skill", "matched": True}],
    )


class _FakeTool:
    def __init__(self, items, rerank_items=None):
        self._items = items
        self._rerank = rerank_items or items

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        return SearchToolResult(
            items=self._items, total=len(self._items), quality="rich", timings_ms={"total": 100}
        )

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        return SearchToolResult(
            items=self._rerank[: req.top_k],
            total=len(self._rerank),
            quality="rich",
            timings_ms={"total": 200},
        )


def _llm():
    return FakeLlmClient(
        responses={
            "agent_understand": {
                "title": "Python engineer",
                "must_abilities": [{"name": "Python", "level": "must"}],
                "needs_clarification": False,
                "talent_profile": {"must_keywords": ["Python"]},
            },
            "agent_plan": {
                "paths": [
                    {
                        "rationale": "direct",
                        "intent": _ir().model_dump(),
                        "expected_yield": "high",
                    }
                ]
            },
            "agent_judge": {
                "judgments": [
                    {
                        "doc_id": f"d{i}",
                        "matched_abilities": ["Python"],
                        "missing_abilities": [],
                        "summary": "ok",
                    }
                    for i in range(5)
                ]
            },
        }
    )


def test_service_returns_search_response_with_items():
    cands = [_candidate(f"d{i}") for i in range(5)]
    tool = _FakeTool(cands)
    svc = SearchAgentService(tool=tool, llm_client=_llm(), runner=LoopRunner())

    req = SearchRequest(query="Python engineer", companyId=39978, topK=20, details=True)
    resp = svc.search(req)

    assert resp.query == "Python engineer"
    assert resp.total == 5
    assert len(resp.items) == 5
    assert all(isinstance(item, CandidateItem) for item in resp.items)
    # debug contains agentTrace
    assert resp.debug is not None
    assert "agentTrace" in resp.debug
    assert resp.debug["agentTrace"]["final"]["rerankUsed"] is True


def test_service_clarification_response():
    tool = _FakeTool([])
    llm = FakeLlmClient(
        responses={
            "agent_understand": {
                "title": "X",
                "must_abilities": [{"name": "Python"}],
                "needs_clarification": True,
                "clarify_questions": ["How many years?"],
                "talent_profile": {},
            }
        }
    )
    svc = SearchAgentService(tool=tool, llm_client=llm, runner=LoopRunner())

    req = SearchRequest(query="engineer", companyId=39978, topK=20)
    resp = svc.search(req)

    assert resp.total == 0
    assert resp.items == []
    assert resp.clarification is not None
    assert resp.clarification["needClarification"] is True
    assert "How many years?" in resp.clarification["questions"]


def test_service_fallback_baseline():
    class _BoomLlm:
        def complete_json(self, **kwargs):
            raise RuntimeError("LLM down")

    tool = _FakeTool([])
    fallback_called = []

    def _fallback(req):
        fallback_called.append(req)
        return SearchResponse(query=req.query, total=0, items=[])

    svc = SearchAgentService(
        tool=tool, llm_client=_BoomLlm(), runner=LoopRunner(), fallback_fn=_fallback
    )

    req = SearchRequest(query="engineer", companyId=39978, topK=20, details=True)
    resp = svc.search(req)

    assert len(fallback_called) == 1
    assert resp.query == "engineer"
    assert resp.debug is not None
    assert resp.debug.get("degradedReason") == "understand_failed"
