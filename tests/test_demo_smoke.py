"""End-to-end smoke tests: scripted LLM + in-memory tool + CLI."""

from __future__ import annotations

import json

from search_agent.cli import main
from search_agent.demo import DEMO_DATA, DEMO_QUERIES, ScriptedLlmClient, run_demo
from search_agent.models.api import SearchRequest
from search_agent.service import SearchAgentService
from search_agent.tools.memory import InMemorySearchTool


def test_demo_runs_end_to_end():
    resp = run_demo(DEMO_QUERIES[0], top_k=5)
    assert resp.items
    assert resp.total >= len(resp.items) >= 1
    debug = resp.debug or {}
    trace = debug.get("agentTrace") or {}
    assert trace.get("final", {}).get("mergedPoolSize", 0) > 0
    metrics = debug.get("agentMetrics") or {}
    assert metrics["rounds"]["iterations"] >= 1
    assert (resp.items[0].matchExplanation or {}).get("matchedAbilities")


def test_event_sink_receives_full_stage_timeline():
    events: list[str] = []
    run_demo(DEMO_QUERIES[0], top_k=5, event_sink=lambda event, state: events.append(event))
    expected = {
        "understand",
        "plan",
        "execute",
        "judge",
        "evaluate",
        "reflect",
        "revise",
        "finalize",
    }
    assert expected <= set(events)


def test_scripted_client_implements_all_four_roles():
    client = ScriptedLlmClient()
    service = SearchAgentService(
        tool=InMemorySearchTool(DEMO_DATA),
        llm_client=client,
    )
    resp = service.search(SearchRequest(query=DEMO_QUERIES[1], topK=5, details=True))
    assert resp.items
    roles = {name for name, _ in client.calls}
    assert roles == {"agent_understand", "agent_plan", "agent_judge", "agent_reflect"}


def test_scripted_client_merges_follow_up_history():
    client = ScriptedLlmClient()
    result = client.complete_json(
        system="",
        user=json.dumps(
            {
                "query": "最好有大厂背景",
                "conversation_history": [
                    {
                        "turn": 0,
                        "query": DEMO_QUERIES[0],
                        "conditions": {},
                        "result_count": 5,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        schema_name="agent_understand",
        schema={},
    )

    abilities = {ability["name"] for ability in result["must_abilities"]}
    assert result["raw_query"] == "最好有大厂背景"
    assert "高并发系统设计" in abilities
    assert any(
        source["value"] == "一线互联网大厂"
        for source in result["talent_profile"]["source_hypotheses"]
    )


def test_cli_demo_repeat_shows_strategy_memory(capsys):
    exit_code = main(["demo", DEMO_QUERIES[0], "--top-k", "5", "--repeat", "2"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "第 2/2 次" in out
    assert "runs=2" in out


def test_cli_search_offline(capsys):
    exit_code = main(["search", "高并发 后端工程师", "--offline", "--top-k", "5"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "understand" in out
    assert "finalize" in out
