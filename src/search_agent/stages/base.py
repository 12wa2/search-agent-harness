"""Shared deps for stages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from search_agent.contract.tool import SearchTool
from search_agent.core.budget import BudgetState
from search_agent.llm.client import LlmClient


@dataclass
class Deps:
    llm: LlmClient
    tool: SearchTool
    budget_state: BudgetState
    # Optional observability hook: ``event_sink(event_name, state)`` fires after
    # each stage completes and on degradation. Must not mutate state.
    event_sink: Callable[[str, dict[str, Any]], None] | None = None


__all__ = ["Deps"]
