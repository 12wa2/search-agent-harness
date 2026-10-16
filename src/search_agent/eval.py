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
import json
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable, TextIO

from search_agent.config import budget_from_env, recall_pool_size_from_env
from search_agent.contract.tool import SearchTool
from search_agent.core.ablation import ABLATION_ROWS, BASELINE_ROW, REFERENCE_ROW, AblationRow
from search_agent.core.budget import BudgetState
from search_agent.core.metrics import collect_metrics
from search_agent.corpus import QuerySpec, SyntheticCorpus, generate_corpus, load_corpus
from search_agent.demo import DEMO_DATA, DEMO_QUERIES, ScriptedLlmClient
from search_agent.harness import Harness, single_path_from_hard_constraints
from search_agent.llm.client import LlmClient, MeteredLlmClient
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

#: ``qualified`` counts the judged pool, not ground-truth relevance -- the caveat
#: below every table says so, and the oracle / ablation reports carry quality.

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
    #: Tokens spent this run (exact when the provider reports usage).
    tokens: int = 0
    tokens_estimated: bool = False


@dataclass(frozen=True)
class _Totals:
    count: int
    baseline_qualified: int
    agent_qualified: int
    baseline_rate: float
    agent_rate: float
    baseline_llm: int
    agent_llm: int
    baseline_tokens: int
    agent_tokens: int
    tokens_estimated: bool
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
    deps = Deps(llm=MeteredLlmClient(llm, bs), tool=tool, budget_state=bs)
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
        tokens=bs.tokens_used,
        tokens_estimated=bs.tokens_estimated,
    )


def run_ablation(
    corpus: SyntheticCorpus,
    *,
    top_k: int = 10,
    markdown: bool = False,
    stream: TextIO | None = None,
    save_baseline: str | None = None,
    baseline_path: str | None = None,
    target_qualified: int | None = None,
) -> int:
    """Run the whole grid on one corpus: one switch flipped per row.

    Reports oracle recall / precision / semantic visibility per configuration
    plus the discovery delta against the single-pass row, and can gate a stored
    baseline (``--baseline``): any quality drop beyond tolerance exits non-zero.
    """
    out = stream or sys.stdout
    # Force the loop to iterate: an unmet target is what makes reflect / revise /
    # converge observable at all. Callers can override it explicitly.
    target = target_qualified if target_qualified is not None else corpus.size
    tool = InMemorySearchTool(corpus.records())
    results: dict[str, list[PipelineResult]] = {}
    for row in ABLATION_ROWS:
        results[row.name] = [
            run_ablation_row(
                row,
                ScriptedLlmClient(),
                tool,
                spec.text,
                top_k=top_k,
                target_qualified=target,
            )
            for spec in corpus.queries
        ]

    baseline_surfaced = [r.surfaced_ids for r in results[BASELINE_ROW]]
    scores = [
        _score_row(row, results[row.name], corpus, baseline_surfaced) for row in ABLATION_ROWS
    ]
    _print_ablation(scores, corpus, markdown=markdown, stream=out, target_qualified=target)

    payload = baseline_payload(scores, corpus=corpus, top_k=top_k, target_qualified=target)
    if save_baseline:
        Path(save_baseline).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n基线已写入 {save_baseline}", file=out)
    if baseline_path:
        ok, messages = compare_baseline(payload, baseline_path)
        if ok:
            print(f"\n门禁：与 {baseline_path} 对比，所有指标在容差内 ✅", file=out)
            return 0
        print(f"\n门禁：相对 {baseline_path} 出现回归 ❌", file=out)
        for message in messages:
            print(f"  {message}", file=out)
        return 1
    return 0


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
        tokens=int(meta.get("totalTokens") or 0),
        tokens_estimated=bool(meta.get("tokensEstimated")),
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
        baseline_tokens=sum(r[1].tokens for r in rows),
        agent_tokens=sum(r[2].tokens for r in rows),
        tokens_estimated=any(r[1].tokens_estimated or r[2].tokens_estimated for r in rows),
        baseline_ms=sum(r[1].elapsed_ms for r in rows),
        agent_ms=sum(r[2].elapsed_ms for r in rows),
    )


def _fmt_tokens(tokens: int, estimated: bool) -> str:
    return f"~{tokens}" if estimated else str(tokens)


