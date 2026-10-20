"""finalize: converged final search (or merge+rerank) -> final ranked items.

When the talent profile was enriched through the reflection loop, run ONE
definitive ``search_with_intent`` using the fully-evolved profile. The enriched
semantic signals (common traits, source hypotheses) broaden vector recall, so
the final search can surface people keyword search alone cannot find. The full
recall page is judged once before the top-k cut; the old behaviour of cutting to
top-k first made relevant candidates below that cut invisible to the ranking.

When the profile was NOT enriched (single_pass / degraded / no reflections) or
the converged search fails, fall back to the cross-path merge + ``rerank_pool``
path. No composite formula.
"""

from __future__ import annotations

import logging
from typing import Any

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    ToolCandidate,
)
from search_agent.core.degrade import degrade
from search_agent.core.merge import merge_into
from search_agent.models.artifacts import PathResult
from search_agent.models.ir import (
    CandidateSearchIR,
    SemanticConditionIR,
    WhereNode,
)
from search_agent.stages.base import Deps
from search_agent.stages.judge import _judge_path

logger = logging.getLogger(__name__)

_MAX_CONVERGED_SEMANTIC = 12


def _build_merged_pool(path_results: list[PathResult]) -> list[ToolCandidate]:
    """Dedup ToolCandidates by doc_id across all path results; keep best score."""
    best: dict[str, ToolCandidate] = {}
    for pr in path_results:
        for tc in pr.tool_result.items:
            if tc.doc_id not in best or tc.score > best[tc.doc_id].score:
                best[tc.doc_id] = tc
    return list(best.values())


def _dedupe_candidates(*groups: list[ToolCandidate]) -> list[ToolCandidate]:
    """Union candidate groups by doc_id, keeping the highest backend score."""
    best: dict[str, ToolCandidate] = {}
    for group in groups:
        for tc in group:
            if tc.doc_id not in best or tc.score > best[tc.doc_id].score:
                best[tc.doc_id] = tc
    return list(best.values())


def _hard_required_violations(tc: ToolCandidate) -> int:
    """Count explicit required hard-condition failures on a candidate."""
    hard = tc.hard_matches or (tc.match_explanation or {}).get("hardMatches") or []
    if not isinstance(hard, list):
        return 0
    return sum(
        1
        for hm in hard
        if isinstance(hm, dict) and hm.get("matched") is False and hm.get("required", True)
    )


_ABILITY_EVIDENCE_SYNONYMS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("服务端", "后端"),
        (
            "后端",
            "服务端",
            "业务系统",
            "交易",
            "支付",
            "订单",
            "电商",
            "网关",
            "研发工程师",
            "java",
            "go",
            "python",
            "微服务",
        ),
    ),
    (
        ("高并发",),
        ("高并发", "容量", "压测", "洪峰", "qps", "吞吐", "性能优化", "限流", "降级", "稳定性"),
    ),
    (
        ("微服务",),
        ("微服务", "spring cloud", "服务治理", "服务拆分", "注册发现", "dubbo", "service mesh"),
    ),
    (
        ("系统架构", "架构设计"),
        ("架构", "容量规划", "高可用", "技术选型", "容灾", "稳定性", "分布式"),
    ),
    (
        ("分布式",),
        ("分布式", "kafka", "消息队列", "分布式事务", "一致性"),
    ),
    (
        ("推荐",),
        ("推荐", "召回", "排序", "ctr", "a/b实验"),
    ),
    (
        ("数据工程",),
        ("spark", "flink", "数据仓库", "数仓", "数据平台", "etl"),
    ),
    (
        ("机器学习",),
        ("机器学习", "深度学习", "pytorch", "tensorflow", "模型训练"),
    ),
)


def _candidate_text(tc: ToolCandidate) -> str:
    """Text blob used only for explainable resume-evidence coverage."""
    item = tc.item
    parts: list[str] = [str(item.summary or "")]
    for skill in item.skills or []:
        if isinstance(skill, dict):
            parts.append(str(skill.get("name", "")))
    for work in item.workExperiences or []:
        if isinstance(work, dict):
            parts.append(
                str(work.get("job_title") or work.get("jobTitle") or work.get("position") or "")
            )
            parts.append(str(work.get("company_name") or work.get("companyName") or ""))
    parts.extend(str(position) for position in item.expectedPositions or [])
    for cert in item.certificates or []:
        if isinstance(cert, dict):
            parts.append(str(cert.get("name", "")))
    return " ".join(parts).lower()


