"""evaluate: termination check after judge.

Pure stage: reads accumulated pool + path statuses, asks ``should_finalize``
whether to stop or reflect. Increments nothing here (revise owns iteration++).
"""

from __future__ import annotations

from typing import Any

from search_agent.core.budget import should_finalize
from search_agent.stages.base import Deps


def run_evaluate(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    accumulated = state.get("accumulated", {})
    qualified_count = len(accumulated)
    paths = state.get("paths", [])
    has_pending = any(p.status == "pending" for p in paths)

    bs = deps.budget_state
    bs.iteration = state.get("iteration", 0)
    bs.new_qualified_last_round = state.get("new_qualified_last_round", 0)

    # Profile convergence: if the talent profile stabilized after the last
    # reflection (no new sources or soft_signals added), trigger the
    # converged final search early. Only after at least one reflect cycle.
    if state.get("profile_converged") and bs.iteration >= 1:
        return {"finalize": True, "finalize_reason": "profile_converged", "phase": "finalize"}

    finalize, reason = should_finalize(
        qualified_count=qualified_count,
        has_pending_paths=has_pending,
        bs=bs,
    )
    if finalize:
        return {"finalize": True, "finalize_reason": reason, "phase": "finalize"}
    return {"phase": "reflect"}


__all__ = ["run_evaluate"]
