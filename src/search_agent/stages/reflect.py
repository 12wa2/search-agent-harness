"""reflect: analyze PathResults + top judgments -> Reflection.

Parsing is tolerant: each new_source_hint and path_action is validated inside a
try/except, so a malformed item never aborts the stage (mirrors understand/plan/
judge). Path actions carrying an intent are signature-checked in revise.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from search_agent.llm.prompts import (
    build_reflect_payload,
    reflect_prompt,
)
from search_agent.models.artifacts import (
    CandidateSource,
    PathAction,
    Reflection,
)
from search_agent.models.ir import SemanticConditionIR
from search_agent.stages.base import Deps

logger = logging.getLogger(__name__)

_TOP_JUDGMENTS = 10


def _coerce_sources(raw: Any) -> list[CandidateSource]:
    out: list[CandidateSource] = []
    for s in raw or []:
        if isinstance(s, dict):
            try:
                out.append(CandidateSource.model_validate(s))
            except Exception:  # noqa: BLE001 - skip malformed hint
                logger.debug("reflect: skip invalid source hint: %s", s)
    return out


def _coerce_soft_signals(raw: Any) -> list[SemanticConditionIR]:
    out: list[SemanticConditionIR] = []
    for s in raw or []:
        if isinstance(s, dict):
            try:
                out.append(SemanticConditionIR.model_validate(s))
            except Exception:  # noqa: BLE001 - skip malformed signal
                logger.debug("reflect: skip invalid inferred_soft_signal: %s", s)
    return out


def _normalize_action(a: dict[str, Any]) -> dict[str, Any]:
    """Map common LLM aliases to the IR contract before validation."""
    intent = a.get("intent")
    if isinstance(intent, dict):
        sem = intent.get("semantic")
        if isinstance(sem, list):
            for s in sem:
                if isinstance(s, dict) and "text" not in s and "query" in s:
                    s["text"] = s.pop("query")
    return a


def _coerce_actions(raw: Any) -> list[PathAction]:
    out: list[PathAction] = []
    for a in raw or []:
        if not isinstance(a, dict):
            continue
        try:
            out.append(PathAction.model_validate(_normalize_action(a)))
        except Exception:  # noqa: BLE001 - skip malformed action
            logger.debug("reflect: skip invalid path_action: %s", a)
    return out


def run_reflect(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    system, schema = reflect_prompt()
    accumulated = state.get("accumulated", {})
    top = sorted(accumulated.values(), key=lambda j: j.score, reverse=True)[:_TOP_JUDGMENTS]
    path_results = state.get("path_results", [])
    prior_reflections = state.get("reflections", [])
    prior_gaps = prior_reflections[-1].gaps if prior_reflections else []
    talent_profile = state.get("talent_profile")

    # Build doc_id -> CandidateItem lookup so reflect can see candidate backgrounds
    candidate_lookup: dict[str, Any] = {}
    for pr in path_results:
        for item in getattr(pr.tool_result, "items", []) or []:
            did = getattr(item, "doc_id", None) or getattr(item, "docId", None)
            if did:
                candidate_lookup[did] = item

    user = json.dumps(
        build_reflect_payload(
            state.get("iteration", 0),
            datetime.now().astimezone().isoformat(timespec="seconds"),
            path_results,
            top,
            sorted(state.get("tried_signatures", set())),
            prior_gaps,
            talent_profile,
            query=state.get("query", ""),
            paths=state.get("paths", []),
            candidate_lookup=candidate_lookup,
        ),
        ensure_ascii=False,
    )
    data = deps.llm.complete_json(
        system=system, user=user, schema_name="agent_reflect", schema=schema
    )
    deps.budget_state.record_llm()

    reflection = Reflection(
        common_traits=list(data.get("common_traits", []) or []),
        gaps=list(data.get("gaps", []) or []),
        inferred_soft_signals=_coerce_soft_signals(data.get("inferred_soft_signals")),
        new_source_hints=_coerce_sources(data.get("new_source_hints")),
        path_actions=_coerce_actions(data.get("path_actions")),
    )
    logger.info(
        "reflect: %d path_actions, %d source_hints",
        len(reflection.path_actions),
        len(reflection.new_source_hints),
    )
    return {"reflections": state.get("reflections", []) + [reflection], "phase": "revise"}


__all__ = ["run_reflect"]
