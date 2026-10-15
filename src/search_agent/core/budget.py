"""Budget accounting + termination."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from search_agent.models.artifacts import Budget


@dataclass
class BudgetState:
    budget: Budget
    start_time: float = field(default_factory=time.perf_counter)
    llm_calls: int = 0
    iteration: int = 0
    new_qualified_last_round: int = 0

    def elapsed_sec(self) -> float:
        return time.perf_counter() - self.start_time

    def record_llm(self, n: int = 1) -> None:
        self.llm_calls += n

    def time_exceeded(self) -> bool:
        return self.elapsed_sec() >= self.budget.time_budget_sec

    def llm_exceeded(self) -> bool:
        return self.llm_calls >= self.budget.max_llm_calls

    def iteration_exceeded(self) -> bool:
        return self.iteration >= self.budget.max_iterations


def should_finalize(
    *,
    qualified_count: int,
    has_pending_paths: bool,
    bs: BudgetState,
) -> tuple[bool, str | None]:
    """Termination check. Returns (finalize, reason).

    Reasons: target_reached / max_iterations / time_budget / llm_budget /
    no_progress (no pending paths AND no new qualified this round).
    """
    if qualified_count >= bs.budget.target_qualified:
        return True, "target_reached"
    if bs.iteration_exceeded():
        return True, "max_iterations"
    if bs.time_exceeded():
        return True, "time_budget"
    if bs.llm_exceeded():
        return True, "llm_budget"
    if not has_pending_paths and bs.new_qualified_last_round == 0:
        return True, "no_progress"
    return False, None


__all__ = ["BudgetState", "should_finalize"]
