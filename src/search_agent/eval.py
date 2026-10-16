"""Offline evaluation: full reflective loop vs single-pass baseline.

Runs the same queries through two pipelines on the bundled synthetic dataset
(16 fictional candidates + deterministic scripted LLM, no API key required):

- **single-pass baseline**: understand -> one hard-constraint path -> judge ->
  merge + global rerank. This reuses the harness's own single-path constructor
  (the ``single_pass`` difficulty route / plan-degradation path), i.e. a
  traditional one-shot search enriched with LLM understanding.
- **agent**: the full eight-stage loop (multi-path planning, reflection,
  revision, converged final search) via the public service entry point.

Both pipelines share the same LLM and the same search backend, so the deltas
come from the reasoning loop itself. The report includes qualified candidates,
recall completeness, LLM calls and wall-clock time.

Usage::

    search-agent eval                  # printable report
    search-agent eval --markdown       # markdown tables (for README)
    search-agent eval --generated 120  # oracle-labeled corpus, no self-grading
    python -m search_agent.eval --markdown
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterable, TextIO

from search_agent.config import budget_from_env, recall_pool_size_from_env
from search_agent.contract.tool import SearchTool
from search_agent.core.budget import BudgetState
from search_agent.core.metrics import collect_metrics
from search_agent.corpus import QuerySpec, SyntheticCorpus, generate_corpus, load_corpus
from search_agent.demo import DEMO_DATA, DEMO_QUERIES, ScriptedLlmClient
from search_agent.harness import single_path_from_hard_constraints
from search_agent.llm.client import LlmClient
from search_agent.models.api import SearchRequest
from search_agent.models.artifacts import Budget
from search_agent.oracle import (
    Novelty,
    OracleScore,
    novelty,
    print_oracle_report,
    score_against_oracle,
)
from search_agent.service import SearchAgentService
from search_agent.stages.base import Deps
from search_agent.stages.execute import run_execute
from search_agent.stages.finalize import run_finalize
from search_agent.stages.judge import run_judge
from search_agent.stages.understand import run_understand
from search_agent.tools.memory import InMemorySearchTool

_LABELS = {"single_pass": "单趟基线", "agent": "Agent 全循环"}

_PREAMBLE = """\
评测：单趟基线 vs Agent 全循环（合成数据 · 脚本化 LLM · 离线确定性运行）
  单趟基线     = understand → 单条硬条件路径 → judge → 合并 + 全局重排
  Agent 全循环 = 八阶段反思循环（多路径规划 / 反思修订 / 收敛再搜）"""


@dataclass(frozen=True)
class PipelineResult:
    """One pipeline run's headline numbers."""

    name: str
    qualified: int
    target: int
    recall_rate: float
    llm_calls: int
    elapsed_ms: float
    final_count: int
    degraded_reason: str | None = None
    #: Doc ids actually delivered; used for oracle scoring (empty if unavailable).
    doc_ids: tuple[str, ...] = ()
    #: Doc ids any path surfaced this session; drives discovery-level novelty.
    surfaced_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Totals:
    count: int
    baseline_qualified: int
    agent_qualified: int
    baseline_rate: float
    agent_rate: float
    baseline_llm: int
    agent_llm: int
    baseline_ms: float
    agent_ms: float


def _initial_state(query: str, top_k: int, budget: Budget) -> dict[str, Any]:
    """Minimal request context both pipelines start from (mirrors the service)."""
    return {
        "query": query,
        "company_id": 1,
        "hr_id": None,
        "permission": None,
        "routing": 1,
        "top_k": top_k,
        "recall_pool_size": recall_pool_size_from_env(top_k),
        "details": False,
        "conversation_history": [],
        "budget": budget,
    }


