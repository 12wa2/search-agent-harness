"""execute: call SearchTool per pending path (parallel) -> PathResult."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

from search_agent.contract.tool import SearchToolRequest
from search_agent.models.artifacts import PathResult, SearchPath
from search_agent.models.ir import SemanticConditionIR
from search_agent.stages.base import Deps

# Cap how many talent-profile soft signals are merged into each path intent, to
# avoid over-broadening vector recall. Top-N by weight only.
_MAX_PROFILE_SOFT_SIGNALS = 3
_MAX_PROFILE_SOURCES = 2


def run_execute(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    job = state["job_profile"]
    seen = set(state.get("seen_doc_ids", set()))
    paths = list(state.get("paths", []))
    pending = [p for p in paths if p.status == "pending"]
    talent = state.get("talent_profile")
    _all_soft = list(getattr(talent, "soft_signals", []) or []) if talent else []
    profile_soft = sorted(_all_soft, key=lambda s: s.weight, reverse=True)[
        :_MAX_PROFILE_SOFT_SIGNALS
    ]

    # Build semantic signals from source_hypotheses so the enriched profile's
    # dimensional insights (company/industry/product) actually drive search.
    _sources = list(getattr(talent, "source_hypotheses", []) or []) if talent else []
    source_semantics: list[SemanticConditionIR] = []
    for src in _sources[:_MAX_PROFILE_SOURCES]:
        fields = ["project_vector"] if src.kind in ("product", "project") else ["work_vector"]
        source_semantics.append(SemanticConditionIR(text=src.value, fields=fields, weight=0.8))

    def call(path: SearchPath):
        intent = path.intent
        _existing = {s.text for s in intent.semantic}
        _extra: list[SemanticConditionIR] = []
        for s in profile_soft + source_semantics:
            if s.text not in _existing:
                _extra.append(s)
                _existing.add(s.text)
        if _extra:
            intent = intent.model_copy(update={"semantic": list(intent.semantic) + _extra})
        req = SearchToolRequest(
            intent=intent,
            query_text=job.raw_query,
            company_id=state["company_id"],
            hr_id=state.get("hr_id"),
            permission=state.get("permission"),
            top_k=state["top_k"],
            recall_pool_size=state.get("recall_pool_size"),
            routing=state["routing"],
            exclude_doc_ids=list(seen),
            details=state.get("details", False),
            skip_rerank=True,
        )
        try:
            return path.id, deps.tool.search_with_intent(req), None
        except Exception as exc:  # noqa: BLE001 - per-path degrade
            return path.id, None, repr(exc)

    workers = max(1, min(4, len(pending)))
    raw_results = (
        list(ThreadPoolExecutor(max_workers=workers).map(call, pending)) if pending else []
    )

    updated = {p.id: p.model_copy() for p in paths}
    new_results: list[PathResult] = list(state.get("path_results", []))
    for pid, res, err in raw_results:
        if res is None:
            updated[pid].status = "exhausted"
            continue
        updated[pid].status = "done"
        for tc in res.items:
            seen.add(tc.doc_id)
        new_results.append(
            PathResult(path_id=pid, tool_result=res, quality=res.quality, quality_reason=err or "")
        )

    return {
        "paths": list(updated.values()),
        "path_results": new_results,
        "seen_doc_ids": seen,
        "phase": "judge",
    }


__all__ = ["run_execute"]
