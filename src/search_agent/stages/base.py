"""Shared deps for stages."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import Any, Callable

from search_agent.contract.tool import SearchTool
from search_agent.core.ablation import AblationSwitches
from search_agent.core.budget import BudgetState
from search_agent.core.experience import ExperienceStore
from search_agent.llm.client import LlmClient


@dataclass
class Deps:
    llm: LlmClient
    tool: SearchTool
    budget_state: BudgetState
    #: Optional strategy memory: (query family, strategy) -> observed yield.
    experience: ExperienceStore | None = None
    #: Offline ablation switches; ``None`` = production behaviour.
    ablation: AblationSwitches | None = None
    # Optional observability hook: ``event_sink(event_name, state)`` fires after
    # each stage completes and on degradation. Must not mutate state.
    event_sink: Callable[[str, dict[str, Any]], None] | None = None
    #: Cooperative per-run cancellation checked at stage / LLM boundaries.
    cancel_event: Event | None = None


__all__ = ["Deps"]
