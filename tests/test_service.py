"""SearchAgentService adapter tests: req -> Harness -> SearchResponse."""

from __future__ import annotations

import json

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.conversation_memory import ConversationStore, ConversationTurn
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


def _hard_ir():
    return CandidateSearchIR(
        where=WhereNode(
            type="and",
            children=[
                WhereNode(
                    type="leaf",
                    condition=StructuredConditionIR(
                        field="skill",
                        operator="any",
                        values=["Python"],
                    ),
                ),
                WhereNode(
                    type="leaf",
                    condition=StructuredConditionIR(
                        field="work_years",
                        operator="gte",
                        values=[5],
                    ),
                ),
                WhereNode(
                    type="leaf",
                    condition=StructuredConditionIR(
                        field="city",
                        operator="any",
                        values=["上海"],
                    ),
                ),
            ],
        )
    )


def _candidate(doc_id, score=0.8):
    return ToolCandidate(
        doc_id=doc_id,
        score=score,
        item=CandidateItem(docId=doc_id, score=score, name=f"Candidate {doc_id}"),
        hard_matches=[{"field": "skill", "matched": True}],
    )


def _hard_candidate(doc_id, score=0.8, *, satisfied=True):
    return ToolCandidate(
        doc_id=doc_id,
        score=score,
        item=CandidateItem(docId=doc_id, score=score, name=f"Candidate {doc_id}"),
        hard_matches=[
            {
                "field": "skill",
                "operator": "any",
                "expectedValues": ["Python"],
                "actualValues": ["Python"],
                "matched": True,
                "required": True,
            },
            {
                "field": "work_years",
                "operator": "gte",
                "expectedValues": [5],
                "actualValues": [7 if satisfied else 2],
                "matched": satisfied,
                "required": True,
            },
            {
                "field": "city",
                "operator": "any",
                "expectedValues": ["上海"],
                "actualValues": ["上海" if satisfied else "北京"],
                "matched": satisfied,
                "required": True,
            },
        ],
    )


class _FakeTool:
    def __init__(self, items, rerank_items=None):
        self._items = items
        self._rerank = rerank_items or items
        self.search_calls = 0
        self.last_request = None
        self.requests: list[SearchToolRequest] = []

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        self.search_calls += 1
        self.last_request = req
        self.requests.append(req)
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


