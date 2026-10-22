"""understand: query -> JobProfile + TalentProfile.

Parsing is tolerant: the LLM gateway does not always enforce strict json_schema,
so we coerce common shapes (string abilities, alias keys) and skip individual
constraints that fail validation rather than aborting the whole stage. This
keeps understand from failing the agent loop on a single malformed field.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

from search_agent.core.clarification import (
    detect_skill_only_clarification,
    is_actionable_recruitment_query,
)
from search_agent.core.difficulty import is_explicit_simple_query, resolve_difficulty
from search_agent.llm.prompts import (
    build_understand_payload,
    understand_prompt,
)
from search_agent.models.artifacts import (
    CandidateSource,
    JobAbility,
    JobProfile,
    TalentProfile,
)
from search_agent.models.ir import (
    SemanticConditionIR,
    StructuredConditionIR,
)
from search_agent.school_tiers import (
    education_tier_values_from_query,
    query_mentions_undergraduate,
)
from search_agent.stages.base import Deps

logger = logging.getLogger(__name__)

_DIFF_ALIASES = {"medium": "mid", "mid": "mid", "low": "low", "high": "high"}
_ENTITY_FILTER_FIELDS = frozenset(
    {"position", "intention_position", "latest_work_job", "company", "latest_company"}
)


def _coerce_abilities(raw: Any) -> list[JobAbility]:
    out: list[JobAbility] = []
    for a in raw or []:
        if isinstance(a, JobAbility):
            out.append(a)
        elif isinstance(a, str):
            out.append(JobAbility(name=a))
        elif isinstance(a, dict):
            name = a.get("name") or a.get("ability") or a.get("skill")
            if not name:
                continue
            try:
                out.append(JobAbility.model_validate(a))
            except Exception:
                out.append(JobAbility(name=str(name)))
    return out


def _coerce_hard_constraints(raw: Any) -> list[StructuredConditionIR]:
    if isinstance(raw, dict):
        raw = [raw]
    out: list[StructuredConditionIR] = []
    for c in raw or []:
        if isinstance(c, StructuredConditionIR):
            out.append(c)
            continue
        if isinstance(c, dict):
            try:
                out.append(StructuredConditionIR.model_validate(c))
            except Exception:
                logger.debug("understand: skip invalid hard_constraint: %s", c)
    return out


def _relax_unstated_entity_filters(
    query: str,
    constraints: list[StructuredConditionIR],
) -> tuple[list[StructuredConditionIR], list[str]]:
    """Keep title/company filters only when the user wrote a concrete value.

    A model often expands "后端经验" into ``position any=[后端工程师, ...]``.
    That turns a capability search into an exact-title filter and excludes
    transferable candidates. Unstated entity values are returned so they can be
    preserved as semantic signals instead of being discarded.
    """
    normalized_query = re.sub(r"\s+", "", query.casefold())
    kept: list[StructuredConditionIR] = []
    relaxed: list[str] = []
    for condition in constraints:
        if condition.field not in _ENTITY_FILTER_FIELDS:
            kept.append(condition)
            continue
        mentioned = any(
            normalized_value and normalized_value in normalized_query
            for normalized_value in (
                re.sub(r"\s+", "", str(value).casefold()) for value in condition.values
            )
        )
        if mentioned:
            kept.append(condition)
        else:
            relaxed.extend(str(value) for value in condition.values if str(value).strip())
    return kept, relaxed


def _repair_education_tier_constraints(
    query: str,
    constraints: list[StructuredConditionIR],
) -> list[StructuredConditionIR]:
    """Keep undergraduate 985/211 on education rows instead of candidate tags."""

    tiers = education_tier_values_from_query(query)
    if not tiers:
        return constraints

    repaired: list[StructuredConditionIR] = []
    for condition in constraints:
        if condition.field == "talent_tag" and condition.operator != "not":
            remaining = [value for value in condition.values if str(value).strip() not in tiers]
            if len(remaining) != len(condition.values):
                if not remaining:
                    continue
                condition = condition.model_copy(update={"values": remaining})
        repaired.append(condition)

    fields = {condition.field for condition in repaired}
    if "degree" not in fields and query_mentions_undergraduate(query):
        repaired.append(
            StructuredConditionIR(
                field="degree",
                operator="eq",
                values=["本科"],
            )
        )
    if "school_tier" not in fields:
        repaired.append(
            StructuredConditionIR(
                field="school_tier",
                operator="eq" if len(tiers) == 1 else "any",
                values=tiers,
            )
        )
    return repaired


def _coerce_soft_signals(raw: Any) -> list[SemanticConditionIR]:
    out: list[SemanticConditionIR] = []
    for s in raw or []:
        if isinstance(s, dict):
            try:
                out.append(SemanticConditionIR.model_validate(s))
            except Exception:
                logger.debug("understand: skip invalid soft_signal: %s", s)
    return out


def _coerce_string_list(raw: Any) -> list[str]:
    """Coerce loose model output for fields typed as ``list[str]``.

    Some OpenAI-compatible gateways ignore the JSON schema for nested talent
    profile fields and return objects such as ``{"type": ..., "value": ...}``.
    Keep the useful text instead of failing the entire understand stage.
    """
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            value = item
        elif isinstance(item, dict):
            value = item.get("value") or item.get("text") or item.get("name") or item.get("type")
        elif item is not None:
            value = item
        else:
            continue
        text = str(value).strip()
        if text:
            out.append(text)
    return out


def _coerce_sources(raw: Any) -> list[CandidateSource]:
    out: list[CandidateSource] = []
    for s in raw or []:
        if isinstance(s, dict):
            try:
                out.append(CandidateSource.model_validate(s))
            except Exception:
                logger.debug("understand: skip invalid source: %s", s)
    return out


def run_understand(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    system, schema = understand_prompt()
    user = json.dumps(
        build_understand_payload(
            state["query"],
            datetime.now().astimezone().isoformat(timespec="seconds"),
            conversation_history=state.get("conversation_history"),
        ),
        ensure_ascii=False,
    )
    data = deps.llm.complete_json(
        system=system, user=user, schema_name="agent_understand", schema=schema
    )
    deps.budget_state.record_llm()

    # tolerate alias keys the model emits when strict mode is not enforced
    diff_raw = data.get("subjective_difficulty") or data.get("difficulty_level") or "mid"
    difficulty = _DIFF_ALIASES.get(str(diff_raw).strip().lower(), "mid")
    difficulty = resolve_difficulty(state["query"], difficulty)
    simple_lookup = is_explicit_simple_query(state["query"])
    tp = data.get("talent_profile") or data.get("initial_talent_profile") or {}
    if not isinstance(tp, dict):
        tp = {}

    hard_constraints = _coerce_hard_constraints(data.get("hard_constraints"))
    hard_constraints = _repair_education_tier_constraints(state["query"], hard_constraints)
    hard_constraints, relaxed_terms = _relax_unstated_entity_filters(
        state["query"], hard_constraints
    )
    soft_signals = _coerce_soft_signals(tp.get("soft_signals"))
    must_keywords = _coerce_string_list(tp.get("must_keywords"))
    existing_soft_texts = {condition.text.casefold() for condition in soft_signals}
    for term in relaxed_terms:
        if term.casefold() not in {str(keyword).casefold() for keyword in must_keywords}:
            must_keywords.append(term)
        if term.casefold() not in existing_soft_texts:
            soft_signals.append(
                SemanticConditionIR(
                    text=term,
                    fields=["work_vector", "skills_vector"],
                    weight=1.2,
                )
            )
            existing_soft_texts.add(term.casefold())

    llm_needs_clarification = bool(data.get("needs_clarification", False)) and not simple_lookup
    if llm_needs_clarification and is_actionable_recruitment_query(state["query"]):
        logger.info("understand: ignoring clarification request for actionable query")
        llm_needs_clarification = False

    job = JobProfile(
        raw_query=state["query"],
        title=data.get("title"),
        must_abilities=_coerce_abilities(data.get("must_abilities")),
        nice_abilities=_coerce_abilities(data.get("nice_abilities")),
        hard_constraints=hard_constraints,
        subjective_difficulty=difficulty,
        difficulty_reason=data.get("difficulty_reason", "") or "",
        needs_clarification=llm_needs_clarification,
        clarify_questions=(
            (data.get("clarify_questions", []) or []) if llm_needs_clarification else []
        ),
    )
    clarification = detect_skill_only_clarification(
        state["query"],
        has_history=bool(state.get("conversation_history")),
    )
    if clarification is not None:
        job = job.model_copy(
            update={
                "needs_clarification": True,
                "clarify_questions": clarification.questions,
            }
        )
    talent = TalentProfile(
        source_hypotheses=_coerce_sources(tp.get("source_hypotheses")),
        must_keywords=must_keywords,
        soft_signals=soft_signals,
        risk_filters=_coerce_string_list(tp.get("risk_filters")),
    )

    updates: dict[str, Any] = {"job_profile": job, "talent_profile": talent}
    if clarification is not None:
        updates["clarify_options"] = [option.to_dict() for option in clarification.options]
    if job.needs_clarification:
        updates.update({"need_clarify": True, "phase": "done"})
    else:
        updates["phase"] = "plan"
    return updates


__all__ = ["run_understand"]
