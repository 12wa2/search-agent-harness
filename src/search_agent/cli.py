"""Command-line interface: offline demo + real search.

Examples::

    search-agent demo                       # zero-config offline showcase
    search-agent search "高并发 后端" --offline
    search-agent search "高并发 后端"        # needs SEARCH_AGENT_API_KEY
    search-agent eval --markdown            # single-pass baseline comparison
"""

from __future__ import annotations

import argparse
import sys
import time
import unicodedata
from typing import Any, Callable, TextIO

from search_agent import __version__
from search_agent.demo import DEMO_DATA, DEMO_QUERIES, ScriptedLlmClient, run_demo
from search_agent.eval import run_eval
from search_agent.llm.client import OpenAILlmClient
from search_agent.models.api import SearchRequest, SearchResponse
from search_agent.service import SearchAgentService
from search_agent.tools.memory import InMemorySearchTool


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _short(text: str, limit: int = 48) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def make_event_printer(stream: TextIO | None = None) -> Callable[[str, dict[str, Any]], None]:
    """Render one line per harness stage -- the loop's 'thinking' timeline."""
    out = stream or sys.stdout

    def sink(event: str, state: dict[str, Any]) -> None:
        if event == "understand":
            job = state.get("job_profile")
            if job is None:
                return
            abilities = [a.name for a in job.must_abilities]
            print(
                f"  [1/8 understand] 岗位={job.title}  必须能力={abilities}  "
                f"硬条件={len(job.hard_constraints)} 条",
                file=out,
            )
        elif event == "plan":
            paths = state.get("paths") or []
            detail = " | ".join(f"{p.id}: {_short(p.rationale, 18)}" for p in paths)
            print(f"  [2/8 plan] 生成 {len(paths)} 条搜索路径 → {detail}", file=out)
        elif event == "execute":
            results = state.get("path_results") or []
            recalled = sum(pr.tool_result.total for pr in results)
            qualities = ", ".join(f"{pr.path_id}:{pr.quality}" for pr in results)
            print(
                f"  [3/8 execute] 并行执行 {len(results)} 条路径，"
                f"召回 {recalled} 人次（{qualities}）",
                file=out,
            )
        elif event == "judge":
            print(
                f"  [4/8 judge] 能力级判断完成，累计合格候选人 "
                f"{len(state.get('accumulated') or {})}",
                file=out,
            )
        elif event == "evaluate":
            reason = state.get("finalize_reason")
            suffix = f"（停止原因：{reason}）" if reason else ""
            print(f"  [5/8 evaluate] → {state.get('phase')}{suffix}", file=out)
        elif event == "reflect":
            reflections = state.get("reflections") or []
            if reflections:
                reflection = reflections[-1]
                print(
                    f"  [6/8 reflect] 共性={reflection.common_traits[:2]}  "
                    f"缺口={reflection.gaps[:2]}  "
                    f"新增软信号={len(reflection.inferred_soft_signals)}",
                    file=out,
                )
        elif event == "revise":
            talent = state.get("talent_profile")
            signals = len(talent.soft_signals) if talent else 0
            converged = state.get("profile_converged")
            print(
                f"  [7/8 revise] 画像软信号={signals}  收敛={converged}  "
                f"迭代={state.get('iteration')}",
                file=out,
            )
        elif event == "finalize":
            converged_search = state.get("final_search_total")
            search_note = (
                f"收敛再搜 {converged_search} 人次"
                if converged_search is not None
                else "合并池重排"
            )
            print(
                f"  [8/8 finalize] 最终 {state.get('final_total')} 人（{search_note}）",
                file=out,
            )
        elif event.startswith("degrade"):
            print(f"  [!] {event} 降级：{state.get('degraded_reason')}", file=out)

    return sink


