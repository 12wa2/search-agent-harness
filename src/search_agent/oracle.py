"""Oracle metrics: grade delivered results against generator ground truth.

The agent's own judge decides "qualified", which makes the headline number
self-reported -- the pipeline grading itself. These metrics take the loop out of
its own exam: relevance comes from the corpus generator, and the report splits
the candidates a keyword filter can reach from the ones only a semantic strategy
could ever surface.

Usage::

    from search_agent.corpus import generate_corpus
    from search_agent.oracle import score_against_oracle

    corpus = generate_corpus(size=120, seed=7)
    score = score_against_oracle(corpus, corpus.queries[0], delivered_doc_ids)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence, TextIO

from search_agent.corpus import QuerySpec, SyntheticCorpus

__all__ = ["Novelty", "OracleScore", "novelty", "print_oracle_report", "score_against_oracle"]

_LABELS = ("单趟基线", "Agent 全循环")

_LEGEND = (
    "  「关键词可见」= 合格候选人中，所需能力有技能/关键词证据的那部分\n"
    "  「仅语义可见」= 只有项目语言、没有任何关键词证据的那部分（词法策略的盲区）"
)


@dataclass(frozen=True)
class OracleScore:
    """Delivered-list quality against ground truth (not against the judge)."""

    relevant: int
    found: int
    delivered: int
    keyword_relevant: int
    keyword_found: int
    semantic_relevant: int
    semantic_found: int

    @property
    def recall(self) -> float:
        """Share of all truly-qualified candidates that were delivered."""
        return self.found / self.relevant if self.relevant else 0.0

    @property
    def precision(self) -> float:
        """Share of the delivered list that is truly qualified."""
        return self.found / self.delivered if self.delivered else 0.0

    @property
    def keyword_recall(self) -> float:
        """Recall over the keyword-visible half (what lexical search can reach)."""
        return self.keyword_found / self.keyword_relevant if self.keyword_relevant else 0.0

    @property
    def semantic_recall(self) -> float:
        """Recall over the semantic-only half (project-language resumes)."""
        return self.semantic_found / self.semantic_relevant if self.semantic_relevant else 0.0


def score_against_oracle(
    corpus: SyntheticCorpus,
    spec: QuerySpec,
    doc_ids: Iterable[str],
) -> OracleScore:
    """Recall / precision of one delivered list against the generator's labels."""
    delivered = {str(doc_id) for doc_id in doc_ids}
    relevant = set(corpus.relevant(spec))
    semantic = set(corpus.semantic_only(spec))
    keyword = relevant - semantic
    found = delivered & relevant
    return OracleScore(
        relevant=len(relevant),
        found=len(found),
        delivered=len(delivered),
        keyword_relevant=len(keyword),
        keyword_found=len(found & keyword),
        semantic_relevant=len(semantic),
        semantic_found=len(found & semantic),
    )


@dataclass(frozen=True)
class Novelty:
    """Candidates only the loop surfaced, plus how many are truly qualified."""

    unique: int
    relevant: int

    @property
    def relevance_rate(self) -> float:
        return self.relevant / self.unique if self.unique else 0.0


def novelty(
    corpus: SyntheticCorpus,
    spec: QuerySpec,
    baseline_ids: Iterable[str],
    agent_ids: Iterable[str],
) -> Novelty:
    """Candidates unique to the agent session, and how many are truly qualified.

    Callers pass *surfaced* doc ids (every candidate a pipeline ever saw, not just
    the delivered page): discovery is where the loop adds value, while delivered
    quality is already covered by the recall / precision tables.
    """
    baseline = {str(doc_id) for doc_id in baseline_ids}
    unique = {str(doc_id) for doc_id in agent_ids} - baseline
    relevant = unique & set(corpus.relevant(spec))
    return Novelty(unique=len(unique), relevant=len(relevant))


#: One report row: query spec, baseline score, agent score, agent-only novelty.
Row = tuple[QuerySpec, OracleScore, OracleScore, Novelty]


