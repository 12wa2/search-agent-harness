"""PathResult merge + path quality.

Merge is the authoritative dedup: same candidate from multiple paths (or rounds)
is folded by doc_id — score=max, matched_abilities=union, missing minus matched.
"""

from __future__ import annotations

from typing import Iterable

from search_agent.models.artifacts import (
    CandidateJudgment,
    Quality,
)


def merge_judgment(existing: CandidateJudgment, new: CandidateJudgment) -> CandidateJudgment:
    """Fold ``new`` into ``existing`` for the same doc_id."""
    score = max(existing.score, new.score)
    matched = list(dict.fromkeys(existing.matched_abilities + new.matched_abilities))
    missing = [m for m in (existing.missing_abilities + new.missing_abilities) if m not in matched]
    missing = list(dict.fromkeys(missing))
    sources = list(dict.fromkeys(existing.source_path_ids + new.source_path_ids))
    summary = new.summary if new.score >= existing.score else existing.summary
    hard = (
        existing.hard_matches
        if len(existing.hard_matches) >= len(new.hard_matches)
        else new.hard_matches
    )
    return CandidateJudgment(
        doc_id=existing.doc_id,
        score=score,
        matched_abilities=matched,
        missing_abilities=missing,
        hard_matches=hard,
        summary=summary,
        source_path_ids=sources,
    )


def merge_into(
    accumulated: dict[str, CandidateJudgment],
    judgments: Iterable[CandidateJudgment],
) -> dict[str, CandidateJudgment]:
    """In-place fold of judgments into the accumulated pool; returns ``accumulated``."""
    for j in judgments:
        if j.doc_id in accumulated:
            accumulated[j.doc_id] = merge_judgment(accumulated[j.doc_id], j)
        else:
            accumulated[j.doc_id] = j
    return accumulated


def path_quality(recalled: int, qualified: int, rich_min: int) -> Quality:
    """Quality bucket for a single path."""
    if recalled <= 0:
        return "empty"
    if qualified < rich_min:
        return "thin"
    return "rich"


def rich_min_for(target_qualified: int, max_paths_per_iteration: int) -> int:
    return max(3, -(-target_qualified // max_paths_per_iteration))  # ceil


__all__ = ["merge_into", "merge_judgment", "path_quality", "rich_min_for"]
