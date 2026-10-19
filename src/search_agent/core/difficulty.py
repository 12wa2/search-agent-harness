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

_PREFERENCE_SIGNAL_RE = re.compile(r"最好|优先|加分")

#: Explicit, low-uncertainty search requests. These are deliberately narrow:
#: a search verb must be present, and the request must name a concrete role or
#: technology. This lets "帮我找会 Python 的" take the one-shot path without
#: demoting abstract searches such as "找可迁移的架构师".
_SEARCH_ACTION_RE = re.compile(
    r"找|寻找|搜|搜索|查|查询|招|招聘|推荐|筛选|匹配|看看|看一下|"
    r"find|search|look\s*for|hire|recruit",
    re.IGNORECASE,
)
_SIMPLE_ROLE_RE = re.compile(
    r"后端|前端|全栈|服务端|客户端|工程师|开发|研发|算法|数据|测试|运维|架构|"
    r"机器学习|深度学习|人工智能|产品|设计|分析|研究|科学家|经理|主管|专家|"
    r"销售|运营|财务|人事|顾问|制作人|剪辑|导演|设计师|"
    r"backend|frontend|full[- ]?stack|engineer|developer|architect|data|"
    r"machine learning|ml|devops|qa|analyst|scientist|manager|lead|recruiter|"
    r"designer|producer|editor",
    re.IGNORECASE,
)
_SIMPLE_SKILL_RE = re.compile(
    r"(?<![a-z0-9])(?:python|java|javascript|typescript|golang|go|c\+\+|c#|rust|"
    r"sql|react|vue|node(?:\.js)?|django|flask|fastapi|spring|kubernetes|docker|"
    r"spark|flink|pytorch|tensorflow|mysql|redis|kafka|hadoop|hive|clickhouse)"
    r"(?![a-z0-9])",
    re.IGNORECASE,
)


def is_explicit_simple_query(query: str) -> bool:
    """Return True for a clear request that should use one-shot retrieval.

    A simple request needs an explicit search action and a concrete role or
    technology. If it names both a role and a technology, concrete domain
    terms do not force reflection: the added detail actually makes the query
    more structured. Abstract capability searches and open-ended preferences
    still retain the reflective path.
    """
    text = str(query or "").strip()
    if not text or len(text) > 160:
        return False
    if _HIGH_SIGNAL_RE.search(text) or _PREFERENCE_SIGNAL_RE.search(text):
        return False
    if not _SEARCH_ACTION_RE.search(text):
        return False
    has_role = bool(_SIMPLE_ROLE_RE.search(text))
    has_skill = bool(_SIMPLE_SKILL_RE.search(text))
    if not has_role and not has_skill:
        return False
    if _MID_SIGNAL_RE.search(text) and not (has_role and has_skill):
        return False
    return True


def resolve_difficulty(query: str, difficulty: str) -> Difficulty:
    """Combine the model's difficulty with deterministic routing guards.

    Explicit one-shot requests are allowed to override an over-cautious model
    label. For everything else the existing conservative floor still applies:
    a model may raise difficulty, never lower a guarded floor.
    """
    current = difficulty if difficulty in _DIFFICULTY_ORDER else "mid"
    if is_explicit_simple_query(query):
        return "low"
    floor = difficulty_floor(query)
    return floor if _DIFFICULTY_ORDER[floor] > _DIFFICULTY_ORDER[current] else current


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


ROUTE_STAGES: dict[DifficultyRoute, tuple[str, ...]] = {
    "single_pass": ("understand", "execute", "judge", "evaluate", "finalize"),
    "reduced": (
        "understand",
        "plan",
        "execute",
        "judge",
        "evaluate",
        "reflect",
        "revise",
        "finalize",
    ),
    "standard": (
        "understand",
        "plan",
        "execute",
        "judge",
        "evaluate",
        "reflect",
        "revise",
        "finalize",
    ),
}


def route_stages(route: str) -> tuple[str, ...]:
    """Return the expected stage sequence for a routing decision."""
    return ROUTE_STAGES.get(route, ROUTE_STAGES["standard"])


__all__ = [
    "Difficulty",
    "DifficultyRoute",
    "ROUTE_STAGES",
    "adaptive_budget",
    "apply_difficulty_floor",
    "difficulty_floor",
    "difficulty_route",
    "is_explicit_simple_query",
    "resolve_difficulty",
    "route_stages",
]