def _surfaced_ids(state: dict[str, Any]) -> tuple[str, ...]:
    """Doc ids any path result returned so far (deduped, first-seen order)."""
    seen: set[str] = set()
    ids: list[str] = []
    for pr in state.get("path_results") or []:
        for candidate in pr.tool_result.items:
            if candidate.doc_id not in seen:
                seen.add(candidate.doc_id)
                ids.append(candidate.doc_id)
    for candidate in state.get("final_items") or []:
        if candidate.doc_id not in seen:
            seen.add(candidate.doc_id)
            ids.append(candidate.doc_id)
    return tuple(ids)


def run_single_pass(
    llm: LlmClient,
    tool: SearchTool,
    query: str,
    *,
    top_k: int = 10,
) -> PipelineResult:
    """One-shot baseline: understand, search once, judge, merge + rerank."""
    budget = budget_from_env(top_k)
    bs = BudgetState(budget=budget)
    deps = Deps(llm=llm, tool=tool, budget_state=bs)
    state = _initial_state(query, top_k, budget)
    started = time.perf_counter()

    state.update(run_understand(state, deps))
    path = single_path_from_hard_constraints(state, loose_fallback=True)
    if path is None:  # pragma: no cover - loose_fallback always yields a path
        raise ValueError("single-pass baseline could not build a path")
    state["paths"] = [path]
    state["tried_signatures"] = {path.signature}
    state["phase"] = "execute"
    state.update(run_execute(state, deps))
    state.update(run_judge(state, deps))
    state.update(run_finalize(state, deps))

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    metrics = collect_metrics(state, bs)
    return PipelineResult(
        name="single_pass",
        qualified=metrics.judged_count,
        target=metrics.target_qualified,
        recall_rate=metrics.recall_rate,
        llm_calls=metrics.total_llm_calls,
        elapsed_ms=elapsed_ms,
        final_count=state.get("final_total", 0),
        degraded_reason=metrics.degraded_reason,
        doc_ids=tuple(tc.doc_id for tc in state.get("final_items") or []),
        surfaced_ids=_surfaced_ids(state),
    )


def run_agent(
    llm: LlmClient,
    tool: SearchTool,
    query: str,
    *,
    top_k: int = 10,
) -> PipelineResult:
    """Full eight-stage loop through the public service entry point."""
    service = SearchAgentService(tool=tool, llm_client=llm)
    started = time.perf_counter()
    resp = service.search(SearchRequest(query=query, topK=top_k, details=True))
    metrics = (resp.debug or {}).get("agentMetrics") or {}
    recall = metrics.get("recall") or {}
    meta = metrics.get("meta") or {}
    return PipelineResult(
        name="agent",
        qualified=int(recall.get("judged") or 0),
        target=int(recall.get("target") or 0),
        recall_rate=float(recall.get("rate") or 0.0),
        llm_calls=int(meta.get("llmCalls") or 0),
        elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
        final_count=resp.total,
        degraded_reason=meta.get("degradedReason"),
        doc_ids=tuple(item.docId for item in resp.items),
        surfaced_ids=tuple(recall.get("surfacedDocIds") or ()),
    )


def _totals(rows: list[tuple[str, PipelineResult, PipelineResult]]) -> _Totals:
    count = len(rows)
    return _Totals(
        count=count,
        baseline_qualified=sum(r[1].qualified for r in rows),
        agent_qualified=sum(r[2].qualified for r in rows),
        baseline_rate=sum(r[1].recall_rate for r in rows) / count,
        agent_rate=sum(r[2].recall_rate for r in rows) / count,
        baseline_llm=sum(r[1].llm_calls for r in rows),
        agent_llm=sum(r[2].llm_calls for r in rows),
        baseline_ms=sum(r[1].elapsed_ms for r in rows),
        agent_ms=sum(r[2].elapsed_ms for r in rows),
    )


def _summary(result: PipelineResult) -> str:
    return (
        f"合格 {result.qualified}/{result.target} · "
        f"召回完成度 {result.recall_rate:.2f} · "
        f"LLM {result.llm_calls} 次 · "
        f"{result.elapsed_ms:.1f} ms · "
        f"最终 {result.final_count} 人 · "
        f"降级={result.degraded_reason or '无'}"
    )


