"""Config helpers honor environment overrides."""

from __future__ import annotations

from search_agent.config import budget_from_env, recall_pool_size_from_env


def test_budget_scales_with_top_k(monkeypatch):
    monkeypatch.delenv("SEARCH_AGENT_TARGET_QUALIFIED", raising=False)
    assert budget_from_env(5).target_qualified == 20
    assert budget_from_env(50).target_qualified == 100


def test_recall_pool_size_scales_with_top_k(monkeypatch):
    monkeypatch.delenv("SEARCH_AGENT_RECALL_POOL_SIZE", raising=False)
    assert recall_pool_size_from_env(10) == 30
    assert recall_pool_size_from_env(20) == 60


def test_recall_pool_size_env_override(monkeypatch):
    monkeypatch.setenv("SEARCH_AGENT_RECALL_POOL_SIZE", "7")
    assert recall_pool_size_from_env(10) == 7


def test_budget_env_override(monkeypatch):
    monkeypatch.setenv("SEARCH_AGENT_MAX_ITERATIONS", "7")
    monkeypatch.setenv("SEARCH_AGENT_TIME_BUDGET_SEC", "3.5")
    budget = budget_from_env(10)
    assert budget.max_iterations == 7
    assert budget.time_budget_sec == 3.5
