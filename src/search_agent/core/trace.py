"""agentTrace builder. Slotted into SearchResponse.debug."""

from __future__ import annotations

from typing import Any

from search_agent.models.artifacts import (
    PathResult,
    Reflection,
    SearchPath,
)


def path_trace(p: SearchPath, pr: PathResult | None) -> dict[str, Any]:
    return {
        "id": p.id,
        "rationale": p.rationale,
        "expectedYield": p.expected_yield,
        "status": p.status,
        "quality": (pr.quality if pr else "empty"),
        "recalled": (pr.tool_result.total if pr else 0),
        "judged": (len(pr.judgments) if pr else 0),
    }


def iteration_trace(
    *,
    index: int,
    phase: str,
    paths: list[SearchPath],
    path_results: list[PathResult],
    reflection: Reflection | None,
    llm_calls: int,
    elapsed_ms: int,
) -> dict[str, Any]:
    pr_by_id = {pr.path_id: pr for pr in path_results}
    return {
        "index": index,
        "phase": phase,
        "paths": [path_trace(p, pr_by_id.get(p.id)) for p in paths],
        "reflection": (
            {
                "commonTraits": reflection.common_traits,
                "gaps": reflection.gaps,
                "inferredSoftSignals": [s.model_dump() for s in reflection.inferred_soft_signals],
                "pathActions": [a.model_dump() for a in reflection.path_actions],
            }
            if reflection
            else None
        ),
        "budgetUsed": {"llmCalls": llm_calls, "elapsedMs": elapsed_ms},
    }


def build_agent_trace(
    *,
    iterations: list[dict[str, Any]],
    merged_pool_size: int,
    reranked_top_k: int,
    rerank_used: bool,
    budget: dict[str, Any],
    budget_used: dict[str, Any],
    degraded_reason: str | None,
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "iterations": iterations,
        "final": {
            "mergedPoolSize": merged_pool_size,
            "rerankedTopK": reranked_top_k,
            "rerankUsed": rerank_used,
        },
        "budget": {**budget, "used": budget_used},
        "degradedReason": degraded_reason,
        "toolCalls": tool_calls,
    }


__all__ = ["build_agent_trace", "iteration_trace", "path_trace"]