def _summary(result: PipelineResult) -> str:
    tokens = _fmt_tokens(result.tokens, result.tokens_estimated)
    return (
        f"判定 {result.qualified}/{result.target} · "
        f"召回完成度 {result.recall_rate:.2f} · "
        f"LLM {result.llm_calls} 次 · "
        f"tokens {tokens} · "
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
            f"  对比         : 判定池 {agent.qualified - baseline.qualified:+d} 人 · "
            f"LLM 调用 {agent.llm_calls - baseline.llm_calls:+d} 次 · "
            f"tokens {agent.tokens - baseline.tokens:+d} · "
            f"耗时 {agent.elapsed_ms - baseline.elapsed_ms:+.1f} ms",
            file=stream,
        )
        print("", file=stream)

    t = _totals(rows)
    gain = t.agent_qualified - t.baseline_qualified
    pct = (gain / t.baseline_qualified * 100) if t.baseline_qualified else 0.0
    llm_x = (t.agent_llm / t.baseline_llm) if t.baseline_llm else 0.0
    tokens_x = (t.agent_tokens / t.baseline_tokens) if t.baseline_tokens else 0.0
    ms_x = (t.agent_ms / t.baseline_ms) if t.baseline_ms else 0.0
    baseline_tokens = _fmt_tokens(t.baseline_tokens, t.tokens_estimated)
    agent_tokens = _fmt_tokens(t.agent_tokens, t.tokens_estimated)
    print("=" * 72, file=stream)
    print(f"合计（{t.count} 个查询）", file=stream)
    print(
        f"  单趟基线     : 判定 {t.baseline_qualified} 人 · "
        f"平均完成度 {t.baseline_rate:.2f} · LLM {t.baseline_llm} 次 · "
        f"tokens {baseline_tokens} · 累计 {t.baseline_ms:.1f} ms",
        file=stream,
    )
    print(
        f"  Agent 全循环 : 判定 {t.agent_qualified} 人 · "
        f"平均完成度 {t.agent_rate:.2f} · LLM {t.agent_llm} 次 · "
        f"tokens {agent_tokens} · 累计 {t.agent_ms:.1f} ms",
        file=stream,
    )
    print(
        f"  判定池变化 {gain:+d}（{pct:+.0f}%） · "
        f"成本：LLM 调用 ×{llm_x:.1f} · tokens ×{tokens_x:.1f} · 耗时 ×{ms_x:.1f}",
        file=stream,
    )
    print(
        "  注：判定候选 = 进入 judge 的池子规模，不是质量口径；"
        "列表质量看 eval --generated（oracle）与 eval --ablate（消融）。",
        file=stream,
    )


def _print_markdown(rows: list[tuple[str, PipelineResult, PipelineResult]], stream: TextIO) -> None:
    print("### 单趟基线 vs Agent 全循环（合成数据 · 离线确定性）\n", file=stream)
    print(
        "| 查询 | 管线 | 判定/目标 | 召回完成度 | LLM 调用 | tokens | 耗时 | 最终结果 |",
        file=stream,
    )
    print("|---|---|---|---|---|---|---|---|", file=stream)
    for index, (_, baseline, agent) in enumerate(rows, 1):
        for result in (baseline, agent):
            print(
                f"| Q{index} | {_LABELS[result.name]} | {result.qualified}/{result.target} | "
                f"{result.recall_rate:.2f} | {result.llm_calls} | "
                f"{_fmt_tokens(result.tokens, result.tokens_estimated)} | "
                f"{result.elapsed_ms:.1f} ms | {result.final_count} 人 |",
                file=stream,
            )

    t = _totals(rows)
    gain = t.agent_qualified - t.baseline_qualified
    pct = (gain / t.baseline_qualified * 100) if t.baseline_qualified else 0.0
    agent_tokens = _fmt_tokens(t.agent_tokens, t.tokens_estimated)
    print(f"\n### 合计（{t.count} 个查询）\n", file=stream)
    print(
        "| 管线 | 判定候选（judged） | 平均召回完成度 | LLM 调用 | tokens | 累计耗时 |",
        file=stream,
    )
    print("|---|---|---|---|---|---|", file=stream)
    print(
        f"| 单趟基线 | {t.baseline_qualified} | {t.baseline_rate:.2f} | "
        f"{t.baseline_llm} | {_fmt_tokens(t.baseline_tokens, t.tokens_estimated)} | "
        f"{t.baseline_ms:.1f} ms |",
        file=stream,
    )
    print(
        f"| Agent 全循环 | {t.agent_qualified} | {t.agent_rate:.2f} | "
        f"{t.agent_llm} | {agent_tokens} | "
        f"{t.agent_ms:.1f} ms |",
        file=stream,
    )
    print(f"\n判定候选变化：{gain:+d}（{pct:+.0f}%）\n", file=stream)
    print(
        "注：「判定候选」= 进入 judge 的候选人池规模（全循环的语义路径会把池子铺得更宽）；"
        "它不是质量口径——\n交付列表的真实质量看 `eval --generated` 的 oracle 指标与 "
        "`eval --ablate` 消融表。\n",
        file=stream,
    )
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
    ablate: bool = False,
    save_baseline: str | None = None,
    baseline_path: str | None = None,
    ablate_target: int | None = None,
) -> int:
    """Run both pipelines over ``queries`` and print the comparison report.

    Pass ``corpus`` (see :mod:`search_agent.corpus`) to run the generated benchmark:
    queries come from the corpus and the report adds oracle metrics -- ground truth
    from the generator instead of the agent grading itself. ``ablate=True`` switches
    to the ablation grid (see :mod:`search_agent.core.ablation`).
    """
    out = stream or sys.stdout
    if ablate:
        return run_ablation(
            corpus if corpus is not None else generate_corpus(size=120, seed=7),
            top_k=top_k,
            markdown=markdown,
            stream=out,
            save_baseline=save_baseline,
            baseline_path=baseline_path,
            target_qualified=ablate_target,
        )
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


