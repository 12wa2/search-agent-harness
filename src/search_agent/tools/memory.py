"""In-memory SearchTool: a deterministic, dependency-free reference backend.

The agent only ever talks to the ``SearchTool`` protocol. This module is a
complete, honest implementation of that contract over a small JSONL corpus:

* ``search_with_intent`` walks the ``WhereNode`` tree as structured filters,
  scores semantic conditions by token coverage against the candidate text,
  and returns explainable hard-match facts.
* ``rerank_pool`` re-scores a cross-path candidate pool against job-level
  abilities and hard constraints.

Swap it for Elasticsearch + vectors + an LLM re-ranker and the harness does
not change -- that boundary is the point of this repository.

Note: ``total`` is the number of matching documents in this corpus (the whole
corpus is "indexed"), mirroring how the real backend reports ES totals.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Sequence

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.models.api import CandidateItem
from search_agent.models.ir import StructuredConditionIR
from search_agent.tools.text import ability_hits as _ability_hits
from search_agent.tools.text import build_tool_candidate as _build_tool_candidate
from search_agent.tools.text import candidate_text as _candidate_text
from search_agent.tools.text import collect_leaves as _collect_leaves
from search_agent.tools.text import hard_facts as _hard_facts
from search_agent.tools.text import quality_label as _quality_label
from search_agent.tools.text import soft_score as _soft_score
from search_agent.tools.text import where_matches as _where_matches

__all__ = ["InMemorySearchTool", "load_candidates"]


# ── loading ────────────────────────────────────────────────────────────────


def load_candidates(
    source: str | Path | Sequence[dict[str, Any] | CandidateItem],
) -> list[CandidateItem]:
    """Load candidates from a JSONL path or an in-memory sequence."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        rows: list[Any] = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        rows = list(source)
    items: list[CandidateItem] = []
    for row in rows:
        items.append(row if isinstance(row, CandidateItem) else CandidateItem.model_validate(row))
    return items


# ── the tool ───────────────────────────────────────────────────────────────


class InMemorySearchTool:
    """Deterministic reference ``SearchTool`` over an in-memory corpus."""

    def __init__(
        self,
        candidates: str | Path | Sequence[dict[str, Any] | CandidateItem],
    ) -> None:
        self._items = load_candidates(candidates)
        self._by_id = {item.docId: item for item in self._items}

    @property
    def size(self) -> int:
        return len(self._items)

    # -- SearchTool protocol ------------------------------------------------

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        started = time.perf_counter()
        excluded = set(req.exclude_doc_ids or [])

        filtered: list[CandidateItem] = []
        for item in self._items:
            if item.docId in excluded:
                continue
            if _where_matches(req.intent.where, item):
                filtered.append(item)
        filtered_ms = int((time.perf_counter() - started) * 1000)

        score_started = time.perf_counter()
        leaves = _collect_leaves(req.intent.where)
        scored: list[tuple[float, list[dict[str, Any]], list[dict[str, Any]], CandidateItem]] = []
        for item in filtered:
            blob = _candidate_text(item)
            hard = _hard_facts(leaves, item)
            score, soft = _soft_score(req.intent.semantic, blob)
            scored.append((score, hard, soft, item))
        score_ms = int((time.perf_counter() - score_started) * 1000)

        scored.sort(key=lambda row: (-row[0], row[3].docId))
        total = len(scored)
        page_size = int(req.recall_pool_size or req.top_k)
        page = scored[: max(0, page_size)]

        items = [_build_tool_candidate(score, hard, soft, item) for score, hard, soft, item in page]
        quality = _quality_label(len(items), total, page_size)

        es_query: dict[str, Any] | None = None
        if req.details:
            es_query = {
                "backend": "in-memory",
                "filteredCount": total,
                "excludedCount": len(excluded),
                "semanticConditions": [s.text for s in req.intent.semantic or []],
            }

        return SearchToolResult(
            items=items,
            total=total,
            total_relation="eq",
            quality=quality,
            es_query=es_query,
            timings_ms={
                "filter": filtered_ms,
                "score": score_ms,
                "total": int((time.perf_counter() - started) * 1000),
            },
        )

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        started = time.perf_counter()
        abilities = [str(a) for a in (req.job_context.get("abilities") or [])]
        job_hard: list[StructuredConditionIR] = []
        for raw in req.job_context.get("hard_constraints") or []:
            try:
                job_hard.append(StructuredConditionIR.model_validate(raw))
            except Exception:  # noqa: BLE001 - ignore malformed job facts
                continue

        scored: list[tuple[float, list[dict[str, Any]], ToolCandidate]] = []
        for candidate in req.candidates:
            blob = _candidate_text(candidate.item)
            hits = _ability_hits(abilities, blob)
            hard = _hard_facts(job_hard, candidate.item)
            score = round(1.0 + 0.5 * hits + 0.2 * sum(1 for h in hard if h["matched"]), 4)
            scored.append((score, hard, candidate))

        scored.sort(key=lambda row: (-row[0], row[2].doc_id))
        top = scored[: max(0, int(req.top_k))]
        items = [
            _build_tool_candidate(score, hard, [], candidate.item) for score, hard, candidate in top
        ]
        return SearchToolResult(
            items=items,
            total=len(scored),
            total_relation="eq",
            quality="rich" if items else "empty",
            timings_ms={"total": int((time.perf_counter() - started) * 1000)},
        )
