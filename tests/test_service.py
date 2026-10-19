"""SearchAgentService adapter tests: req -> Harness -> SearchResponse."""

from __future__ import annotations

import json

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.conversation_memory import ConversationStore
from search_agent.llm.client import FakeLlmClient
from search_agent.models.api import CandidateItem, SearchRequest, SearchResponse
from search_agent.models.ir import (
    CandidateSearchIR,
    StructuredConditionIR,
    WhereNode,
)
from search_agent.runner import LoopRunner
from search_agent.service import SearchAgentService
from search_agent.session import AgentSessionStore


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
    assert resp.topK == 20
    assert resp.total == 5
    assert len(resp.items) == 5
    assert all(isinstance(item, CandidateItem) for item in resp.items)
    # debug contains agentTrace
    assert resp.debug is not None
    assert "agentTrace" in resp.debug
    assert resp.debug["agentTrace"]["final"]["rerankUsed"] is True


def test_service_backfills_conversation_id_and_second_turn_uses_history():
    llm = _llm()
    conversations = ConversationStore(connect_redis=False)
    sessions = AgentSessionStore(connect_redis=False)
    svc = SearchAgentService(
        tool=_FakeTool([_candidate(f"d{i}") for i in range(5)]),
        llm_client=llm,
        runner=LoopRunner(),
        conversation_store=conversations,
        session_store=sessions,
    )

    first = svc.search(SearchRequest(query="Python engineer", companyId=39978, hrId=7, topK=5))
    assert first.conversationId
    assert first.conversationTurns == 1

    second = svc.search(
        SearchRequest(
            query="prefer big tech",
            companyId=39978,
            hrId=7,
            topK=5,
            conversationId=first.conversationId,
        )
    )
    assert second.conversationId == first.conversationId
    assert second.conversationTurns == 2

    understands = [json.loads(user) for name, user in llm.calls if name == "agent_understand"]
    assert understands[1]["conversation_history"][0]["query"] == "Python engineer"


def test_service_does_not_reuse_conversation_across_tenants():
    llm = _llm()
    svc = SearchAgentService(
        tool=_FakeTool([_candidate(f"d{i}") for i in range(5)]),
        llm_client=llm,
        runner=LoopRunner(),
        conversation_store=ConversationStore(connect_redis=False),
        session_store=AgentSessionStore(connect_redis=False),
    )

    first = svc.search(SearchRequest(query="Python engineer", companyId=39978, topK=5))
    second = svc.search(
        SearchRequest(
            query="prefer big tech",
            companyId=40000,
            topK=5,
            conversationId=first.conversationId,
        )
    )

    assert second.conversationId != first.conversationId
    understands = [json.loads(user) for name, user in llm.calls if name == "agent_understand"]
    assert "conversation_history" not in understands[1]


def test_service_does_not_reuse_hr_scoped_conversation_without_hr_id():
    llm = _llm()
    svc = SearchAgentService(
        tool=_FakeTool([_candidate(f"d{i}") for i in range(5)]),
        llm_client=llm,
        runner=LoopRunner(),
        conversation_store=ConversationStore(connect_redis=False),
        session_store=AgentSessionStore(connect_redis=False),
    )

    first = svc.search(SearchRequest(query="Python engineer", companyId=39978, hrId=7, topK=5))
    second = svc.search(
        SearchRequest(
            query="prefer big tech",
            companyId=39978,
            topK=5,
            conversationId=first.conversationId,
        )
    )

    assert second.conversationId != first.conversationId
    understands = [json.loads(user) for name, user in llm.calls if name == "agent_understand"]
    assert "conversation_history" not in understands[1]


def test_service_does_not_create_pagination_session_without_page_size():
    svc = SearchAgentService(
        tool=_FakeTool([_candidate(f"d{i}") for i in range(5)]),
        llm_client=_llm(),
        runner=LoopRunner(),
        conversation_store=ConversationStore(connect_redis=False),
        session_store=AgentSessionStore(connect_redis=False),
    )

    resp = svc.search(SearchRequest(query="Python engineer", companyId=39978, topK=5))

    assert resp.queryToken is None
    assert resp.page is None


def test_service_turn_index_continues_after_memory_window():
    conversations = ConversationStore(connect_redis=False, max_turns=2)
    svc = SearchAgentService(
        tool=_FakeTool([_candidate(f"d{i}") for i in range(5)]),
        llm_client=_llm(),
        runner=LoopRunner(),
        conversation_store=conversations,
        session_store=AgentSessionStore(connect_redis=False),
    )

    conv_id = None
    for index in range(4):
        resp = svc.search(
            SearchRequest(
                query=f"query {index}",
                companyId=39978,
                topK=5,
                conversationId=conv_id,
            )
        )
        conv_id = resp.conversationId

    memory = conversations.load(conv_id)
    assert memory is not None
    assert [turn.turn_index for turn in memory.turns] == [2, 3]
    assert resp.conversationTurns == 4


def test_service_returns_first_page_and_reusable_session_token():
    sessions = AgentSessionStore(connect_redis=False)
    svc = SearchAgentService(
        tool=_FakeTool([_candidate(f"d{i}", 0.9 - i * 0.05) for i in range(5)]),
        llm_client=_llm(),
        runner=LoopRunner(),
        conversation_store=ConversationStore(connect_redis=False),
        session_store=sessions,
    )

    resp = svc.search(SearchRequest(query="Python engineer", companyId=39978, topK=5, pageSize=2))

    assert len(resp.items) == 2
    assert resp.total == 5
    assert resp.queryToken
    assert resp.page is not None
    assert resp.page.currentPage == 1
    assert resp.page.totalPages == 3
    assert resp.page.hasMore is True

    second = sessions.get_page(resp.queryToken, offset=2, limit=2)
    assert [item.docId for item in second.items] == ["d2", "d3"]


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


def test_service_skill_only_query_returns_clarification_options():
    tool = _FakeTool([])
    svc = SearchAgentService(tool=tool, llm_client=_llm(), runner=LoopRunner())

    req = SearchRequest(query="找会 Python 的", companyId=39978, topK=8, details=True)
    resp = svc.search(req)

    assert resp.topK == 8
    assert resp.total == 0
    assert resp.clarification is not None
    assert resp.clarification["needClarification"] is True
    assert len(resp.clarification["options"]) == 3
    assert resp.clarification["options"][0]["label"] == "后端开发"
    assert "Python" in resp.clarification["options"][0]["query"]


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
