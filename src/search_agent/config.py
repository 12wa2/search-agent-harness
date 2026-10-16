"""Environment-driven configuration (no host settings module required).

All knobs are optional ``SEARCH_AGENT_*`` environment variables with
reconstruction-friendly defaults.
"""

from __future__ import annotations

import os

from search_agent.models.artifacts import Budget


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


__all__ = ["budget_from_env", "recall_pool_size_from_env"]