def run_ablation_row(
    row: AblationRow,
    llm: LlmClient,
    tool: SearchTool,
    query: str,
    *,
    top_k: int = 10,
    target_qualified: int | None = None,
) -> PipelineResult:
    """Run one grid row end to end: same stages, exactly one switch flipped.

    ``target_qualified`` overrides the stop threshold: the ablation grid raises it
    so the loop actually reflects (otherwise a first round that already meets the
    target would make the reflect / revise / converge switches indistinguishable).
    """
    if row.single_pass:
        return replace(run_single_pass(llm, tool, query, top_k=top_k), name=row.name)

    budget = budget_from_env(top_k)
    if target_qualified is not None:
        budget = budget.model_copy(update={"target_qualified": target_qualified})
    if row.max_paths_per_iteration is not None or row.max_iterations is not None:
        budget = budget.model_copy(
            update={
                k: v
                for k, v in (
                    ("max_paths_per_iteration", row.max_paths_per_iteration),
                    ("max_iterations", row.max_iterations),
                )
                if v is not None
            }
        )
    bs = BudgetState(budget=budget)
    deps = Deps(
        llm=MeteredLlmClient(llm, bs),
        tool=tool,
        budget_state=bs,
        ablation=row.switches,
    )
    state = _initial_state(query, top_k, budget)
    if row.skip_reflection:
        state["skip_reflection"] = True

    started = time.perf_counter()
    state = Harness(deps).run(state)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    metrics = collect_metrics(state, bs)
    return PipelineResult(
        name=row.name,
        qualified=metrics.judged_count,
        target=metrics.target_qualified,
        recall_rate=metrics.recall_rate,
        llm_calls=metrics.total_llm_calls,
        elapsed_ms=elapsed_ms,
        final_count=state.get("final_total", 0),
        degraded_reason=metrics.degraded_reason,
        doc_ids=tuple(tc.doc_id for tc in state.get("final_items") or []),
        surfaced_ids=_surfaced_ids(state),
        tokens=bs.tokens_used,
        tokens_estimated=bs.tokens_estimated,
    )


@dataclass(frozen=True)
class AblationScore:
    """Aggregated oracle + cost metrics for one grid row (micro over queries)."""

    name: str
    label: str
    recall: float
    precision: float
    keyword_recall: float
    semantic_recall: float
    unique: int
    unique_relevant: int
    llm_calls: int
    tokens: int
    tokens_estimated: bool
    elapsed_ms: float
    degraded: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "recall": round(self.recall, 4),
            "precision": round(self.precision, 4),
            "keyword_recall": round(self.keyword_recall, 4),
            "semantic_recall": round(self.semantic_recall, 4),
            "unique": self.unique,
            "unique_relevant": self.unique_relevant,
            "llm_calls": self.llm_calls,
            "tokens": self.tokens,
            "elapsed_ms": self.elapsed_ms,
            "degraded": self.degraded,
        }