def _llm(extra: dict | None = None):
    responses = {
        "hybrid_search_v2_ir": _ir().model_dump(),
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
    responses.update(extra or {})
    return FakeLlmClient(responses=responses)


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


def test_service_summary_covers_full_pool_and_persists_hard_condition_gap():
    conversations = ConversationStore(connect_redis=False)
    candidates = [_hard_candidate(f"d{i}", 0.7 - i * 0.01) for i in range(4)]
    candidates.append(_hard_candidate("violating", 0.99, satisfied=False))
    svc = SearchAgentService(
        tool=_FakeTool(candidates),
        llm_client=_llm({"hybrid_search_v2_ir": _hard_ir().model_dump()}),
        runner=LoopRunner(),
        conversation_store=conversations,
        session_store=AgentSessionStore(connect_redis=False),
    )

    resp = svc.search(
        SearchRequest(
            query="帮我找 5 年以上 Python 工程师，上海",
            companyId=39978,
            hrId=7,
            topK=5,
            pageSize=2,
            details=True,
        )
    )

    assert resp.debug["difficultyRoute"] == "direct_search"
    assert resp.total == 5
    assert len(resp.items) == 2
    assert resp.items[0].docId != "violating"
    assert resp.hardConditionSummary is not None
    assert resp.hardConditionSummary.model_dump() == {
        "status": "review_required",
        "delivered": 5,
        "satisfied": 4,
        "violated": 1,
        "unverified": 0,
        "affected": 1,
        "conditionCount": 3,
    }
    assert resp.debug["hardConditionSummary"]["delivered"] == 5

    memory = conversations.load(resp.conversationId)
    assert memory is not None
    turn = memory.turns[-1]
    assert turn.hard_condition_summary == resp.debug["hardConditionSummary"]
    assert "硬条件缺口" in turn.assistant_summary


def test_service_skips_entry_llm_for_strong_direct_query():
    cands = [_candidate(f"d{i}") for i in range(3)]
    tool = _FakeTool(cands)
    llm = _llm(
        {
            "agent_entry_route": {
                "route": "agent",
                "confidence": 0.99,
                "reason": "must not be called",
            }
        }
    )
    svc = SearchAgentService(tool=tool, llm_client=llm, runner=LoopRunner())

    resp = svc.search(
        SearchRequest(query="帮我找会 Python 的", companyId=39978, topK=3, details=True)
    )

    assert "agent_entry_route" not in [name for name, _ in llm.calls]
    assert resp.debug is not None
    assert resp.debug["difficultyRoute"] == "direct_search"
    assert resp.debug["entryRoute"]["route"] == "direct_search"
    assert resp.debug["entryRoute"]["source"] == "rule"
    assert resp.debug["agentMetrics"]["meta"]["entryLlmCalls"] == 0
    assert tool.search_calls == 1


def test_service_uses_entry_llm_review_to_veto_weak_direct_query():
    cands = [_candidate(f"d{i}") for i in range(5)]
    tool = _FakeTool(cands)
    llm = _llm(
        {
            "agent_entry_route": {
                "route": "agent",
                "confidence": 0.92,
                "reason": "缺少岗位或技能，先进入 Agent 理解",
            }
        }
    )
    svc = SearchAgentService(tool=tool, llm_client=llm, runner=LoopRunner())

    resp = svc.search(SearchRequest(query="帮我找北京的", companyId=39978, topK=5, details=True))

    call_names = [name for name, _ in llm.calls]
    assert call_names[0] == "agent_entry_route"
    assert "agent_understand" in call_names
    assert resp.debug is not None
    assert resp.debug["entryRoute"]["route"] == "agent"
    assert resp.debug["entryRoute"]["source"] == "llm"
    assert resp.debug["difficultyRoute"] != "direct_search"
    assert tool.search_calls == 1


def test_service_keeps_weak_direct_query_when_entry_llm_confirms_it():
    cands = [_candidate(f"d{i}") for i in range(3)]
    tool = _FakeTool(cands)
    llm = _llm(
        {
            "agent_entry_route": {
                "route": "direct_search",
                "confidence": 0.9,
                "reason": "地点条件足以直接检索",
            }
        }
    )
    svc = SearchAgentService(tool=tool, llm_client=llm, runner=LoopRunner())

    resp = svc.search(SearchRequest(query="帮我找北京的", companyId=39978, topK=3, details=True))

    call_names = [name for name, _ in llm.calls]
    assert call_names == ["agent_entry_route", "hybrid_search_v2_ir"]
    assert resp.debug is not None
    assert resp.debug["difficultyRoute"] == "direct_search"
    assert resp.debug["entryRoute"]["route"] == "direct_search"
    assert resp.debug["entryRoute"]["source"] == "llm"
    assert resp.debug["agentMetrics"]["meta"]["entryLlmCalls"] == 1
    assert resp.debug["agentMetrics"]["meta"]["parserLlmCalls"] == 1
    assert resp.debug["agentMetrics"]["meta"]["llmCalls"] == 2
    assert tool.search_calls == 1


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


def test_reply_turn_is_archived_but_not_injected_into_search_history():
    conversations = ConversationStore(connect_redis=False)
    conversation = conversations.create(39978, 7)
    conversations.append_turn(
        conversation.conversation_id,
        39978,
        7,
        ConversationTurn(
            turn_index=0,
            query="你好",
            assistant_summary="你好！我是 Search Agent。",
            route="GREETING",
            status="reply",
        ),
    )
    llm = _llm()
    svc = SearchAgentService(
        tool=_FakeTool([_candidate(f"d{i}") for i in range(5)]),
        llm_client=llm,
        runner=LoopRunner(),
        conversation_store=conversations,
        session_store=AgentSessionStore(connect_redis=False),
    )

    resp = svc.search(
        SearchRequest(
            query="Python engineer",
            companyId=39978,
            hrId=7,
            topK=5,
            conversationId=conversation.conversation_id,
        )
    )

    understands = [json.loads(user) for name, user in llm.calls if name == "agent_understand"]
    assert "conversation_history" not in understands[0]
    memory = conversations.load(conversation.conversation_id)
    assert memory is not None
    assert memory.title == "Python engineer"
    assert [turn.status for turn in memory.turns] == ["reply", "completed"]
    assert [turn.turn_index for turn in memory.turns] == [0, 1]
    assert resp.conversationTurns == 2


def test_service_persists_restorable_turn_snapshot():
    conversations = ConversationStore(connect_redis=False)
    svc = SearchAgentService(
        tool=_FakeTool([_candidate(f"d{i}") for i in range(5)]),
        llm_client=_llm(),
        runner=LoopRunner(),
        conversation_store=conversations,
        session_store=AgentSessionStore(connect_redis=False),
    )

    resp = svc.search(
        SearchRequest(
            query="Python engineer",
            companyId=39978,
            hrId=7,
            topK=5,
            pageSize=2,
        )
    )
    memory = conversations.load(resp.conversationId)

    assert memory is not None
    assert len(memory.turns) == 1
    turn = memory.turns[0]
    assert turn.assistant_summary.startswith("岗位：Python engineer。")
    assert turn.top_candidate_ids == ["d0", "d1", "d2", "d3", "d4"]
    assert turn.result_items[0]["docId"] == "d0"
    assert turn.result_count == 5
    assert turn.query_token == resp.queryToken
    assert turn.top_k == 5
    assert turn.status == "completed"
    assert turn.route


def test_service_passes_explicit_exclusions_and_hides_them_defensively():
    conversations = ConversationStore(connect_redis=False)
    tool = _FakeTool([_candidate(f"d{i}") for i in range(5)])
    svc = SearchAgentService(
        tool=tool,
        llm_client=_llm(),
        runner=LoopRunner(),
        conversation_store=conversations,
        session_store=AgentSessionStore(connect_redis=False),
    )
    first = svc.search(SearchRequest(query="Python engineer", companyId=39978, hrId=7, topK=5))
    conversations.set_candidate_excluded(
        first.conversationId,
        39978,
        7,
        "d2",
        excluded=True,
    )
    request_count = len(tool.requests)

    second = svc.search(
        SearchRequest(
            query="prefer big tech",
            companyId=39978,
            hrId=7,
            topK=5,
            conversationId=first.conversationId,
        )
    )

    new_requests = tool.requests[request_count:]
    assert new_requests
    assert all("d2" in request.exclude_doc_ids for request in new_requests)
    assert "d2" not in {item.docId for item in second.items}


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


def test_service_skill_only_search_uses_direct_search_route():
    llm = _llm()
    tool = _FakeTool([_candidate(f"d{i}") for i in range(5)])
    svc = SearchAgentService(tool=tool, llm_client=llm, runner=LoopRunner())

    req = SearchRequest(query="帮我找会 Python 的", companyId=39978, topK=8, details=True)
    resp = svc.search(req)

    assert resp.topK == 8
    assert resp.total == 5
    assert resp.clarification is None
    assert resp.debug is not None
    assert resp.debug["difficultyRoute"] == "direct_search"
    assert resp.debug["executedStages"] == [
        "direct_search",
        "execute",
        "finalize",
    ]
    assert resp.debug["agentMetrics"]["meta"]["llmCalls"] == 1
    assert resp.debug["agentMetrics"]["meta"]["agentLlmCalls"] == 0
    assert resp.debug["agentMetrics"]["meta"]["parserLlmCalls"] == 1
    assert resp.debug["agentMetrics"]["meta"]["totalTokens"] > 0
    assert resp.debug["directSearch"]["plan"]["skills"] == ["Python"]
    assert resp.debug["directSearch"]["plan"]["irSource"] == "hybrid_v2_parser"
    assert tool.search_calls == 1
    assert tool.last_request is not None
    assert tool.last_request.intent.where == _ir().where
    assert tool.last_request.intent.semantic == _ir().semantic
    assert [call[0] for call in llm.calls] == ["hybrid_search_v2_ir"]


def test_service_direct_search_uses_v2_parser_not_agent_stages():
    llm = FakeLlmClient(responses={"hybrid_search_v2_ir": _ir().model_dump()})
    tool = _FakeTool([_candidate("python-1")])
    svc = SearchAgentService(tool=tool, llm_client=llm, runner=LoopRunner())

    resp = svc.search(
        SearchRequest(query="帮我找会 Python 的", companyId=39978, topK=5, details=True)
    )

    assert resp.total == 1
    assert resp.debug["difficultyRoute"] == "direct_search"
    assert [call[0] for call in llm.calls] == ["hybrid_search_v2_ir"]
    assert tool.search_calls == 1


def test_service_direct_search_falls_back_when_v2_parser_fails():
    class _BoomLlm:
        def __init__(self):
            self.calls = 0

        def complete_json(self, **kwargs):
            self.calls += 1
            raise RuntimeError("parser unavailable")

    llm = _BoomLlm()
    tool = _FakeTool([_candidate("python-1")])
    svc = SearchAgentService(tool=tool, llm_client=llm, runner=LoopRunner())

    resp = svc.search(
        SearchRequest(query="帮我找会 Python 的", companyId=39978, topK=5, details=True)
    )

    assert resp.total == 1
    assert llm.calls == 2
    assert resp.debug["directSearch"]["plan"]["irSource"] == "local_rules_fallback"
    assert resp.debug["agentMetrics"]["meta"]["parserLlmCalls"] == 2
    assert resp.debug["degradedReason"] == "hybrid_v2_parse_failed"
    assert tool.search_calls == 1


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
