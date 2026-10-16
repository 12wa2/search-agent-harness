"""Tests for the offline evaluation pipelines (single-pass vs full agent)."""

import io

from search_agent.corpus import DEFAULT_QUERIES, generate_corpus
from search_agent.demo import DEMO_DATA, DEMO_QUERIES, ScriptedLlmClient
from search_agent.eval import run_agent, run_eval, run_single_pass
from search_agent.oracle import novelty, score_against_oracle
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


def test_oracle_score_counts_overlap():
    corpus = generate_corpus(size=36, seed=7, queries=DEFAULT_QUERIES[:2])
    spec = corpus.queries[0]
    relevant = corpus.relevant(spec)
    score = score_against_oracle(corpus, spec, relevant[:2])
    assert score.found == 2
    assert score.recall == 2 / len(relevant)
    assert score.precision == 1.0

    empty = score_against_oracle(corpus, spec, [])
    assert empty.recall == 0.0
    assert empty.precision == 0.0


def test_run_eval_with_generated_corpus_reports_oracle_metrics():
    corpus = generate_corpus(size=36, seed=7, queries=DEFAULT_QUERIES[:2])

    text = io.StringIO()
    assert run_eval(corpus=corpus, top_k=5, stream=text) == 0
    body = text.getvalue()
    assert "Oracle 评测" in body
    assert "仅语义可见" in body
    assert "单趟基线" in body and "Agent 全循环" in body

    markdown = io.StringIO()
    assert run_eval(corpus=corpus, top_k=5, markdown=True, stream=markdown) == 0
    md = markdown.getvalue()
    assert "仅语义可见召回" in md
    assert "独有候选相关率" in md
    assert "仅全循环发现" in md
    assert "| Q1 |" in md and "| Q2 |" in md


def test_novelty_measures_agent_only_discoveries():
    corpus = generate_corpus(size=36, seed=7, queries=DEFAULT_QUERIES[:2])
    spec = corpus.queries[0]
    relevant = list(corpus.relevant(spec))
    relevant_set = set(relevant)
    noise = [row["docId"] for row in corpus.records() if row["docId"] not in relevant_set][:2]
    discovered = novelty(corpus, spec, [], relevant[:3] + noise)
    assert discovered.unique == 5
    assert discovered.relevant == 3
    assert discovered.relevance_rate == 3 / 5
    overlap = novelty(corpus, spec, relevant[:2], relevant[:3] + noise)
    assert overlap.unique == 3
    assert overlap.relevant == 1


def test_surfaced_ids_cover_delivered():
    baseline = run_single_pass(ScriptedLlmClient(), _tool(), QUERY, top_k=10)
    agent = run_agent(ScriptedLlmClient(), _tool(), QUERY, top_k=10)
    assert set(baseline.doc_ids) <= set(baseline.surfaced_ids)
    assert set(agent.doc_ids) <= set(agent.surfaced_ids)
    assert agent.surfaced_ids


def test_single_pass_handles_query_without_hard_constraints():
    """A query with no hard constraint still gets a comparable baseline path."""
    result = run_single_pass(ScriptedLlmClient(), _tool(), "找后端工程师，最好来自大厂", top_k=5)
    assert result.final_count > 0
    assert result.llm_calls > 0
    assert result.degraded_reason is None
