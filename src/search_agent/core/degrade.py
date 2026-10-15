"""Degradation decisions.

Maps a stage failure to (action, degraded_reason). The harness applies the
action and never raises; a degradedReason is surfaced in agentTrace + response.
"""

from __future__ import annotations

from typing import Literal

DegradedReason = Literal[
    "understand_failed",
    "plan_failed_single_path",
    "all_paths_failed",
    "judge_failed",
    "global_rerank_failed",
    "budget_exceeded",
]

# action: what the harness does next given a failure at ``stage``
DegradedAction = Literal[
    "fallback_baseline",  # abandon agent, return the baseline single-pass result
    "single_path",  # run the one path we already have, skip planning
    "skip_judge",  # proceed with empty matched_abilities, rely on tool facts
    "skip_global_rerank",  # finalize with per-path max-score order
    "finalize_now",  # stop loop, output what we have
]

_MATRIX: dict[tuple[str, str], tuple[DegradedAction, DegradedReason]] = {
    ("understand", "llm_failed"): ("fallback_baseline", "understand_failed"),
    ("understand", "clarify"): ("finalize_now", "understand_failed"),
    ("plan", "llm_failed"): ("single_path", "plan_failed_single_path"),
    ("execute", "all_paths_failed"): ("fallback_baseline", "all_paths_failed"),
    ("judge", "llm_failed"): ("skip_judge", "judge_failed"),
    ("rerank_pool", "llm_failed"): ("skip_global_rerank", "global_rerank_failed"),
    ("any", "budget_exceeded"): ("finalize_now", "budget_exceeded"),
}


def degrade(stage: str, failure: str) -> tuple[DegradedAction, DegradedReason]:
    """Look up the degradation action for a (stage, failure) pair."""
    return _MATRIX.get((stage, failure), ("finalize_now", "understand_failed"))


__all__ = ["DegradedAction", "DegradedReason", "degrade"]
