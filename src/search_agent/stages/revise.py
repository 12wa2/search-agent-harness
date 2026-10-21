"""revise: apply path_actions, iteration++, route back to execute.

Pure stage: applies drop/loosen/tighten/add_path actions. For loosen/tighten/
add_path the action must carry a valid ``intent``; the old path (if any) is
dropped and a new pending path is added (signature-checked against
tried_signatures). No executable action -> finalize (no improvement space).
"""

from __future__ import annotations

from typing import Any

from search_agent.core.education_constraints import preserve_required_education_constraints
from search_agent.core.signature import intent_signature
from search_agent.models.artifacts import (
    PathAction,
    SearchPath,
)
from search_agent.models.ir import SemanticConditionIR
from search_agent.stages.base import Deps


def _apply_action(
    action: PathAction,
    paths: list[SearchPath],
    tried: set[str],
    new_sigs: set[str],
    new_paths: list[SearchPath],
    *,
    dedup: bool = True,
) -> None:
    kind = action.kind
    if kind == "drop":
        for p in paths:
            if p.id == action.target_path_id:
                p.status = "dropped"
        return
    if kind == "clarify":
        return  # handled by caller via need_clarify
    if kind in ("loosen", "tighten") and action.target_path_id:
        for p in paths:
            if p.id == action.target_path_id:
                p.status = "dropped"
        # fall through to add new path if intent present
    if action.intent is None:
        return
    try:
        sig = intent_signature(action.intent)
    except Exception:  # noqa: BLE001 - skip malformed intent
        return
    if dedup and (sig in tried or sig in new_sigs):
        return
    new_sigs.add(sig)
    base = len(tried) + len(new_paths)
    new_paths.append(
        SearchPath(
            id=f"p{base + 1}",
            rationale=action.rationale or f"revised ({kind})",
            intent=action.intent,
            expected_yield="mid",
            origin=kind,
            signature=sig,
            status="pending",
        )
    )


def run_revise(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    reflections = state.get("reflections", [])
    if not reflections:
        return {"phase": "finalize", "finalize_reason": "reflection_empty"}
    reflection = reflections[-1]
    actions = reflection.path_actions

    paths = [p.model_copy() for p in state.get("paths", [])]
    tried = set(state.get("tried_signatures", set()))
    new_sigs: set[str] = set()
    new_paths: list[SearchPath] = []

    executable = False
    need_clarify = False
    for action in actions:
        job = state.get("job_profile")
        if action.intent is not None and job is not None:
            action = action.model_copy(
                update={
                    "intent": preserve_required_education_constraints(
                        action.intent,
                        job.hard_constraints,
                    )
                }
            )
        if action.kind == "clarify":
            need_clarify = True
            executable = True
            continue
        if action.kind in ("drop", "loosen", "tighten", "add_path"):
            _apply_action(
                action,
                paths,
                tried,
                new_sigs,
                new_paths,
                dedup=not (deps.ablation and deps.ablation.no_signature_dedup),
            )
            executable = True

    # merge new source hints + inferred soft signals into talent profile
    talent = state.get("talent_profile")
    profile_converged = False
    if talent is not None:
        merged_sources = list(talent.source_hypotheses)
        if reflection.new_source_hints:
            existing_ids = {s.id for s in merged_sources}
            existing_values = {(s.kind, s.value) for s in merged_sources}
            for src in reflection.new_source_hints:
                if src.id not in existing_ids and (src.kind, src.value) not in existing_values:
                    merged_sources.append(src)
                    existing_ids.add(src.id)
                    existing_values.add((src.kind, src.value))
        merged_soft = list(talent.soft_signals)
        existing_texts = {s.text for s in merged_soft}
        if reflection.inferred_soft_signals:
            for sig in reflection.inferred_soft_signals:
                if sig.text not in existing_texts:
                    merged_soft.append(sig)
                    existing_texts.add(sig.text)
        if reflection.common_traits:
            for trait in reflection.common_traits:
                if trait and trait not in existing_texts:
                    merged_soft.append(
                        SemanticConditionIR(
                            text=trait,
                            fields=["work_vector", "project_vector"],
                            weight=1.5,
                        )
                    )
                    existing_texts.add(trait)
        profile_changed = len(merged_sources) != len(talent.source_hypotheses) or len(
            merged_soft
        ) != len(talent.soft_signals)
        if profile_changed:
            talent = talent.model_copy(
                update={
                    "source_hypotheses": merged_sources,
                    "soft_signals": merged_soft,
                }
            )
        profile_converged = not profile_changed

    iteration = state.get("iteration", 0) + 1
    deps.budget_state.iteration = iteration

    all_paths = paths + new_paths
    updates: dict[str, Any] = {
        "paths": all_paths,
        "tried_signatures": tried | new_sigs,
        "iteration": iteration,
        "talent_profile": talent,
        "profile_converged": profile_converged,
    }
    if need_clarify:
        updates.update(
            {
                "need_clarify": True,
                "phase": "finalize",
                "finalize_reason": "clarification_requested",
            }
        )
    elif not executable:
        updates.update({"phase": "finalize", "finalize_reason": "no_path_actions"})
    elif not any(p.status == "pending" for p in all_paths):
        updates.update({"phase": "finalize", "finalize_reason": "paths_exhausted"})
    else:
        updates["phase"] = "execute"
    return updates


__all__ = ["run_revise"]