def _print_report(
    rows: list[tuple[str, PipelineResult, PipelineResult]],
    *,
    markdown: bool,
    stream: TextIO,
) -> None:
    if markdown:
        _print_markdown(rows, stream)
        return

    print(_PREAMBLE, file=stream)
    for index, (query, baseline, agent) in enumerate(rows, 1):
        print("=" * 72, file=stream)
        print(f"查询 {index}: {query}", file=stream)
        print("=" * 72, file=stream)
        print(f"  单趟基线     : {_summary(baseline)}", file=stream)
        print(f"  Agent 全循环 : {_summary(agent)}", file=stream)
        print(
            f"  对比         : 合格 {agent.qualified - baseline.qualified:+d} 人 · "
            f"LLM 调用 {agent.llm_calls - baseline.llm_calls:+d} 次 · "
            f"耗时 {agent.elapsed_ms - baseline.elapsed_ms:+.1f} ms",
            file=stream,
        )
        print("", file=stream)

    t = _totals(rows)
    gain = t.agent_qualified - t.baseline_qualified
    pct = (gain / t.baseline_qualified * 100) if t.baseline_qualified else 0.0
    llm_x = (t.agent_llm / t.baseline_llm) if t.baseline_llm else 0.0
    ms_x = (t.agent_ms / t.baseline_ms) if t.baseline_ms else 0.0
    print("=" * 72, file=stream)
    print(f"合计（{t.count} 个查询）", file=stream)
    print(
        f"  单趟基线     : 合格 {t.baseline_qualified} 人 · "
        f"平均完成度 {t.baseline_rate:.2f} · LLM {t.baseline_llm} 次 · "
        f"累计 {t.baseline_ms:.1f} ms",
        file=stream,
    )
    print(
        f"  Agent 全循环 : 合格 {t.agent_qualified} 人 · "
        f"平均完成度 {t.agent_rate:.2f} · LLM {t.agent_llm} 次 · "
        f"累计 {t.agent_ms:.1f} ms",
        file=stream,
    )
    print(
        f"  提升：合格候选人 {gain:+d}（{pct:+.0f}%） · "
        f"成本：LLM 调用 ×{llm_x:.1f} · 耗时 ×{ms_x:.1f}",
        file=stream,
    )


def _print_markdown(rows: list[tuple[str, PipelineResult, PipelineResult]], stream: TextIO) -> None:
    print("### 单趟基线 vs Agent 全循环（合成数据 · 离线确定性）\n", file=stream)
    print("| 查询 | 管线 | 合格/目标 | 召回完成度 | LLM 调用 | 耗时 | 最终结果 |", file=stream)
    print("|---|---|---|---|---|---|---|", file=stream)
    for index, (_, baseline, agent) in enumerate(rows, 1):
        for result in (baseline, agent):
            print(
                f"| Q{index} | {_LABELS[result.name]} | {result.qualified}/{result.target} | "
                f"{result.recall_rate:.2f} | {result.llm_calls} | {result.elapsed_ms:.1f} ms | "
                f"{result.final_count} 人 |",
                file=stream,
            )

    t = _totals(rows)
    gain = t.agent_qualified - t.baseline_qualified
    pct = (gain / t.baseline_qualified * 100) if t.baseline_qualified else 0.0
    print(f"\n### 合计（{t.count} 个查询）\n", file=stream)
    print("| 管线 | 合格候选人 | 平均召回完成度 | LLM 调用 | 累计耗时 |", file=stream)
    print("|---|---|---|---|---|", file=stream)
    print(
        f"| 单趟基线 | {t.baseline_qualified} | {t.baseline_rate:.2f} | "
        f"{t.baseline_llm} | {t.baseline_ms:.1f} ms |",
        file=stream,
    )
    print(
        f"| Agent 全循环 | {t.agent_qualified} | {t.agent_rate:.2f} | "
        f"{t.agent_llm} | {t.agent_ms:.1f} ms |",
        file=stream,
    )
    print(f"\n合格候选人变化：{gain:+d}（{pct:+.0f}%）\n", file=stream)
    print("查询列表：", file=stream)
    for index, (query, _, _) in enumerate(rows, 1):
        print(f"- Q{index}：{query}", file=stream)
    print("\n重新生成：`search-agent eval --markdown`", file=stream)