def _ability_evidence_terms(ability: Any) -> set[str]:
    """Combine LLM-provided hints with a small transferable-role vocabulary."""
    name = str(getattr(ability, "name", "") or "").lower()
    terms = {
        str(hint).lower()
        for hint in (getattr(ability, "evidence_hints", None) or [])
        if str(hint).strip()
    }
    for keys, synonyms in _ABILITY_EVIDENCE_SYNONYMS:
        if any(key in name for key in keys):
            terms.update(synonyms)
    return terms


def _resume_evidence_coverage(tc: ToolCandidate, must_abilities: list[Any]) -> tuple[int, int]:
    """How many must abilities have resume evidence; (matched, missing)."""
    text = _candidate_text(tc)
    matched = 0
    for ability in must_abilities:
        terms = _ability_evidence_terms(ability)
        if terms and any(term in text for term in terms):
            matched += 1
    return matched, len(must_abilities) - matched


def _selection_key(
    tc: ToolCandidate,
    accumulated: dict[str, Any],
    must_abilities: list[Any],
) -> tuple[int, int, int, int, int, int, int, float, str]:
    """Rank judged candidates by ability coverage before backend similarity.

    Order: LLM-judged must coverage (desc), missing must abilities (asc), required
    hard-condition violations (asc), resume-evidence coverage (desc), evidence
    gaps (asc), unjudged penalty, total matched abilities, backend score, doc_id.
    The evidence terms resolve ties where the LLM misses an implicit ability.
    """
    must_names = {str(getattr(a, "name", "")) for a in must_abilities}
    judgment = accumulated.get(tc.doc_id)
    matched = set(judgment.matched_abilities) if judgment else set()
    missing_must = len(must_names - matched)
    evidence_hits, evidence_missing = _resume_evidence_coverage(tc, must_abilities)
    return (
        -len(matched & must_names),
        missing_must,
        _hard_required_violations(tc),
        -evidence_hits,
        evidence_missing,
        0 if judgment else 1,
        -len(matched),
        -tc.score,
        tc.doc_id,
    )


def _build_converged_intent(state: dict[str, Any]) -> CandidateSearchIR | None:
    """Build the definitive intent from the fully-evolved talent profile.

    ``where`` = job hard_constraints (legitimate structured filters only, e.g.
    work_years; never keyword position/skill filters that would exclude people).
    ``semantic`` = every converged soft_signal + source_hypothesis, so vector
    recall is driven by the richest possible profile. Returns None when there
    are no hard_constraints to anchor the where (mirrors plan._default_path).
    """
    job = state["job_profile"]
    if not job.hard_constraints:
        return None
    leaves = [WhereNode(type="leaf", condition=c) for c in job.hard_constraints]
    where = leaves[0] if len(leaves) == 1 else WhereNode(type="and", children=leaves)

    talent = state.get("talent_profile")
    semantic: list[SemanticConditionIR] = []
    seen_texts: set[str] = set()
    if talent is not None:
        for src in getattr(talent, "source_hypotheses", []) or []:
            if not src.value or src.value in seen_texts:
                continue
            fields = ["project_vector"] if src.kind in ("product", "project") else ["work_vector"]
            semantic.append(SemanticConditionIR(text=src.value, fields=fields, weight=1.5))
            seen_texts.add(src.value)
        for sig in getattr(talent, "soft_signals", []) or []:
            if sig.text and sig.text not in seen_texts:
                semantic.append(sig)
                seen_texts.add(sig.text)
    semantic = sorted(semantic, key=lambda s: s.weight, reverse=True)[:_MAX_CONVERGED_SEMANTIC]
    return CandidateSearchIR(where=where, semantic=semantic)


