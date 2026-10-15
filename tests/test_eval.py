"""Tests for the offline evaluation pipelines (single-pass vs full agent)."""

import io

from search_agent.demo import DEMO_DATA, DEMO_QUERIES, ScriptedLlmClient
from search_agent.eval import run_agent, run_eval, run_single_pass
from search_agent.tools.memory import InMemorySearchTool

QUERY = DEMO_QUERIES[0]


def _tool() -> InMemorySearchTool:
    return InMemorySearchTool(DEMO_DATA)


def test_single_pass_baseline_produces_results():
    result = run_single_pass(ScriptedLlmClient(), _tool(), QUERY, top_k=10)
    assert result.qualified > 0
    assert result.final_count > 0
    assert result.llm_calls > 0
    assert result.degraded_reason is None


def test_agent_loop_produces_results():
    result = run_agent(ScriptedLlmClient(), _tool(), QUERY, top_k=10)
    assert result.qualified > 0
    assert result.final_count > 0
    assert result.degraded_reason is None


def test_agent_loop_never_judges_fewer_than_single_pass():
    tool = _tool()
    baseline = run_single_pass(ScriptedLlmClient(), tool, QUERY, top_k=10)
    agent = run_agent(ScriptedLlmClient(), tool, QUERY, top_k=10)
    assert agent.qualified >= baseline.qualified


def test_run_eval_text_and_markdown_reports():
    text = io.StringIO()
    assert run_eval([QUERY], top_k=5, stream=text) == 0
    body = text.getvalue()
    assert "单趟基线" in body
    assert "Agent 全循环" in body

    markdown = io.StringIO()
    assert run_eval([QUERY], top_k=5, markdown=True, stream=markdown) == 0
    assert "| Q1 |" in markdown.getvalue()
