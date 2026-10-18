"""Difficulty-based routing + adaptive budget.

The understand stage scores ``subjective_difficulty``; the harness routes on it:
  low  -> single_pass: explicit, single-path queries use the traditional search
  mid  -> reduced:     one reflection round, two paths per round
  high -> standard:    full reflective loop for ambiguous / transferable search

Difficulty is about search-strategy uncertainty, not the number of extracted
fields. A query with several precise filters can still be easy; an abstract
ability search with few literal filters can be hard.
"""

from __future__ import annotations

import re
from typing import Literal

from search_agent.models.artifacts import Budget

DifficultyRoute = Literal["single_pass", "reduced", "standard"]
Difficulty = Literal["low", "mid", "high"]

_DIFFICULTY_ORDER: dict[str, int] = {"low": 0, "mid": 1, "high": 2}
_HIGH_SIGNAL_RE = re.compile(
    r"可迁移|跨行业|跨领域|行业不限|背景不限|技术骨干转|转岗|转型|抽象能力"
)
_MID_SIGNAL_RE = re.compile(
    r"高并发|分布式|微服务|架构|稳定性|容量|推荐|召回|排序|实时|机器学习|"
    r"数据平台|服务治理|最好|优先|加分|或者|或"
)


def difficulty_floor(query: str) -> Difficulty:
    """Conservative floor for queries whose search strategy is not trivial.

    The model may classify an abstract capability query as ``low`` because the
    years filter is explicit. That would bypass reflection entirely. This
    deterministic guard promotes such queries to at least ``mid``; transferable
    ability searches go to ``high``.
    """
    text = query.lower()
    if _HIGH_SIGNAL_RE.search(text):
        return "high"
    if _MID_SIGNAL_RE.search(text):
        return "mid"
    return "low"


def apply_difficulty_floor(query: str, difficulty: str) -> Difficulty:
    """Raise (never lower) a model-assigned difficulty to the guarded floor."""
    current = difficulty if difficulty in _DIFFICULTY_ORDER else "mid"
    floor = difficulty_floor(query)
    return floor if _DIFFICULTY_ORDER[floor] > _DIFFICULTY_ORDER[current] else current


def difficulty_route(difficulty: str) -> DifficultyRoute:
    """Map subjective_difficulty to a routing decision."""
    if difficulty == "low":
        return "single_pass"
    if difficulty == "mid":
        return "reduced"
    return "standard"


def adaptive_budget(base: Budget, route: DifficultyRoute) -> Budget:
    """Shrink budget for easy/hard queries; leave standard unchanged."""
    if route == "single_pass":
        return base.model_copy(update={"max_iterations": 0, "max_paths_per_iteration": 1})
    if route == "reduced":
        return base.model_copy(update={"max_iterations": 1, "max_paths_per_iteration": 2})
    return base


__all__ = [
    "Difficulty",
    "DifficultyRoute",
    "adaptive_budget",
    "apply_difficulty_floor",
    "difficulty_floor",
    "difficulty_route",
]