def _judge_converged_new(
    deps: Deps,
    state: dict[str, Any],
    final_items: list[ToolCandidate],
    accumulated: dict[str, Any],
) -> int:
    """Judge converged-search candidates not yet in the accumulated pool.

    The converged final search surfaces people the path loop never judged. This
    verifies they actually match the job abilities (grounding-checked) so the
    final ranking and metrics reflect true quality -- not just vector proximity.
    Costs ~1 LLM call (batched). Skips when budget is exhausted or no new
    candidates exist. Returns the count of newly judged candidates.
    """
    new_cands = [tc for tc in final_items if tc.doc_id not in accumulated]
    if not new_cands:
        return 0
    bs = deps.budget_state
    if bs.llm_exceeded() or bs.time_exceeded():
        return 0
    job = state["job_profile"]
    abilities = [a.name for a in job.must_abilities + job.nice_abilities]
    ability_hints = {
        a.name: list(a.evidence_hints or []) for a in job.must_abilities + job.nice_abilities
    }
    try:
        judgments = _judge_path(
            deps,
            query=job.raw_query,
            abilities=abilities,
            path_id="final",
            cands=new_cands,
            ability_hints=ability_hints,
        )
        merge_into(accumulated, judgments)
        return len(judgments)
    except Exception:  # noqa: BLE001 - keep converged ranking without judgments
        return 0


def _enrich(tc: ToolCandidate, accumulated, path_rationale) -> ToolCandidate:
    """Attach accumulated matched_abilities + path rationale to match_explanation."""
    judgment = accumulated.get(tc.doc_id)
    if not judgment:
        return tc
    explanation = dict(tc.match_explanation or {})
    explanation["matchedAbilities"] = judgment.matched_abilities
    explanation["missingAbilities"] = judgment.missing_abilities
    explanation["pathRationale"] = [path_rationale.get(pid, "") for pid in judgment.source_path_ids]
    return tc.model_copy(update={"match_explanation": explanation})


def _validate_output(final_items: list[ToolCandidate], state: dict[str, Any]) -> list[str]:
    """Post-finalize sanity checks. Returns list of issue strings (empty = OK)."""
    issues: list[str] = []
    if not final_items:
        if not state.get("need_clarify") and not state.get("fallback_baseline"):
            if state.get("accumulated"):
                issues.append("final_empty_but_pool_nonempty")
            else:
                issues.append("empty_result_no_clarification")
        return issues
    for tc in final_items:
        if tc.score < 0:
            issues.append(f"negative_score:{tc.doc_id}")
        hard = tc.match_explanation.get("hardMatches") if tc.match_explanation else None
        if isinstance(hard, list):
            for hm in hard:
                if isinstance(hm, dict) and hm.get("matched") is False and hm.get("required", True):
                    issues.append(f"hard_violation:{tc.doc_id}:{hm.get('field', '?')}")
                    break
    return issues