def _score_row(
    row: AblationRow,
    results: list[PipelineResult],
    corpus: SyntheticCorpus,
    baseline_surfaced: list[tuple[str, ...]],
) -> AblationScore:
    found = delivered = relevant = 0
    keyword_found = keyword_relevant = 0
    semantic_found = semantic_relevant = 0
    unique = unique_relevant = 0
    per_query = zip(corpus.queries, results, baseline_surfaced, strict=True)
    for spec, result, base_ids in per_query:
        score = score_against_oracle(corpus, spec, result.doc_ids)
        found += score.found
        delivered += score.delivered
        relevant += score.relevant
        keyword_found += score.keyword_found
        keyword_relevant += score.keyword_relevant
        semantic_found += score.semantic_found
        semantic_relevant += score.semantic_relevant
        fresh = novelty(corpus, spec, base_ids, result.surfaced_ids)
        unique += fresh.unique
        unique_relevant += fresh.relevant
    return AblationScore(
        name=row.name,
        label=row.label,
        recall=found / relevant if relevant else 0.0,
        precision=found / delivered if delivered else 0.0,
        keyword_recall=keyword_found / keyword_relevant if keyword_relevant else 0.0,
        semantic_recall=semantic_found / semantic_relevant if semantic_relevant else 0.0,
        unique=unique,
        unique_relevant=unique_relevant,
        llm_calls=sum(r.llm_calls for r in results),
        tokens=sum(r.tokens for r in results),
        tokens_estimated=any(r.tokens_estimated for r in results),
        elapsed_ms=sum(r.elapsed_ms for r in results),
        degraded=sum(1 for r in results if r.degraded_reason),
    )


_RATIO_TOLERANCE = 0.02  # absolute drop allowed on recall / precision style ratios
_COUNT_TOLERANCE = 1  # absolute drop allowed on discovery counts
_COST_TOLERANCE = 0.10  # relative rise allowed on llm calls / tokens


def baseline_payload(
    scores: list[AblationScore],
    *,
    corpus: SyntheticCorpus,
    top_k: int,
    target_qualified: int,
) -> dict[str, Any]:
    """Serializable snapshot of one ablation run (what the CI gate compares)."""
    return {
        "version": 1,
        "size": corpus.size,
        "seed": corpus.seed,
        "top_k": top_k,
        "target_qualified": target_qualified,
        "rows": {score.name: score.to_dict() for score in scores},
    }


def compare_baseline(
    payload: dict[str, Any],
    baseline_path: str,
) -> tuple[bool, list[str]]:
    """Compare a fresh ablation run against a stored baseline.

    Returns ``(ok, messages)``; ``ok=False`` means a quality metric regressed
    beyond tolerance, a row disappeared, or a cost metric blew up. Timing is
    never gated (machine-dependent).
    """
    stored = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
    messages: list[str] = []
    ok = True
    if stored.get("size") != payload["size"] or stored.get("seed") != payload["seed"]:
        messages.append(
            f"! 语料不一致：基线 size={stored.get('size')} seed={stored.get('seed')} · "
            f"本次 size={payload['size']} seed={payload['seed']}"
        )
        ok = False
    if stored.get("target_qualified") != payload["target_qualified"]:
        messages.append(
            f"! 终止阈值不一致：基线 target={stored.get('target_qualified')} · "
            f"本次 target={payload['target_qualified']}"
        )
        ok = False
    for name, current in payload["rows"].items():
        base = (stored.get("rows") or {}).get(name)
        if base is None:
            messages.append(f"! {name}: 基线里没有这一行（基线过期？）")
            ok = False
            continue
        for key in ("recall", "precision", "keyword_recall", "semantic_recall"):
            drop = float(base.get(key, 0.0)) - float(current[key])
            if drop > _RATIO_TOLERANCE:
                messages.append(
                    f"! {name}.{key}: {base[key]:.3f} → {current[key]:.3f}（-{drop:.3f}）"
                )
                ok = False
        for key in ("unique_relevant",):
            drop = int(base.get(key, 0)) - int(current[key])
            if drop > _COUNT_TOLERANCE:
                messages.append(f"! {name}.{key}: {base[key]} → {current[key]}（-{drop}）")
                ok = False
        for key in ("llm_calls", "tokens"):
            limit = float(base.get(key, 0)) * (1 + _COST_TOLERANCE)
            if base.get(key) and float(current[key]) > limit:
                messages.append(
                    f"! {name}.{key}: 成本上升 {base[key]} → {current[key]}"
                    f"（> {(1 + _COST_TOLERANCE):.0%} 容差）"
                )
                ok = False
        if current["degraded"] > int(base.get("degraded", 0)):
            messages.append(
                f"! {name}.degraded: {base.get('degraded', 0)} → {current['degraded']} 次降级"
            )
            ok = False
    return ok, messages


