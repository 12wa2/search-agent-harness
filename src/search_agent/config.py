"""Environment-driven configuration (no host settings module required).

All knobs are optional ``SEARCH_AGENT_*`` environment variables with
reconstruction-friendly defaults.
"""

from __future__ import annotations

import os

from search_agent.models.artifacts import Budget

_FINAL_SELECTION_POLICIES = frozenset({"current", "final_v2", "judge_gate", "judge_gate_v2"})


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def budget_from_env(top_k: int) -> Budget:
    """Budget for one run; ``target_qualified`` scales with ``top_k``."""
    return Budget(
        max_iterations=_env_int("SEARCH_AGENT_MAX_ITERATIONS", 3),
        max_paths_per_iteration=_env_int("SEARCH_AGENT_MAX_PATHS_PER_ITERATION", 4),
        target_qualified=_env_int("SEARCH_AGENT_TARGET_QUALIFIED", max(top_k * 2, 20)),
        time_budget_sec=_env_float("SEARCH_AGENT_TIME_BUDGET_SEC", 12.0),
        max_llm_calls=_env_int("SEARCH_AGENT_LLM_CALL_BUDGET", 40),
        max_tokens=_env_int("SEARCH_AGENT_TOKEN_BUDGET", 0),
    )


def recall_pool_size_from_env(top_k: int) -> int:
    """Per-path recall pool: recall wide for judging, then deliver ``top_k``.

    Mirrors the original funnel (``max(top_k, scan window)``): each path returns
    a pool of candidates, all of them get judged, and finalize only delivers
    ``top_k``. Set ``SEARCH_AGENT_RECALL_POOL_SIZE`` to override; ``0`` restores
    the narrow (pool == top_k) behaviour.
    """
    return _env_int("SEARCH_AGENT_RECALL_POOL_SIZE", max(top_k * 3, 30))


def final_selection_policy_from_env() -> str:
    """Return the final ordering policy for an enriched Agent run.

    ``current`` preserves the existing judge/evidence-first selection.
    ``final_v2`` skips the implicit rerank inside the converged search and
    applies one Hybrid V2 rerank over the complete merged pool in ``finalize``.
    ``judge_gate`` keeps the normal converged-search rerank and applies
    explicit must/evidence tiers. ``judge_gate_v2`` applies the same tiers,
    with one global Hybrid V2 rerank used only as a tie-breaker inside a tier.
    Unknown values fall back to ``current`` so a deployment typo cannot change
    existing behaviour.
    """
    value = os.environ.get("SEARCH_AGENT_FINAL_SELECTION_POLICY", "current").strip().lower()
    return value if value in _FINAL_SELECTION_POLICIES else "current"


def require_permission_from_env() -> bool:
    """Whether HTTP requests must carry a trusted permission context."""
    value = os.environ.get("SEARCH_AGENT_REQUIRE_PERMISSION", "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


__all__ = [
    "budget_from_env",
    "final_selection_policy_from_env",
    "recall_pool_size_from_env",
    "require_permission_from_env",
]