def run_finalize(state: dict[str, Any], deps: Deps) -> dict[str, Any]:
    if state.get("need_clarify"):
        return {"final_items": [], "final_total": 0, "phase": "done"}

    top_k = state["top_k"]
    accumulated = state.get("accumulated", {})
    path_results = state.get("path_results", [])
    paths = state.get("paths", [])
    path_rationale = {p.id: p.rationale for p in paths}
    degraded_reason = state.get("degraded_reason")
    rerank_used = False
    final_search_new = 0
    final_search_total = 0

    merged_pool = _build_merged_pool(path_results)
    pool_by_id = {tc.doc_id: tc for tc in merged_pool}
    job_context: dict[str, Any] = {
        "abilities": [
            a.name
            for a in state["job_profile"].must_abilities + state["job_profile"].nice_abilities
        ],
        "hard_constraints": [c.model_dump() for c in state["job_profile"].hard_constraints],
    }

    final_items: list[ToolCandidate] = []
    converged_pool: list[ToolCandidate] = []

    # --- converged final search (only when the profile was enriched) ---------
    # The reflection loop evolved the talent profile; use it for one definitive
    # search. The enriched semantic signals broaden vector recall, so people
    # keyword search could not find can now surface. Same LLM token cost as
    # rerank_pool (one rerank) plus one ES query.
    no_converged = bool(deps.ablation and deps.ablation.no_converged_search)
    enriched = (
        bool(state.get("reflections")) and not state.get("fallback_baseline") and not no_converged
    )
    converged = _build_converged_intent(state) if enriched else None
    converged_semantic = (
        [(s.text, s.weight) for s in converged.semantic] if converged is not None else []
    )
    if converged is not None:
        try:
            fs_result = deps.tool.search_with_intent(
                SearchToolRequest(
                    intent=converged,
                    query_text=state["query"],
                    company_id=state["company_id"],
                    hr_id=state.get("hr_id"),
                    permission=state.get("permission"),
                    top_k=top_k,
                    recall_pool_size=state.get("recall_pool_size"),
                    routing=state["routing"],
                    exclude_doc_ids=list(state.get("excluded_doc_ids") or []),
                    details=state.get("details", False),
                )
            )
            rerank_used = True
            final_search_total = fs_result.total
            for tc in fs_result.items:
                if tc.doc_id not in accumulated:
                    final_search_new += 1
            # Keep the complete recall page: judging or selecting only the first
            # top-k silently discards relevant candidates surfaced at ranks 6..N.
            converged_pool = list(fs_result.items)
            logger.info(
                "converged final search: total=%d items=%d new=%d",
                fs_result.total,
                len(fs_result.items),
                final_search_new,
            )
        except Exception as exc:  # noqa: BLE001 - fall back to merge+rerank
            logger.warning("converged final search failed: %s", exc)
            converged = None

    final_judged = 0
    _job = state.get("job_profile")
    _must_abilities = list(_job.must_abilities) if _job else []

    if converged is not None and converged_pool:
        # Union the enriched converged page with the already-judged path pool,
        # then judge every newly surfaced candidate before choosing top-k.
        candidate_pool = _dedupe_candidates(converged_pool, merged_pool)
        final_judged = _judge_converged_new(deps, state, candidate_pool, accumulated)
        final_items = [_enrich(tc, accumulated, path_rationale) for tc in candidate_pool]
        final_items.sort(key=lambda tc: _selection_key(tc, accumulated, _must_abilities))
        final_items = final_items[:top_k]
    else:
        # --- fallback: global rerank over the accumulated cross-path pool ---
        # single_pass / no enrichment / converged-search-failed.
        if not merged_pool:
            logger.warning(
                "finalize: empty result, merged_pool=0 (path_results=%d accumulated=%d)",
                len(path_results),
                len(accumulated),
            )
        if merged_pool:
            try:
                result = deps.tool.rerank_pool(
                    RerankToolRequest(
                        query_text=state["query"],
                        candidates=merged_pool,
                        job_context=job_context,
                        top_k=top_k,
                        routing=state["routing"],
                    )
                )
                rerank_used = True
                final_items = list(result.items[:top_k])
            except Exception:  # noqa: BLE001 - fallback per 15.4
                _, reason = degrade("rerank_pool", "llm_failed")
                degraded_reason = reason
                final_items = sorted(merged_pool, key=lambda tc: tc.score, reverse=True)[:top_k]

        # Fill any shortfall from the judged accumulated pool.
        if len(final_items) < top_k and accumulated:
            have = {tc.doc_id for tc in final_items}
            fill = [
                pool_by_id[j.doc_id]
                for j in accumulated.values()
                if j.doc_id not in have and j.doc_id in pool_by_id
            ]
            fill.sort(key=lambda tc: tc.score, reverse=True)
            final_items = final_items + fill[: top_k - len(final_items)]

        final_items = [_enrich(tc, accumulated, path_rationale) for tc in final_items[:top_k]]
        final_items.sort(key=lambda tc: _selection_key(tc, accumulated, _must_abilities))

    validation_issues = _validate_output(final_items, state)

    return {
        "final_items": final_items,
        "final_total": len(final_items),
        "accumulated": accumulated,
        "rerank_used": rerank_used,
        "degraded_reason": degraded_reason,
        "validation_issues": validation_issues,
        "final_search_new": final_search_new,
        "final_search_total": final_search_total,
        "final_search_semantic": converged_semantic,
        "final_judged": final_judged,
        "phase": "done",
    }


__all__ = ["run_finalize"]