def _rate(value: float, found: int, total: int) -> str:
    return f"{value:.2f} ({found}/{total})"


def _micro(scores: Sequence[OracleScore]) -> dict[str, float]:
    """Micro-average over queries: sum of hits over sum of opportunities."""

    def ratio(found_attr: str, total_attr: str) -> float:
        total = sum(getattr(score, total_attr) for score in scores)
        found = sum(getattr(score, found_attr) for score in scores)
        return found / total if total else 0.0

    return {
        "recall": ratio("found", "relevant"),
        "precision": ratio("found", "delivered"),
        "keyword": ratio("keyword_found", "keyword_relevant"),
        "semantic": ratio("semantic_found", "semantic_relevant"),
    }


def print_oracle_report(
    corpus: SyntheticCorpus,
    rows: Sequence[Row],
    *,
    markdown: bool,
    stream: TextIO,
) -> None:
    """Print per-query scores for both pipelines plus micro totals."""
    if markdown:
        _print_markdown(corpus, rows, stream)
    else:
        _print_text(corpus, rows, stream)


def _print_text(
    corpus: SyntheticCorpus,
    rows: Sequence[Row],
    stream: TextIO,
) -> None:
    print("=" * 72, file=stream)
    print(
        f"Oracle 评测：真值来自语料生成器（{corpus.size} 条合成候选人 · seed={corpus.seed}）",
        file=stream,
    )
    print(_LEGEND, file=stream)
    for index, (spec, baseline, agent, only_agent) in enumerate(rows, 1):
        print("-" * 72, file=stream)
        print(f"查询 {index}: {spec.text}", file=stream)
        print(
            f"  合格（oracle）: {baseline.relevant} 人 · "
            f"关键词可见 {baseline.keyword_relevant} · 仅语义可见 {baseline.semantic_relevant}",
            file=stream,
        )
        for label, score in zip(_LABELS, (baseline, agent), strict=True):
            keyword = _rate(score.keyword_recall, score.keyword_found, score.keyword_relevant)
            semantic = _rate(score.semantic_recall, score.semantic_found, score.semantic_relevant)
            print(
                f"  {label}：召回 {_rate(score.recall, score.found, score.relevant)} · "
                f"精确率 {score.precision:.2f} · 关键词可见召回 {keyword} · "
                f"仅语义可见召回 {semantic}",
                file=stream,
            )
        print(
            f"  仅全循环发现: {only_agent.unique} 人 · 其中合格 {only_agent.relevant} 人 "
            f"（独有候选相关率 {only_agent.relevance_rate:.2f}）",
            file=stream,
        )
    _print_totals_text(rows, stream)


def _print_totals_text(
    rows: Sequence[Row],
    stream: TextIO,
) -> None:
    baseline = _micro([row[1] for row in rows])
    agent = _micro([row[2] for row in rows])
    print("=" * 72, file=stream)
    print(f"合计（{len(rows)} 个查询 · micro）", file=stream)
    print(
        f"  召回（交付列表）  : 单趟基线 {baseline['recall']:.2f} → 全循环 {agent['recall']:.2f}",
        file=stream,
    )
    print(
        f"  精确率（交付列表）: 单趟基线 {baseline['precision']:.2f} → "
        f"全循环 {agent['precision']:.2f}",
        file=stream,
    )
    print(
        f"  关键词可见召回    : 单趟基线 {baseline['keyword']:.2f} → 全循环 {agent['keyword']:.2f}",
        file=stream,
    )
    print(
        f"  仅语义可见召回    : 单趟基线 {baseline['semantic']:.2f} → "
        f"全循环 {agent['semantic']:.2f}",
        file=stream,
    )
    unique_total = sum(row[3].unique for row in rows)
    unique_relevant = sum(row[3].relevant for row in rows)
    per_query = unique_relevant / len(rows) if rows else 0.0
    discovered = unique_total / len(rows) if rows else 0.0
    rate = unique_relevant / unique_total if unique_total else 0.0
    print(
        f"  仅全循环发现      : 平均每条查询新增候选 {discovered:.1f} 人，其中合格 "
        f"{per_query:.1f} 人 · 独有候选相关率 {rate:.1%}（{unique_relevant}/{unique_total}）",
        file=stream,
    )
    print(
        "  注：「仅语义可见」目前接近 0 是已知天花板——打分与 grounding 都是词法的，"
        "换成真实 embedding 才能跨越项目语言与能力词之间的表述差异。",
        file=stream,
    )


