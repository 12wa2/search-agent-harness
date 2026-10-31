"""Per-round strategy effects for the adaptive search loop.

The loop already records cumulative state, which makes it hard to answer the
question HR actually cares about after a reflection: *what did changing the
strategy buy us, and what did it cost?* This module derives immutable
per-round snapshots from the existing state. It never changes search,
judgment, ranking, or the hard-condition policy.

All document counts are deduplicated by ``doc_id``. A round's ``new*`` values
are the difference between that round and the previous completed round; the
cumulative totals are kept alongside them so the frontend does not have to
reconstruct the arithmetic from SSE event order.
"""

from __future__ import annotations

from typing import Any, Iterable


def _doc_id(item: Any) -> str | None:
    value = getattr(item, "doc_id", None) or getattr(item, "docId", None)
    text = str(value).strip() if value is not None else ""
    return text or None


def _result_doc_ids(results: Iterable[Any]) -> set[str]:
    ids: set[str] = set()
    for result in results:
        tool_result = getattr(result, "tool_result", None)
        for item in getattr(tool_result, "items", None) or []:
            doc_id = _doc_id(item)
            if doc_id:
                ids.add(doc_id)
    return ids


def _result_path_ids(results: Iterable[Any]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for result in results:
        path_id = str(getattr(result, "path_id", "") or "").strip()
        if path_id and path_id not in seen:
            seen.add(path_id)
            ids.append(path_id)
    return ids


def _must_names(job_profile: Any) -> set[str]:
    return {
        str(getattr(ability, "name", "") or "").strip()
        for ability in (getattr(job_profile, "must_abilities", None) or [])
        if str(getattr(ability, "name", "") or "").strip()
    }


def _is_must_qualified(judgment: Any, must_names: set[str]) -> bool:
    if not must_names:
        return True
    matched = {str(name) for name in (getattr(judgment, "matched_abilities", None) or [])}
    return must_names.issubset(matched)


def _count_must_qualified(
    doc_ids: Iterable[str],
    accumulated: dict[str, Any],
    must_names: set[str],
) -> int:
    return sum(
        1
        for doc_id in doc_ids
        if doc_id in accumulated and _is_must_qualified(accumulated[doc_id], must_names)
    )


def _talent_counts(talent: Any) -> tuple[int, int]:
    if talent is None:
        return 0, 0
    return (
        len(getattr(talent, "soft_signals", None) or []),
        len(getattr(talent, "source_hypotheses", None) or []),
    )


def _budget_values(
    budget_state: Any,
    *,
    llm_calls: int | None,
    elapsed_ms: int | None,
) -> tuple[int, int]:
    current_calls = (
        int(llm_calls) if llm_calls is not None else int(getattr(budget_state, "llm_calls", 0) or 0)
    )
    if elapsed_ms is not None:
        current_elapsed = int(elapsed_ms)
    else:
        elapsed_sec = getattr(budget_state, "elapsed_sec", None)
        current_elapsed = int(elapsed_sec() * 1000) if callable(elapsed_sec) else 0
    return max(0, current_calls), max(0, current_elapsed)


def _previous_round(state: dict[str, Any]) -> dict[str, Any] | None:
    summaries = state.get("round_summaries") or []
    return dict(summaries[-1]) if summaries else None


def _latest_reflection(state: dict[str, Any]) -> Any:
    reflections = state.get("reflections") or []
    return reflections[-1] if reflections else None


def _strategy_actions(reflection: Any) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for action in getattr(reflection, "path_actions", None) or []:
        kind = str(getattr(action, "kind", "") or "").strip()
        if not kind:
            continue
        actions.append(
            {
                "kind": kind,
                "rationale": str(getattr(action, "rationale", "") or "").strip(),
                "targetPathId": getattr(action, "target_path_id", None),
            }
        )
    return actions


def _next_phase(state: dict[str, Any]) -> str:
    phase = str(state.get("phase") or "")
    return "finalize" if phase in {"done", "finalize"} else phase


def _stop_reason(state: dict[str, Any]) -> str | None:
    if _next_phase(state) != "finalize":
        return None
    return (
        state.get("finalize_reason")
        or ("cancelled" if state.get("cancelled") else None)
        or state.get("degraded_reason")
    )


def record_round_summary(
    state: dict[str, Any],
    budget_state: Any,
    *,
    llm_calls: int | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any]:
    """Append one immutable round snapshot to ``state`` and return it.

    Called immediately after an ``evaluate`` stage completes. ``path_results``
    is cumulative, so ``round_path_cursor`` marks the first result belonging to
    the new round. Candidate duplicates within a round are counted once.
    """
    path_results = list(state.get("path_results") or [])
    cursor = int(state.get("round_path_cursor", 0) or 0)
    if cursor < 0 or cursor > len(path_results):
        cursor = 0

    prior_results = path_results[:cursor]
    round_results = path_results[cursor:]
    prior_docs = _result_doc_ids(prior_results)
    round_docs = _result_doc_ids(round_results)
    round_path_ids = _result_path_ids(round_results)

    accumulated = dict(state.get("accumulated") or {})
    prior_judged_ids = {
        str(doc_id) for doc_id in (state.get("round_judged_doc_ids") or set()) if doc_id
    }
    current_judged_ids = {str(doc_id) for doc_id in accumulated if doc_id}
    new_judged_ids = current_judged_ids - prior_judged_ids

    previous = _previous_round(state)
    previous_path_ids = {
        str(path_id) for path_id in ((previous or {}).get("pathIds") or []) if path_id
    }
    previous_soft_signals = int((previous or {}).get("softSignalTotal", 0) or 0)
    previous_source_hints = int((previous or {}).get("sourceHintTotal", 0) or 0)
    previous_llm_calls = int((previous or {}).get("llmCallsTotal", 0) or 0)
    previous_elapsed = int((previous or {}).get("elapsedMsTotal", 0) or 0)
    has_previous = previous is not None
    current_llm_calls, current_elapsed = _budget_values(
        budget_state, llm_calls=llm_calls, elapsed_ms=elapsed_ms
    )

    must_names = _must_names(state.get("job_profile"))
    talent = state.get("talent_profile")
    soft_signal_total, source_hint_total = _talent_counts(talent)
    reflection = _latest_reflection(state)
    gaps = [
        str(gap).strip() for gap in (getattr(reflection, "gaps", None) or []) if str(gap).strip()
    ]

    new_path_ids = set(round_path_ids) - previous_path_ids
    summary: dict[str, Any] = {
        "round": len(state.get("round_summaries") or []) + 1,
        "kind": "path",
        "pathCount": len(round_path_ids),
        "pathIds": round_path_ids,
        "returned": sum(
            int(getattr(getattr(result, "tool_result", None), "total", 0) or 0)
            for result in round_results
        ),
        "uniqueReturned": len(round_docs),
        "newUnique": len(round_docs - prior_docs),
        "newJudged": len(new_judged_ids),
        "judgedTotal": len(current_judged_ids),
        "newMustQualified": _count_must_qualified(new_judged_ids, accumulated, must_names),
        "mustQualifiedTotal": _count_must_qualified(current_judged_ids, accumulated, must_names),
        "gaps": gaps,
        "actions": _strategy_actions(reflection),
        "newSoftSignals": (
            max(0, soft_signal_total - previous_soft_signals) if has_previous else 0
        ),
        "newSourceHints": (
            max(0, source_hint_total - previous_source_hints) if has_previous else 0
        ),
        "softSignalTotal": soft_signal_total,
        "sourceHintTotal": source_hint_total,
        "newPathCount": len(new_path_ids),
        "llmCallsDelta": max(0, current_llm_calls - previous_llm_calls),
        "elapsedMsDelta": max(0, current_elapsed - previous_elapsed),
        "llmCallsTotal": current_llm_calls,
        "elapsedMsTotal": current_elapsed,
        "next": _next_phase(state),
        "stopReason": _stop_reason(state),
    }

    summaries = list(state.get("round_summaries") or [])
    summaries.append(summary)
    state["round_summaries"] = summaries
    state["round_path_cursor"] = len(path_results)
    state["round_judged_doc_ids"] = current_judged_ids
    return summary


def refresh_round_outcome(state: dict[str, Any]) -> None:
    """Make the last round's outcome reflect a later revise/finalize decision."""
    summaries = list(state.get("round_summaries") or [])
    if not summaries:
        return
    last = dict(summaries[-1])
    last["next"] = _next_phase(state)
    last["stopReason"] = _stop_reason(state)
    summaries[-1] = last
    state["round_summaries"] = summaries


def build_final_search_summary(
    state: dict[str, Any],
    budget_state: Any,
    *,
    llm_calls: int | None = None,
    elapsed_ms: int | None = None,
) -> dict[str, Any] | None:
    """Describe the optional converged final search as a separate strategy step."""
    final_search_total = int(state.get("final_search_total") or 0)
    final_search_new = int(state.get("final_search_new") or 0)
    final_judged = int(state.get("final_judged") or 0)
    final_semantic = state.get("final_search_semantic") or []
    if not (final_search_total or final_search_new or final_judged or final_semantic):
        return None

    accumulated = dict(state.get("accumulated") or {})
    prior_judged_ids = {
        str(doc_id) for doc_id in (state.get("round_judged_doc_ids") or set()) if doc_id
    }
    current_judged_ids = {str(doc_id) for doc_id in accumulated if doc_id}
    new_judged_ids = current_judged_ids - prior_judged_ids
    must_names = _must_names(state.get("job_profile"))
    previous = _previous_round(state)
    if state.get("finalize_start_llm_calls") is not None:
        previous_llm_calls = int(state["finalize_start_llm_calls"])
    else:
        previous_llm_calls = int((previous or {}).get("llmCallsTotal", 0) or 0)
    if state.get("finalize_start_elapsed_ms") is not None:
        previous_elapsed = int(state["finalize_start_elapsed_ms"])
    else:
        previous_elapsed = int((previous or {}).get("elapsedMsTotal", 0) or 0)
    current_llm_calls, current_elapsed = _budget_values(
        budget_state, llm_calls=llm_calls, elapsed_ms=elapsed_ms
    )

    soft_signal_total, source_hint_total = _talent_counts(state.get("talent_profile"))
    summary: dict[str, Any] = {
        "kind": "final",
        "label": "最终收敛检索",
        "pathCount": 1,
        "returned": final_search_total,
        "newUnique": final_search_new,
        "newJudged": len(new_judged_ids),
        "judgedTotal": len(current_judged_ids),
        "newMustQualified": _count_must_qualified(new_judged_ids, accumulated, must_names),
        "mustQualifiedTotal": _count_must_qualified(current_judged_ids, accumulated, must_names),
        "softSignalTotal": soft_signal_total,
        "sourceHintTotal": source_hint_total,
        "llmCallsDelta": max(0, current_llm_calls - previous_llm_calls),
        "elapsedMsDelta": max(0, current_elapsed - previous_elapsed),
        "llmCallsTotal": current_llm_calls,
        "elapsedMsTotal": current_elapsed,
        "next": "finalize",
        "stopReason": _stop_reason(state),
    }
    state["final_search_summary"] = summary
    return summary


def finalize_round_display(
    state: dict[str, Any],
    budget_state: Any,
    *,
    llm_calls: int | None = None,
    elapsed_ms: int | None = None,
) -> None:
    """Refresh the last round and attach the final-search summary in one call."""
    refresh_round_outcome(state)
    build_final_search_summary(
        state,
        budget_state,
        llm_calls=llm_calls,
        elapsed_ms=elapsed_ms,
    )


__all__ = [
    "build_final_search_summary",
    "finalize_round_display",
    "record_round_summary",
    "refresh_round_outcome",
]