def _run_oracle_eval(
    corpus: SyntheticCorpus,
    *,
    top_k: int,
    markdown: bool,
    stream: TextIO,
) -> int:
    """Corpus-driven run: relevance comes from the generator, not the judge."""
    tool = InMemorySearchTool(corpus.records())
    rows: list[tuple[QuerySpec, OracleScore, OracleScore, Novelty]] = []
    for spec in corpus.queries:
        baseline = run_single_pass(ScriptedLlmClient(), tool, spec.text, top_k=top_k)
        agent = run_agent(ScriptedLlmClient(), tool, spec.text, top_k=top_k)
        rows.append(
            (
                spec,
                score_against_oracle(corpus, spec, baseline.doc_ids),
                score_against_oracle(corpus, spec, agent.doc_ids),
                novelty(corpus, spec, baseline.surfaced_ids, agent.surfaced_ids),
            )
        )
    print_oracle_report(corpus, rows, markdown=markdown, stream=stream)
    return 0


def run_eval(
    queries: Iterable[str] | None = None,
    *,
    data_path: str | None = None,
    top_k: int = 10,
    markdown: bool = False,
    stream: TextIO | None = None,
    corpus: SyntheticCorpus | None = None,
) -> int:
    """Run both pipelines over ``queries`` and print the comparison report.

    Pass ``corpus`` (see :mod:`search_agent.corpus`) to run the generated benchmark:
    queries come from the corpus and the report adds oracle metrics -- ground truth
    from the generator instead of the agent grading itself.
    """
    out = stream or sys.stdout
    if corpus is not None:
        return _run_oracle_eval(corpus, top_k=top_k, markdown=markdown, stream=out)

    tool = InMemorySearchTool(data_path or DEMO_DATA)
    rows: list[tuple[str, PipelineResult, PipelineResult]] = []
    for query in list(queries or DEMO_QUERIES):
        baseline = run_single_pass(ScriptedLlmClient(), tool, query, top_k=top_k)
        agent = run_agent(ScriptedLlmClient(), tool, query, top_k=top_k)
        rows.append((query, baseline, agent))
    _print_report(rows, markdown=markdown, stream=out)
    return 0


def main(argv: list[str] | None = None, *, stream: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="search-agent eval",
        description="Offline evaluation: full reflective loop vs single-pass baseline.",
    )
    parser.add_argument("query", nargs="*", help="optional custom queries")
    parser.add_argument("--data", default=None, help="candidate JSONL path")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--markdown", action="store_true", help="markdown tables (for README)")
    parser.add_argument(
        "--generated",
        type=int,
        default=0,
        help="generate N synthetic candidates and run the oracle benchmark",
    )
    parser.add_argument("--seed", type=int, default=7, help="seed for --generated")
    parser.add_argument(
        "--oracle", default=None, help="corpus directory written by `search-agent corpus`"
    )
    args = parser.parse_args(argv)
    corpus = None
    if args.generated:
        corpus = generate_corpus(size=args.generated, seed=args.seed)
    elif args.oracle:
        corpus = load_corpus(args.oracle)
    return run_eval(
        args.query or None,
        data_path=args.data,
        top_k=args.top_k,
        markdown=args.markdown,
        stream=stream,
        corpus=corpus,
    )


if __name__ == "__main__":
    raise SystemExit(main())
