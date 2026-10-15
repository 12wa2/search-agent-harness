"""Show the hybrid recall funnel: BM25 vs kNN vs reciprocal rank fusion.

Prerequisites::

    docker compose up -d elasticsearch
    pip install -e ".[es]"

Usage::

    python examples/es_hybrid_recall.py ["自定义查询"]

The point of the demo is the *funnel*, not the ranking quality: a long keyword
query that the AND-only BM25 channel answers with zero hits is exactly the case
the vector channel (and rank fusion) exists for. Vectors come from the bundled
deterministic embedding -- swap ``tools/embedding.py::embed_text`` for a real
model and re-index to move to production semantics.
"""

from __future__ import annotations

import sys

from search_agent.demo import DEMO_DATA
from search_agent.tools.elasticsearch import ElasticsearchSearchTool
from search_agent.tools.memory import load_candidates

QUERY = "高并发 秒杀 大促 稳定性治理 后端架构"
INDEX = "search-agent-demo"


def _print_rows(title: str, rows, names, mapper) -> None:
    print(f"\n[{title}] {len(rows)} 条")
    if not rows:
        print("  （无命中）")
        return
    for row in rows:
        doc_id = row["docId"] if isinstance(row, dict) else row[0]
        detail = mapper(row)
        print(f"  {names.get(doc_id, '?'):<6} {doc_id}  {detail}")


def main() -> int:
    query = sys.argv[1] if len(sys.argv) > 1 else QUERY
    try:
        tool = ElasticsearchSearchTool(DEMO_DATA, index=INDEX, recreate=True)
    except (RuntimeError, ImportError) as exc:
        print(f"[skip] {exc}")
        return 1

    names = {item.docId: (item.name or "") for item in load_candidates(DEMO_DATA)}
    print(f"indexed {tool.size} candidates -> {tool.url} (index: {tool.index_name})")
    print(f"\nquery: {query}")

    keyword = tool.search_text(query, top_k=5)
    vector = tool.search_semantic(query, top_k=5)
    fused = tool.hybrid_search(query, top_k=5)

    _print_rows("BM25 关键词通道 (operator=and)", keyword, names, lambda row: f"score={row[1]}")
    _print_rows(
        "kNN 向量通道 (dense_vector + cosine)", vector, names, lambda row: f"score={row[1]}"
    )
    _print_rows(
        "RRF 混合召回 (rank_constant=60)",
        fused,
        names,
        lambda row: (
            f"rrf={row['score']:.4f}  bm25={row['bm25Rank'] or '-'}  knn={row['knnRank'] or '-'}"
        ),
    )

    if not keyword and vector:
        print("\n关键词通道 0 命中、向量通道有召回 —— 这正是混合召回要解决的场景。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
