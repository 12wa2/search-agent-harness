"""Agent-level search quality metrics.

Collected from the final SearchState after the harness loop completes.
Five metric groups:
  1. Recall success:   qualified / target, quality distribution
  2. Search rounds:    iteration count, per-round new qualified (marginal curve)
  3. Match quality:    matched-ability coverage, hard-match hit rate
  4. Reasoning power:  source_hypotheses -> qualified conversion
  5. Reflection power: new qualified after each reflect->revise cycle
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentMetrics:
    """Five-group quality snapshot of one agent run."""

    # 1. Recall success
    recall_rate: float = 0.0
    judged_count: int = 0
    truly_matched: int = 0
    target_qualified: int = 0
    quality_distribution: dict[str, int] = field(default_factory=dict)
    #: Doc ids any path returned this session (deduped); feeds discovery metrics.
    surfaced_doc_ids: list[str] = field(default_factory=list)

    # 2. Search rounds
    iterations: int = 0
    new_qualified_per_round: list[int] = field(default_factory=list)

    # 3. Match quality
    matched_ability_coverage: float = 0.0
    hard_match_hit_rate: float = 0.0

    # 4. Reasoning power
    source_conversion: dict[str, int] = field(default_factory=dict)

    # 5. Reflection power
    reflection_gain: list[int] = field(default_factory=list)

    # Meta
    total_llm_calls: int = 0
    llm_retries: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_estimated: bool = False
    total_elapsed_ms: int = 0
    degraded_reason: str | None = None
    rerank_used: bool = False
    fallback_baseline: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "recall": {
                "rate": round(self.recall_rate, 3),
                "judged": self.judged_count,
                "trulyMatched": self.truly_matched,
                "target": self.target_qualified,
                "qualityDistribution": self.quality_distribution,
                "surfacedDocIds": self.surfaced_doc_ids,
            },
            "rounds": {
                "iterations": self.iterations,
                "newQualifiedPerRound": self.new_qualified_per_round,
            },
            "match": {
                "abilityCoverage": round(self.matched_ability_coverage, 3),
                "hardMatchHitRate": round(self.hard_match_hit_rate, 3),
            },
            "reasoning": {
                "sourceConversion": self.source_conversion,
            },
            "reflection": {
                "gain": self.reflection_gain,
            },
            "meta": {
                "llmCalls": self.total_llm_calls,
                "llmRetries": self.llm_retries,
                "promptTokens": self.prompt_tokens,
                "completionTokens": self.completion_tokens,
                "totalTokens": self.prompt_tokens + self.completion_tokens,
                "tokensEstimated": self.tokens_estimated,
                "elapsedMs": self.total_elapsed_ms,
                "degradedReason": self.degraded_reason,
                "rerankUsed": self.rerank_used,
                "fallbackBaseline": self.fallback_baseline,
            },
        }


def collect_metrics(state: dict[str, Any], bs: Any) -> AgentMetrics:
    """Extract metrics from the final SearchState + BudgetState."""
    accumulated: dict = state.get("accumulated", {})
    budget = state.get("budget")
    path_results = state.get("path_results", [])
    job_profile = state.get("job_profile")
    talent_profile = state.get("talent_profile")
    paths = state.get("paths", [])

    # --- 1. Recall success ---
    judged_count = len(accumulated)
    target = budget.target_qualified if budget else 20
    recall_rate = judged_count / target if target > 0 else 0.0
    must_names = set()
    if job_profile:
        must_names = {a.name for a in job_profile.must_abilities}
    truly_matched = 0
    for j in accumulated.values():
        if (
            j.matched_abilities
            and must_names
            and any(ma in must_names for ma in j.matched_abilities)
        ):
            truly_matched += 1
    quality_dist: dict[str, int] = {}
    for pr in path_results:
        quality_dist[pr.quality] = quality_dist.get(pr.quality, 0) + 1

    # --- 2. Search rounds ---
    iterations = state.get("iteration", 0)
    qualified_per_round: list[int] = state.get("qualified_per_round", [])

    # --- 3. Match quality ---
    total_abilities = 0
    if job_profile:
        total_abilities = len(job_profile.must_abilities) + len(job_profile.nice_abilities)
    matched_cov_sum = 0.0
    hard_hit_sum = 0.0
    judged_count = 0
    for j in accumulated.values():
        if total_abilities > 0:
            matched_cov_sum += len(j.matched_abilities) / total_abilities
        if j.hard_matches:
            hit = sum(1 for hm in j.hard_matches if hm.get("matched"))
            hard_hit_sum += hit / len(j.hard_matches)
        judged_count += 1
    matched_cov = matched_cov_sum / judged_count if judged_count > 0 else 0.0
    hard_hit = hard_hit_sum / judged_count if judged_count > 0 else 0.0

    # --- 4. Reasoning power ---
    source_conversion: dict[str, int] = {}
    if talent_profile:
        for src in talent_profile.source_hypotheses:
            path_ids = [p.id for p in paths if src.id in (p.source_refs or [])]
            if path_ids:
                cnt = sum(
                    1
                    for j in accumulated.values()
                    if any(pid in j.source_path_ids for pid in path_ids)
                )
                source_conversion[src.id] = cnt
            else:
                source_conversion[src.id] = 0

    # --- 5. Reflection power ---
    reflection_gain: list[int] = []
    for i in range(1, len(qualified_per_round)):
        reflection_gain.append(qualified_per_round[i] - qualified_per_round[i - 1])

    surfaced_doc_ids: list[str] = []
    seen_surfaced: set[str] = set()
    for pr in path_results:
        for candidate in pr.tool_result.items:
            if candidate.doc_id not in seen_surfaced:
                seen_surfaced.add(candidate.doc_id)
                surfaced_doc_ids.append(candidate.doc_id)
    # Converged final-search candidates are judged into ``accumulated`` but do
    # not belong to any PathResult. They are still part of the recall pool, so
    # excluding them under-reports the agent's discovery coverage.
    for doc_id in accumulated:
        if doc_id not in seen_surfaced:
            seen_surfaced.add(doc_id)
            surfaced_doc_ids.append(doc_id)
    for candidate in state.get("final_items") or []:
        if candidate.doc_id not in seen_surfaced:
            seen_surfaced.add(candidate.doc_id)
            surfaced_doc_ids.append(candidate.doc_id)

    return AgentMetrics(
        recall_rate=recall_rate,
        judged_count=judged_count,
        truly_matched=truly_matched,
        target_qualified=target,
        quality_distribution=quality_dist,
        surfaced_doc_ids=surfaced_doc_ids,
        iterations=iterations,
        new_qualified_per_round=qualified_per_round,
        matched_ability_coverage=matched_cov,
        hard_match_hit_rate=hard_hit,
        source_conversion=source_conversion,
        reflection_gain=reflection_gain,
        total_llm_calls=bs.llm_calls,
        llm_retries=bs.llm_retries,
        prompt_tokens=bs.prompt_tokens,
        completion_tokens=bs.completion_tokens,
        tokens_estimated=bs.tokens_estimated,
        total_elapsed_ms=int(bs.elapsed_sec() * 1000),
        degraded_reason=state.get("degraded_reason"),
        rerank_used=bool(state.get("rerank_used", False)),
        fallback_baseline=bool(state.get("fallback_baseline", False)),
    )


def emit_metrics(metrics: AgentMetrics, query: str | None = None) -> None:
    """Log metrics for observability. Extend to push to monitoring system."""
    logger.info(
        "[search-agent-metrics] recall=%.2f judged=%d/%d trulyMatched=%d iterations=%d "
        "llmCalls=%d elapsedMs=%d degraded=%s rerankUsed=%s",
        metrics.recall_rate,
        metrics.judged_count,
        metrics.target_qualified,
        metrics.truly_matched,
        metrics.iterations,
        metrics.total_llm_calls,
        metrics.total_elapsed_ms,
        metrics.degraded_reason,
        metrics.rerank_used,
    )


__all__ = ["AgentMetrics", "collect_metrics", "emit_metrics"]