def _print_ablation(
    scores: list[AblationScore],
    corpus: SyntheticCorpus,
    *,
    markdown: bool,
    stream: TextIO,
    target_qualified: int,
) -> None:
    reference = next((s for s in scores if s.name == REFERENCE_ROW), None)
    if markdown:
        print(
            "\n| 配置 | 召回 | 精确率 | 关键词可见 | 仅语义可见 | 仅该行发现（合格） "
            "| LLM 调用 | tokens | 耗时 |",
            file=stream,
        )
        print("|---|---|---|---|---|---|---|---|---|", file=stream)
        for score in scores:
            delta = ""
            if reference is not None and score.name != REFERENCE_ROW:
                delta = f"（{score.recall - reference.recall:+.2f}）"
            tokens = f"~{score.tokens}" if score.tokens_estimated else str(score.tokens)
            found = f"{score.unique} 人（{score.unique_relevant} 合格）" if score.unique else "—"
            print(
                f"| {score.label} `{score.name}` | {score.recall:.2f}{delta} "
                f"| {score.precision:.2f} | "
                f"{score.keyword_recall:.2f} | {score.semantic_recall:.2f} | {found} | "
                f"{score.llm_calls} | {tokens} | {score.elapsed_ms:.0f} ms |",
                file=stream,
            )
        print(
            f"\n语料 {corpus.size} 人 · {len(corpus.queries)} 个查询 · seed={corpus.seed} · "
            f"目标合格数 {target_qualified}（强制循环迭代）· "
            f"「仅该行发现」= 该配置看到、单趟基线没看到的候选人（真值来自生成器）。",
            file=stream,
        )
        return

    print("=" * 72, file=stream)
    print("消融实验：同一语料 / seed，每行只翻转一个结构开关", file=stream)
    print(
        f"语料 {corpus.size} 人 · {len(corpus.queries)} 个查询 · seed={corpus.seed} · "
        f"目标合格数 {target_qualified}（强制循环迭代）· 「仅该行发现」相对单趟基线计算",
        file=stream,
    )
    print("-" * 72, file=stream)
    for score in scores:
        delta = ""
        if reference is not None and score.name != REFERENCE_ROW:
            delta = f"（Δ{score.recall - reference.recall:+.2f}）"
        tokens = f"~{score.tokens}" if score.tokens_estimated else str(score.tokens)
        found = f"独有 {score.unique}/{score.unique_relevant} 合格" if score.unique else "独有 —"
        print(
            f"  {score.label:<22} {score.name:<20} 召回 {score.recall:.2f}{delta} · "
            f"精确率 {score.precision:.2f} · 关键词 {score.keyword_recall:.2f} · "
            f"仅语义 {score.semantic_recall:.2f} · {found} · LLM {score.llm_calls} · "
            f"tokens {tokens} · {score.elapsed_ms:.0f} ms",
            file=stream,
        )
    print("-" * 72, file=stream)
    print("  对照行：完整循环 = 基准；其余行各自只关掉一个机制。", file=stream)


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
    parser.add_argument(
        "--ablate",
        action="store_true",
        help="run the ablation grid: one structural switch flipped per row",
    )
    parser.add_argument(
        "--save-baseline",
        default=None,
        help="write this run's ablation metrics to a JSON file (CI gate baseline)",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="compare against a stored baseline JSON; exit 1 on regression",
    )
    parser.add_argument(
        "--ablate-target",
        type=int,
        default=None,
        help="override target_qualified for --ablate (default: corpus size, forcing iteration)",
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
        ablate=args.ablate,
        save_baseline=args.save_baseline,
        baseline_path=args.baseline,
        ablate_target=args.ablate_target,
    )


if __name__ == "__main__":
    raise SystemExit(main())
