"""Difficulty-based routing + adaptive budget.

The understand stage scores ``subjective_difficulty``; the harness routes on it:
  high -> single_pass: skip plan/reflect/revise, execute one path only
         (objectively hard roles burn budget without better recall)
  low  -> reduced:     fewer iterations / paths (easy queries converge fast)
  mid  -> standard:    default budget
"""

from __future__ import annotations

from typing import Literal

from search_agent.models.artifacts import Budget

DifficultyRoute = Literal["single_pass", "reduced", "standard"]


def difficulty_route(difficulty: str) -> DifficultyRoute:
    """Map subjective_difficulty to a routing decision."""
    if difficulty == "high":
        return "single_pass"
    if difficulty == "low":
        return "reduced"
    return "standard"


def adaptive_budget(base: Budget, route: DifficultyRoute) -> Budget:
    """Shrink budget for easy/hard queries; leave standard unchanged."""
    if route == "single_pass":
        return base.model_copy(update={"max_iterations": 0, "max_paths_per_iteration": 1})
    if route == "reduced":
        return base.model_copy(update={"max_iterations": 1, "max_paths_per_iteration": 2})
    return base


__all__ = ["DifficultyRoute", "adaptive_budget", "difficulty_route"]