def print_results(resp: SearchResponse, stream: TextIO | None = None) -> None:
    out = stream or sys.stdout
    if not resp.items:
        print("  （无结果）", file=out)
        return
    print(f"\n  共 {resp.total} 人，展示前 {len(resp.items)} 人：", file=out)
    header = f"  {'#':>2}  {_pad('姓名', 8)} {'年限':>4}  {'分数':>5}  {_pad('命中能力', 34)} 来源"
    print(header, file=out)
    print("  " + "-" * 78, file=out)
    for index, item in enumerate(resp.items, 1):
        explanation = item.matchExplanation or {}
        matched = "、".join((explanation.get("matchedAbilities") or [])[:3]) or "-"
        rationale = (explanation.get("pathRationale") or [None])[0] or "-"
        years = "-" if item.workYears is None else f"{item.workYears:g}"
        print(
            f"  {index:>2}  {_pad(item.name or '-', 8)} {years:>4}  "
            f"{item.score:>5.2f}  {_pad(_short(matched, 30), 34)} {_short(str(rationale), 18)}",
            file=out,
        )


def print_metrics(resp: SearchResponse, stream: TextIO | None = None) -> None:
    out = stream or sys.stdout
    debug = resp.debug or {}
    metrics = debug.get("agentMetrics") or {}
    if not metrics:
        return
    meta = metrics.get("meta") or {}
    rounds = metrics.get("rounds") or {}
    recall = metrics.get("recall") or {}
    print(
        f"\n  运行指标: LLM 调用 {meta.get('llmCalls')} 次 · 迭代 {rounds.get('iterations')} 轮 · "
        f"召回完成度 {recall.get('rate')}（{recall.get('judged')}/{recall.get('target')}） · "
        f"降级={meta.get('degradedReason') or '无'}",
        file=out,
    )
    issues = debug.get("validationIssues") or []
    if issues:
        print(f"  输出校验: {issues}", file=out)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="search-agent",
        description="Eight-stage reflective retrieval agent (offline demo + real LLM mode).",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    demo = sub.add_parser("demo", help="offline scripted demo (no API key needed)")
    demo.add_argument("query", nargs="*", help="optional custom queries")
    demo.add_argument("--data", default=None, help="candidate JSONL path")
    demo.add_argument("--top-k", type=int, default=10)
    demo.add_argument("--json", action="store_true", help="print raw JSON response")

    search = sub.add_parser("search", help="run a real search (needs an LLM API key)")
    search.add_argument("query")
    search.add_argument(
        "--offline", action="store_true", help="use the scripted demo LLM instead of a real API"
    )
    search.add_argument("--data", default=None, help="candidate JSONL path")
    search.add_argument("--top-k", type=int, default=10)
    search.add_argument("--json", action="store_true", help="print raw JSON response")

    eval_cmd = sub.add_parser("eval", help="offline evaluation: full loop vs single-pass baseline")
    eval_cmd.add_argument("query", nargs="*", help="optional custom queries")
    eval_cmd.add_argument("--data", default=None, help="candidate JSONL path")
    eval_cmd.add_argument("--top-k", type=int, default=10)
    eval_cmd.add_argument("--markdown", action="store_true", help="markdown tables")
    return parser


def _run_demo_command(args: argparse.Namespace) -> int:
    queries = args.query or DEMO_QUERIES
    for index, query in enumerate(queries, 1):
        print(f"\n{'=' * 72}\n查询 {index}: {query}\n{'=' * 72}")
        started = time.perf_counter()
        resp = run_demo(
            query,
            top_k=args.top_k,
            data_path=args.data,
            event_sink=make_event_printer(),
        )
        elapsed = time.perf_counter() - started
        if args.json:
            print(resp.model_dump_json(by_alias=True, indent=2))
        else:
            print_results(resp)
            print_metrics(resp)
            print(f"  用时 {elapsed * 1000:.0f} ms")
    return 0


def _run_search_command(args: argparse.Namespace) -> int:
    llm = ScriptedLlmClient() if args.offline else OpenAILlmClient()
    service = SearchAgentService(
        tool=InMemorySearchTool(args.data or DEMO_DATA),
        llm_client=llm,
        event_sink=make_event_printer(),
    )
    try:
        resp = service.search(SearchRequest(query=args.query, topK=args.top_k, details=True))
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(resp.model_dump_json(by_alias=True, indent=2))
    else:
        print_results(resp)
        print_metrics(resp)
    return 0


def _run_eval_command(args: argparse.Namespace) -> int:
    return run_eval(
        args.query or None,
        data_path=args.data,
        top_k=args.top_k,
        markdown=args.markdown,
    )


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "demo":
        return _run_demo_command(args)
    if args.command == "search":
        return _run_search_command(args)
    if args.command == "eval":
        return _run_eval_command(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
