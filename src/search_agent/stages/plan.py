"""plan: JobProfile+TalentProfile -> list[SearchPath].

Parsing is tolerant: each LLM-proposed path is validated end-to-end inside a
try/except, so one malformed path never aborts the stage. If the LLM produces
zero valid paths, a default path is built from understand's hard_constraints so
execute always has something to run (mirrors the harness's single-path fallback).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from search_agent.core.education_constraints import preserve_required_education_constraints
from search_agent.core.experience import query_family
from search_agent.core.signature import intent_signature
from search_agent.llm.prompts import (
    build_plan_payload,
    plan_prompt,
)
from search_agent.models.artifacts import SearchPath
from search_agent.models.ir import (
    CandidateSearchIR,
    WhereNode,
)
from search_agent.stages.base import Deps

logger = logging.getLogger(__name__)


def _dedup_disabled(deps: Deps) -> bool:
    """True only for the ``no_signature_dedup`` ablation row (never in serving)."""
    return bool(deps.ablation and deps.ablation.no_signature_dedup)


def _default_path(state: dict[str, Any], tried: set[str]) -> SearchPath | None:
    """Build a single fallback path from understand's hard_constraints."""
    job = state.get("job_profile")
    talent = state.get("talent_profile")
    if not job or not job.hard_constraints:
        return None
    leaves = [WhereNode(type="leaf", condition=c) for c in job.hard_constraints]
    where = leaves[0] if len(leaves) == 1 else WhereNode(type="and", children=leaves)
    semantic = list(getattr(talent, "soft_signals", []) or [])
    intent = CandidateSearchIR(where=where, semantic=semantic)
    sig = intent_signature(intent)
    if sig in tried:
        return None
    return SearchPath(
        id="p1",
        rationale="default path (plan produced no valid paths)",
        intent=intent,
        origin="default",
        signature=sig,
        status="pending",
    )


def run_plan(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    system, schema = plan_prompt()
    tried = set(state.get("tried_signatures", set()))
    priors = deps.experience.hints(query_family(state["job_profile"])) if deps.experience else []
    user = json.dumps(
        build_plan_payload(
            state["job_profile"],
            state["talent_profile"],
            sorted(tried),
            deps.budget_state.budget.max_paths_per_iteration,
            experience=priors,
        ),
        ensure_ascii=False,
    )
    data = deps.llm.complete_json(system=system, user=user, schema_name="agent_plan", schema=schema)
    deps.budget_state.record_llm()

    cap = deps.budget_state.budget.max_paths_per_iteration
    new_paths: list[SearchPath] = []
    new_sigs: set[str] = set()
    base = len(tried)
    for raw in data.get("paths", []) or []:
        try:
            intent_raw = raw["intent"]
            if isinstance(intent_raw, dict):
                for _s in intent_raw.get("semantic", []) or []:
                    if isinstance(_s, dict) and "text" not in _s and "query" in _s:
                        _s["text"] = _s.pop("query")
            intent = CandidateSearchIR.model_validate(intent_raw)
            job = state.get("job_profile")
            intent = preserve_required_education_constraints(
                intent,
                job.hard_constraints if job else [],
            )
            sig = intent_signature(intent)
        except Exception:  # noqa: BLE001 - skip path with invalid intent
            logger.debug("plan: skip path with invalid intent: %s", raw)
            continue
        if not _dedup_disabled(deps) and (sig in tried or sig in new_sigs):
            continue
        try:
            new_paths.append(
                SearchPath(
                    id=f"p{base + len(new_paths) + 1}",
                    rationale=str(raw.get("rationale", "")),
                    intent=intent,
                    source_refs=list(raw.get("source_refs", []) or []),
                    expected_yield=raw.get("expected_yield", "mid"),
                    signature=sig,
                    status="pending",
                )
            )
            new_sigs.add(sig)
        except Exception:  # noqa: BLE001 - skip path failing SearchPath build
            logger.debug("plan: skip path failing SearchPath build: %s", raw)
            continue
        if len(new_paths) >= cap:
            break

    if not new_paths:
        dp = _default_path(state, tried)
        if dp is not None:
            new_paths.append(dp)
            new_sigs.add(dp.signature)
            logger.info("plan: 0 valid LLM paths, using default path from hard_constraints")

    return {
        "paths": list(state.get("paths", [])) + new_paths,
        "tried_signatures": tried | new_sigs,
        "phase": "execute" if new_paths else "finalize",
    }


__all__ = ["run_plan"]
