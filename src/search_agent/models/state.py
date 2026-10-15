"""SearchState for the agent loop: TypedDict + reducers.

Carries request context (query/company/...) + reasoning artifacts + control
signals. Reducers:
- seen_doc_ids / tried_signatures: set-union
- accumulated: per-doc_id smart merge (max score, union matched abilities)
- paths / path_results / reflections: list append
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from search_agent.contract.tool import ToolCandidate
from search_agent.models.artifacts import (
    Budget,
    CandidateJudgment,
    JobProfile,
    PathResult,
    Reflection,
    SearchPath,
    TalentProfile,
)


def _set_union(a: set[str] | None, b: set[str] | None) -> set[str]:
    return (a or set()) | (b or set())


def _list_concat(a: list | None, b: list | None) -> list:
    return (a or []) + (b or [])


def _accumulated_reducer(
    existing: dict[str, CandidateJudgment] | None,
    incoming: dict[str, CandidateJudgment] | None,
) -> dict[str, CandidateJudgment]:
    from search_agent.core.merge import merge_judgment

    out: dict[str, CandidateJudgment] = dict(existing or {})
    for doc_id, j in (incoming or {}).items():
        if doc_id in out:
            out[doc_id] = merge_judgment(out[doc_id], j)
        else:
            out[doc_id] = j
    return out


class SearchState(TypedDict, total=False):
    # --- request context (set once at start) ---
    query: str
    company_id: int
    hr_id: int | None
    permission: dict[str, Any] | None
    routing: int
    top_k: int
    recall_pool_size: int | None
    details: bool
    # --- reasoning artifacts ---
    iteration: int
    phase: str
    job_profile: JobProfile
    talent_profile: TalentProfile
    budget: Budget
    paths: Annotated[list[SearchPath], _list_concat]
    path_results: Annotated[list[PathResult], _list_concat]
    accumulated: Annotated[dict[str, CandidateJudgment], _accumulated_reducer]
    seen_doc_ids: Annotated[set[str], _set_union]
    tried_signatures: Annotated[set[str], _set_union]
    reflections: Annotated[list[Reflection], _list_concat]
    # --- budget / control signals ---
    llm_calls: int
    new_qualified_last_round: int
    need_clarify: bool
    finalize: bool
    finalize_reason: str
    degraded_reason: str
    # --- final output ---
    final_items: list[ToolCandidate]
    final_total: int
    conversation_history: list[dict]


__all__ = ["SearchState"]
