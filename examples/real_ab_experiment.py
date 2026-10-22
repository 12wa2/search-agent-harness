"""Real-LLM A/B experiment: one-shot Hybrid V2 baseline vs reflective Agent.

This script is intentionally opt-in: it calls a configured OpenAI-compatible
endpoint and a live Elasticsearch index. It does not ship credentials or real
resume data.

Example::

    $env:SEARCH_AGENT_BASE_URL = "http://127.0.0.1:4000/v1"
    $env:SEARCH_AGENT_API_KEY = "local-gateway-key"
    $env:SEARCH_AGENT_MODEL = "search-agent-primary"
    $env:SEARCH_AGENT_ES_URL = "http://127.0.0.1:9200"
    $env:SEARCH_AGENT_ES_INDEX = "search-agent-hybrid-v2-bench100"
    $env:SEARCH_AGENT_TIME_BUDGET_SEC = "180"
    $env:SEARCH_AGENT_MAX_ITERATIONS = "2"
    $env:SEARCH_AGENT_TARGET_QUALIFIED = "99"
    python examples/real_ab_experiment.py `
      --benchmark examples/real_ab_queries_100.json --top-k 5 --workers 4
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from search_agent.eval import PipelineResult, run_agent, run_single_pass
from search_agent.integrations.hybrid_v2 import build_hybrid_v2_tool
from search_agent.llm.client import OpenAILlmClient


def _load_benchmark(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    queries = payload.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("benchmark must contain a non-empty queries array")
    for row in queries:
        if not row.get("id") or not row.get("query") or not row.get("relevant"):
            raise ValueError("each query needs id, query, and relevant")
    return payload


def _rates(
    delivered: tuple[str, ...], surfaced: tuple[str, ...], relevant: set[str], top_k: int
) -> dict[str, float]:
    delivered_set = set(delivered)
    relevant_delivered = delivered_set & relevant
    return {
        "recall": len(relevant_delivered) / len(relevant) if relevant else 0.0,
        "precision": len(relevant_delivered) / len(delivered_set) if delivered_set else 0.0,
        "pool_recall": len(set(surfaced) & relevant) / len(relevant) if relevant else 0.0,
        "hit_at_k": 1.0 if relevant_delivered else 0.0,
    }


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(round(0.95 * (len(ordered) - 1))))
    return ordered[index]


def _bootstrap_mean_ci(
    values: list[float],
    *,
    iterations: int = 2000,
    seed: int = 2026,
) -> tuple[float, float]:
    """Percentile bootstrap CI for a mean; descriptive, not a significance test."""
    if not values:
        return 0.0, 0.0
    if len(values) == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    means = [sum(rng.choice(values) for _ in values) / len(values) for _ in range(iterations)]
    means.sort()
    low = means[int(0.025 * (iterations - 1))]
    high = means[int(0.975 * (iterations - 1))]
    return low, high


def _result_summary(result: PipelineResult) -> dict[str, Any]:
    return {
        "qualified": result.qualified,
        "target": result.target,
        "recall_rate": result.recall_rate,
        "final_count": result.final_count,
        "llm_calls": result.llm_calls,
        "tokens": result.tokens,
        "tokens_estimated": result.tokens_estimated,
        "elapsed_ms": result.elapsed_ms,
        "degraded_reason": result.degraded_reason,
        "doc_ids": list(result.doc_ids),
        "surfaced_ids": list(result.surfaced_ids),
    }


def _run_query(spec: dict[str, Any], *, top_k: int) -> dict[str, Any]:
    tool = build_hybrid_v2_tool()
    relevant = set(spec["relevant"])
    baseline = run_single_pass(OpenAILlmClient(), tool, spec["query"], top_k=top_k)
    agent = run_agent(OpenAILlmClient(), tool, spec["query"], top_k=top_k)
    baseline_ids = set(baseline.doc_ids)
    agent_ids = set(agent.doc_ids)
    novel_ids = agent_ids - baseline_ids
    novel_relevant = novel_ids & relevant
    row = {
        "id": spec["id"],
        "query": spec["query"],
        "must": spec.get("must", []),
        "relevant": sorted(relevant),
        "baseline": _result_summary(baseline),
        "agent": _result_summary(agent),
        "baseline_metrics": _rates(baseline.doc_ids, baseline.surfaced_ids, relevant, top_k),
        "agent_metrics": _rates(agent.doc_ids, agent.surfaced_ids, relevant, top_k),
        "novel_ids": sorted(novel_ids),
        "novel_relevant_ids": sorted(novel_relevant),
        "novel_count": len(novel_ids),
        "novel_relevant_count": len(novel_relevant),
        "novel_precision": len(novel_relevant) / len(novel_ids) if novel_ids else 0.0,
    }
    print(
        f"[{spec['id']}] baseline={len(baseline_ids)} relevant={len(baseline_ids & relevant)} "
        f"agent={len(agent_ids)} relevant={len(agent_ids & relevant)} "
        f"novel_relevant={len(novel_relevant)}",
        flush=True,
    )
    return row


def run_benchmark(
    benchmark: dict[str, Any],
    *,
    top_k: int,
    workers: int = 1,
) -> dict[str, Any]:
    started = time.perf_counter()
    specs = list(benchmark["queries"])
    worker_count = max(1, min(workers, len(specs)))
    if worker_count == 1:
        rows = [_run_query(spec, top_k=top_k) for spec in specs]
    else:
        ordered: list[dict[str, Any] | None] = [None] * len(specs)
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            futures = {
                pool.submit(_run_query, spec, top_k=top_k): index
                for index, spec in enumerate(specs)
            }
            for future in as_completed(futures):
                ordered[futures[future]] = future.result()
        rows = [row for row in ordered if row is not None]

    count = len(rows)
    baseline_recalls = [r["baseline_metrics"]["recall"] for r in rows]
    agent_recalls = [r["agent_metrics"]["recall"] for r in rows]
    baseline_pool_recalls = [r["baseline_metrics"]["pool_recall"] for r in rows]
    agent_pool_recalls = [r["agent_metrics"]["pool_recall"] for r in rows]
    baseline_precisions = [r["baseline_metrics"]["precision"] for r in rows]
    agent_precisions = [r["agent_metrics"]["precision"] for r in rows]
    baseline_latency = [float(r["baseline"]["elapsed_ms"]) for r in rows]
    agent_latency = [float(r["agent"]["elapsed_ms"]) for r in rows]
    recall_deltas = [a - b for a, b in zip(agent_recalls, baseline_recalls, strict=True)]
    pool_recall_deltas = [
        a - b for a, b in zip(agent_pool_recalls, baseline_pool_recalls, strict=True)
    ]
    recall_delta_ci = _bootstrap_mean_ci(recall_deltas)
    pool_recall_delta_ci = _bootstrap_mean_ci(pool_recall_deltas)

    aggregate = {
        "queries": count,
        "top_k": top_k,
        "baseline_mean_recall": sum(baseline_recalls) / count,
        "agent_mean_recall": sum(agent_recalls) / count,
        "recall_delta": sum(recall_deltas) / count,
        "recall_delta_ci95": list(recall_delta_ci),
        "baseline_mean_pool_recall": sum(baseline_pool_recalls) / count,
        "agent_mean_pool_recall": sum(agent_pool_recalls) / count,
        "pool_recall_delta": sum(pool_recall_deltas) / count,
        "pool_recall_delta_ci95": list(pool_recall_delta_ci),
        "baseline_mean_precision": sum(baseline_precisions) / count,
        "agent_mean_precision": sum(agent_precisions) / count,
        "total_novel_candidates": sum(r["novel_count"] for r in rows),
        "total_novel_relevant": sum(r["novel_relevant_count"] for r in rows),
        "mean_novel_relevant": sum(r["novel_relevant_count"] for r in rows) / count,
        "novel_precision": (
            sum(r["novel_relevant_count"] for r in rows) / sum(r["novel_count"] for r in rows)
            if sum(r["novel_count"] for r in rows)
            else 0.0
        ),
        "baseline_llm_calls": sum(r["baseline"]["llm_calls"] for r in rows),
        "agent_llm_calls": sum(r["agent"]["llm_calls"] for r in rows),
        "baseline_tokens": sum(r["baseline"]["tokens"] for r in rows),
        "agent_tokens": sum(r["agent"]["tokens"] for r in rows),
        "baseline_p50_ms": statistics.median(baseline_latency),
        "agent_p50_ms": statistics.median(agent_latency),
        "baseline_p95_ms": _p95(baseline_latency),
        "agent_p95_ms": _p95(agent_latency),
        "baseline_degraded": sum(1 for r in rows if r["baseline"]["degraded_reason"]),
        "agent_degraded": sum(1 for r in rows if r["agent"]["degraded_reason"]),
        "llm_call_multiplier": (
            sum(r["agent"]["llm_calls"] for r in rows)
            / sum(r["baseline"]["llm_calls"] for r in rows)
            if sum(r["baseline"]["llm_calls"] for r in rows)
            else 0.0
        ),
        "token_multiplier": (
            sum(r["agent"]["tokens"] for r in rows) / sum(r["baseline"]["tokens"] for r in rows)
            if sum(r["baseline"]["tokens"] for r in rows)
            else 0.0
        ),
        "latency_multiplier": (
            statistics.median(agent_latency) / statistics.median(baseline_latency)
            if statistics.median(baseline_latency)
            else 0.0
        ),
    }

    return {
        "experiment": "real_llm_a_ablation",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "model": os.environ.get("SEARCH_AGENT_MODEL", ""),
        "gateway": os.environ.get("SEARCH_AGENT_BASE_URL", ""),
        "es_index": os.environ.get("SEARCH_AGENT_ES_INDEX", ""),
        "config": {
            "top_k": top_k,
            "workers": worker_count,
            "time_budget_sec": os.environ.get("SEARCH_AGENT_TIME_BUDGET_SEC"),
            "max_iterations": os.environ.get("SEARCH_AGENT_MAX_ITERATIONS"),
            "max_paths_per_iteration": os.environ.get("SEARCH_AGENT_MAX_PATHS_PER_ITERATION"),
            "target_qualified": os.environ.get("SEARCH_AGENT_TARGET_QUALIFIED"),
            "disable_thinking": os.environ.get("SEARCH_AGENT_DISABLE_THINKING"),
            "final_selection_policy": os.environ.get(
                "SEARCH_AGENT_FINAL_SELECTION_POLICY", "current"
            ),
        },
        "benchmark": benchmark,
        "aggregate": aggregate,
        "rows": rows,
        "wall_clock_sec": round(time.perf_counter() - started, 2),
        "notes": [
            "Public synthetic corpus; label provenance is recorded in benchmark metadata.",
            (
                "Single observed run; LLM outputs vary and this is not a "
                "statistical significance claim."
            ),
            "Bootstrap intervals describe aggregate uncertainty over this query set only.",
            (
                "Baseline and Agent share model, gateway, corpus, tool, and top-k; "
                "the controlled variable is the reflective loop."
            ),
        ],
    }


def render_markdown(payload: dict[str, Any]) -> str:
    agg = payload["aggregate"]
    lines = [
        "# Real LLM A/B Experiment",
        "",
        (
            "> Public synthetic corpus. Label provenance is recorded in benchmark "
            "metadata; this is an observed engineering run, not a company-data reproduction."
        ),
        "",
        f"- Model: `{payload['model']}`",
        f"- Index: `{payload['es_index']}`",
        f"- Corpus: `{payload['benchmark'].get('size', '?')}` candidates "
        f"({payload['benchmark'].get('label_kind', 'unknown labels')})",
        f"- Top-k: `{agg['top_k']}`",
        f"- Queries: `{agg['queries']}`",
        f"- Wall clock: `{payload['wall_clock_sec']}s`",
        "",
        (
            "| Query | Baseline pool recall | Agent pool recall | Baseline recall@k | "
            "Agent recall@k | New relevant | New candidates |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        lines.append(
            f"| `{row['id']}` | {row['baseline_metrics']['pool_recall']:.3f} | "
            f"{row['agent_metrics']['pool_recall']:.3f} | "
            f"{row['baseline_metrics']['recall']:.3f} | "
            f"{row['agent_metrics']['recall']:.3f} | {row['novel_relevant_count']} | "
            f"{row['novel_count']} |"
        )
    lines.extend(
        [
            "",
            "| Aggregate | Baseline | Agent |",
            "|---|---:|---:|",
            f"| Mean recall@{agg['top_k']} | {agg['baseline_mean_recall']:.3f} | "
            f"{agg['agent_mean_recall']:.3f} |",
            f"| Mean pool recall | {agg['baseline_mean_pool_recall']:.3f} | "
            f"{agg['agent_mean_pool_recall']:.3f} |",
            f"| Mean precision | {agg['baseline_mean_precision']:.3f} | "
            f"{agg['agent_mean_precision']:.3f} |",
            f"| LLM calls | {agg['baseline_llm_calls']} | {agg['agent_llm_calls']} |",
            f"| Tokens | {agg['baseline_tokens']} | {agg['agent_tokens']} |",
            f"| p50 latency | {agg['baseline_p50_ms']:.1f} ms | {agg['agent_p50_ms']:.1f} ms |",
            f"| p95 latency | {agg['baseline_p95_ms']:.1f} ms | {agg['agent_p95_ms']:.1f} ms |",
            "",
            f"Paired mean recall delta = **{agg['recall_delta']:+.3f}** "
            f"(bootstrap 95% CI {agg['recall_delta_ci95'][0]:+.3f} to "
            f"{agg['recall_delta_ci95'][1]:+.3f}); "
            f"pool-recall delta = **{agg['pool_recall_delta']:+.3f}** "
            f"(CI {agg['pool_recall_delta_ci95'][0]:+.3f} to "
            f"{agg['pool_recall_delta_ci95'][1]:+.3f}).",
            "",
            f"Cost multipliers: LLM calls ×{agg['llm_call_multiplier']:.1f}, "
            f"tokens ×{agg['token_multiplier']:.1f}, p50 latency ×{agg['latency_multiplier']:.1f}.",
            "",
            f"Agent found **{agg['total_novel_relevant']}** relevant candidates absent "
            f"from the baseline across {agg['queries']} queries; novel-candidate "
            f"precision = **{agg['novel_precision']:.3f}**.",
            "",
            "## Per-query Results",
            "",
        ]
    )
    for row in payload["rows"]:
        lines.extend(
            [
                f"### `{row['id']}`",
                "",
                f"Query: {row['query']}",
                "",
                f"- Relevant labels: `{', '.join(row['relevant'])}`",
                f"- Baseline delivered: `{', '.join(row['baseline']['doc_ids']) or '(none)'}`",
                f"- Agent delivered: `{', '.join(row['agent']['doc_ids']) or '(none)'}`",
                f"- Agent-only relevant: `{', '.join(row['novel_relevant_ids']) or '(none)'}`",
                f"- Agent degraded: `{row['agent']['degraded_reason'] or 'none'}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Interpretation Boundary",
            "",
            (
                "- The corpus and labels are public and synthetic; absolute values "
                "must not be compared with company metrics."
            ),
            (
                "- The run uses one model configuration and one observed sample per "
                "query; bootstrap CIs are descriptive, not significance tests."
            ),
            (
                "- The baseline is the same LLM-understanding plus one-shot Hybrid V2 "
                "path; the controlled variable is reflection/revise."
            ),
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", default="examples/real_ab_queries_100.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--markdown-out", default=None)
    parser.add_argument("--only", default=None, help="comma-separated query ids for diagnosis")
    parser.add_argument("--limit", type=int, default=0, help="run only the first N queries")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="parallel query workers (each owns isolated LLM/tool clients)",
    )
    args = parser.parse_args()

    if not os.environ.get("SEARCH_AGENT_API_KEY"):
        raise SystemExit("SEARCH_AGENT_API_KEY is required for the real experiment")
    if not os.environ.get("SEARCH_AGENT_ES_URL"):
        raise SystemExit("SEARCH_AGENT_ES_URL is required for the real experiment")

    benchmark = _load_benchmark(Path(args.benchmark))
    if args.only:
        selected = {item.strip() for item in args.only.split(",") if item.strip()}
        benchmark["queries"] = [row for row in benchmark["queries"] if row["id"] in selected]
        missing = selected - {row["id"] for row in benchmark["queries"]}
        if missing:
            raise SystemExit(f"unknown query ids: {', '.join(sorted(missing))}")
    if args.limit > 0:
        benchmark["queries"] = benchmark["queries"][: args.limit]
    if not benchmark["queries"]:
        raise SystemExit("no benchmark queries selected")
    payload = run_benchmark(benchmark, top_k=args.top_k, workers=args.workers)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.json_out:
        Path(args.json_out).write_text(text, encoding="utf-8")
    if args.markdown_out:
        Path(args.markdown_out).write_text(render_markdown(payload), encoding="utf-8")
    agg = payload["aggregate"]
    print(
        f"mean recall@{agg['top_k']}: baseline={agg['baseline_mean_recall']:.3f} "
        f"agent={agg['agent_mean_recall']:.3f}; pool recall="
        f"{agg['baseline_mean_pool_recall']:.3f}->{agg['agent_mean_pool_recall']:.3f}; "
        f"novel relevant={agg['total_novel_relevant']}; "
        f"LLM calls={agg['baseline_llm_calls']}->{agg['agent_llm_calls']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
