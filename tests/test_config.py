"""Config helpers honor environment overrides."""

from __future__ import annotations

from search_agent.config import budget_from_env


def test_budget_scales_with_top_k(monkeypatch):
    monkeypatch.delenv("SEARCH_AGENT_TARGET_QUALIFIED", raising=False)
    assert budget_from_env(5).target_qualified == 20
    assert budget_from_env(50).target_qualified == 100


def test_budget_env_override(monkeypatch):
    monkeypatch.setenv("SEARCH_AGENT_MAX_ITERATIONS", "7")
    monkeypatch.setenv("SEARCH_AGENT_TIME_BUDGET_SEC", "3.5")
    budget = budget_from_env(10)
    assert budget.max_iterations == 7
    assert budget.time_budget_sec == 3.5