def _print_markdown(
    corpus: SyntheticCorpus,
    rows: Sequence[Row],
    stream: TextIO,
) -> None:
    print(
        f"### Oracle 指标（真值来自语料生成器 · {corpus.size} 条合成候选人 · "
        f"seed={corpus.seed}）\n",
        file=stream,
    )
    print("| 查询 | 管线 | 召回 | 精确率 | 关键词可见召回 | 仅语义可见召回 |", file=stream)
    print("|---|---|---|---|---|---|", file=stream)
    for index, row in enumerate(rows, 1):
        baseline, agent = row[1], row[2]
        for label, score in zip(_LABELS, (baseline, agent), strict=True):
            print(
                f"| Q{index} | {label} | {_rate(score.recall, score.found, score.relevant)} | "
                f"{score.precision:.2f} | "
                f"{_rate(score.keyword_recall, score.keyword_found, score.keyword_relevant)} | "
                f"{_rate(score.semantic_recall, score.semantic_found, score.semantic_relevant)} |",
                file=stream,
            )

    baseline_totals = _micro([row[1] for row in rows])
    agent_totals = _micro([row[2] for row in rows])
    print(f"\n### Oracle 合计（{len(rows)} 个查询 · micro）\n", file=stream)
    print("| 指标 | 单趟基线 | Agent 全循环 |", file=stream)
    print("|---|---|---|", file=stream)
    print(
        f"| 召回（交付列表） | {baseline_totals['recall']:.2f} | {agent_totals['recall']:.2f} |",
        file=stream,
    )
    print(
        f"| 精确率（交付列表） | {baseline_totals['precision']:.2f} | "
        f"{agent_totals['precision']:.2f} |",
        file=stream,
    )
    print(
        f"| 关键词可见召回 | {baseline_totals['keyword']:.2f} | {agent_totals['keyword']:.2f} |",
        file=stream,
    )
    print(
        f"| 仅语义可见召回 | {baseline_totals['semantic']:.2f} | {agent_totals['semantic']:.2f} |",
        file=stream,
    )
    unique_total = sum(row[3].unique for row in rows)
    unique_relevant = sum(row[3].relevant for row in rows)
    per_query = unique_relevant / len(rows) if rows else 0.0
    discovered = unique_total / len(rows) if rows else 0.0
    rate = unique_relevant / unique_total if unique_total else 0.0
    print("\n### 反思循环的增量（相对单趟基线 · 探索层）\n", file=stream)
    print("| 查询 | 仅全循环发现 | 其中合格 | 独有候选相关率 |", file=stream)
    print("|---|---|---|---|", file=stream)
    for index, row in enumerate(rows, 1):
        novel = row[3]
        print(
            f"| Q{index} | {novel.unique} | {novel.relevant} | {novel.relevance_rate:.2f} |",
            file=stream,
        )
    print(
        f"\n平均每条查询新增候选 **{discovered:.1f} 人**，其中合格 **{per_query:.1f} 人** · "
        f"独有候选相关率 **{rate:.1%}**（{unique_relevant}/{unique_total}）\n",
        file=stream,
    )
    print(
        "> 「仅语义可见」= 简历里只有项目语言、没有任何关键词证据的合格候选人。"
        "当前实现（词法打分 + 无真实 embedding）几乎无法交付这部分人，"
        "这是下一步要修的已知天花板，而不是被隐藏的短板。\n",
        file=stream,
    )
