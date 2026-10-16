"""Elasticsearch SearchTool: a real search engine behind the same protocol.

This is the backend the in-memory reference stands in for. It keeps the same
contract, the same explainable facts and the same rankings as
``tools/memory.py`` and ``tools/sqlite_fts.py``:

* the ``WhereNode`` tree is compiled to real Elasticsearch DSL (``terms`` /
  ``range`` / ``bool.must_not`` / ``minimum_should_match``); with
  ``details=True`` that exact DSL is returned as ``es_query``;
* hits are re-verified and scored with the shared ``tools/text.py`` helpers,
  so swapping engines never changes the agent-visible result;
* ``search_text`` exposes a BM25 channel over the indexed candidate text.
* ``search_semantic`` / ``hybrid_search`` expose the recall funnel: kNN over an
  indexed ``dense_vector`` field plus BM25+kNN fused by reciprocal rank fusion.

The ``elasticsearch`` client package is imported lazily, so the DSL compiler
and the offline unit tests work without it. Start a node with::

    docker compose up -d elasticsearch
    pip install -e ".[es]"
    python examples/es_end_to_end.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Sequence

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.models.api import CandidateItem
from search_agent.models.ir import StructuredConditionIR, WhereNode
from search_agent.tools.embedding import EMBEDDING_DIMS, embed_text
from search_agent.tools.memory import load_candidates
from search_agent.tools.text import (
    TERM_FIELDS,
    ability_hits,
    as_number,
    build_tool_candidate,
    candidate_text,
    collect_leaves,
    hard_facts,
    normalized_terms,
    quality_label,
    soft_score,
    where_matches,
)

__all__ = ["ElasticsearchSearchTool", "compile_where"]

DEFAULT_ES_URL = "http://127.0.0.1:9200"

#: IR fields that alias another indexed keyword field.
_ALIAS_FIELDS = {"live_city": "city"}
#: ``latest_*`` fields index only the newest entry (mirrors ``companies[:1]``).
_LATEST_FIELDS = ("latest_company", "latest_work_job")
_NUMERIC_FIELDS = ("work_years", "age")

_MAPPINGS: dict[str, Any] = {
    "properties": {
        "doc_id": {"type": "keyword"},
        "text_blob": {"type": "text"},
        "semantic_vector": {
            "type": "dense_vector",
            "dims": EMBEDDING_DIMS,
            "index": True,
            "similarity": "cosine",
        },
        "work_years": {"type": "float"},
        "age": {"type": "float"},
        **{field: {"type": "keyword"} for field in TERM_FIELDS},
        "latest_company": {"type": "keyword"},
        "latest_work_job": {"type": "keyword"},
        "payload": {"type": "object", "enabled": False},
    }
}

_MATCH_ALL: dict[str, Any] = {"match_all": {}}
_MATCH_NONE: dict[str, Any] = {"match_none": {}}


def _compile_node(node: WhereNode | None) -> tuple[dict[str, Any], bool]:
    """Compile one node; returns ``(dsl, exact)`` like the SQL compiler."""
    if node is None:
        return _MATCH_ALL, True
    if node.type in ("and", "or"):
        parts = [_compile_node(child) for child in node.children]
        exact = all(part[1] for part in parts)
        queries = [part[0] for part in parts]
        if node.type == "and":
            return {"bool": {"filter": queries}}, exact
        need = int(node.minimumShouldMatch or 1)
        return {"bool": {"should": queries, "minimum_should_match": need}}, exact
    if node.type == "not":
        child, exact = _compile_node(node.child)
        if not exact:
            return _MATCH_ALL, False
        return {"bool": {"must_not": [child]}}, True
    if node.condition is None:
        return _MATCH_ALL, False
    return _compile_leaf(node.condition)


def _compile_leaf(cond: StructuredConditionIR) -> tuple[dict[str, Any], bool]:
    field = _ALIAS_FIELDS.get(cond.field, cond.field)
    if field in _NUMERIC_FIELDS:
        return _compile_numeric_leaf(field, cond)
    if field in _LATEST_FIELDS or field in TERM_FIELDS:
        return _compile_term_leaf(field, cond)
    # Unknown to the shared evaluator as well: stay permissive.
    return _MATCH_ALL, False


def _compile_numeric_leaf(field: str, cond: StructuredConditionIR) -> tuple[dict[str, Any], bool]:
    if cond.operator not in ("gte", "lte", "range"):
        return _MATCH_NONE, True
    numbers = [n for n in (as_number(value) for value in cond.values) if n is not None]
    if not numbers:
        return _MATCH_NONE, True
    if cond.operator == "gte":
        return {"range": {field: {"gte": numbers[0]}}}, True
    if cond.operator == "lte":
        return {"range": {field: {"lte": numbers[0]}}}, True
    low, high = sorted(numbers[:2])
    return {"range": {field: {"gte": low, "lte": high}}}, True


def _compile_term_leaf(field: str, cond: StructuredConditionIR) -> tuple[dict[str, Any], bool]:
    wanted = sorted({str(v).strip().lower() for v in cond.values if str(v).strip()})
    if not wanted:
        return _MATCH_NONE, True
    terms: dict[str, Any] = {"terms": {field: wanted}}
    if cond.operator in ("any", "eq"):
        return terms, True
    if cond.operator == "not":
        return {"bool": {"must_not": [terms]}}, True
    if cond.operator == "all":
        clauses = [{"terms": {field: [value]}} for value in wanted]
        return {"bool": {"filter": clauses}}, True
    # numeric operator on a term-backed field: the shared evaluator says False
    return _MATCH_NONE, True


def compile_where(node: WhereNode | None) -> dict[str, Any]:
    """Compile a ``WhereNode`` tree to Elasticsearch DSL (pure, offline-testable).

    Returns ``{"query": <dsl>, "exact": bool}``; ``exact=False`` marks a
    deliberate over-approximation (unknown field / non-compilable ``not``)
    that the shared Python evaluator tightens after the engine pass.
    """
    query, exact = _compile_node(node)
    return {"query": query, "exact": exact}


class ElasticsearchSearchTool:
    """Elasticsearch implementation of the ``SearchTool`` protocol."""

    def __init__(
        self,
        candidates: str | Path | Sequence[dict[str, Any] | CandidateItem],
        *,
        url: str | None = None,
        index: str = "search-agent-candidates",
        recreate: bool = False,
        client: Any | None = None,
    ) -> None:
        if client is None:
            try:
                from elasticsearch import Elasticsearch
            except ImportError as exc:  # pragma: no cover - depends on env
                raise ImportError(
                    "ElasticsearchSearchTool needs the 'elasticsearch' package: "
                    'pip install "search-agent-harness[es]"'
                ) from exc
            url = url or os.environ.get("SEARCH_AGENT_ES_URL", DEFAULT_ES_URL)
            client = Elasticsearch(url)
            try:
                client.info()
            except Exception as exc:  # noqa: BLE001 - any connection failure
                raise RuntimeError(
                    f"Elasticsearch not reachable at {url}; start one with: "
                    "docker compose up -d elasticsearch"
                ) from exc
        self._client = client
        self._url = url or os.environ.get("SEARCH_AGENT_ES_URL", DEFAULT_ES_URL)
        self._index = index
        self._items = load_candidates(candidates)
        self._by_id = {item.docId: item for item in self._items}
        self._ensure_index(recreate=recreate)

    @property
    def size(self) -> int:
        return len(self._items)

    @property
    def url(self) -> str:
        return self._url

    @property
    def index_name(self) -> str:
        return self._index

    def close(self) -> None:
        close = getattr(self._client, "close", None)
        if callable(close):
            close()

    # -- indexing ------------------------------------------------------------

    def _ensure_index(self, *, recreate: bool) -> None:
        if recreate and self._client.indices.exists(index=self._index):
            self._client.indices.delete(index=self._index)
        if not self._client.indices.exists(index=self._index):
            self._client.indices.create(index=self._index, mappings=_MAPPINGS)
        else:
            # Indexes created before the vector channel only need the one new field;
            # the corpus is re-indexed below either way.
            self._client.indices.put_mapping(
                index=self._index,
                properties={"semantic_vector": _MAPPINGS["properties"]["semantic_vector"]},
            )
        for item in self._items:
            self._client.index(index=self._index, id=item.docId, document=self._document(item))
        self._client.indices.refresh(index=self._index)

    @staticmethod
    def _document(item: CandidateItem) -> dict[str, Any]:
        blob = candidate_text(item)
        terms = normalized_terms(item)
        document: dict[str, Any] = {
            "doc_id": item.docId,
            "text_blob": blob,
            "semantic_vector": embed_text(blob),
            "payload": item.model_dump(by_alias=True),
            "work_years": item.workYears if isinstance(item.workYears, (int, float)) else None,
            "age": item.age if isinstance(item.age, (int, float)) else None,
            "latest_company": terms.get("company", [])[:1],
            "latest_work_job": terms.get("position", [])[:1],
        }
        for field, values in terms.items():
            document[field] = values
        return document

    # -- extra channels ------------------------------------------------------

    def search_text(
        self, query: str, *, top_k: int = 5, where: WhereNode | None = None
    ) -> list[tuple[str, float]]:
        """Keyword channel: BM25 over the indexed candidate text."""
        match: dict[str, Any] = {"match": {"text_blob": {"query": query, "operator": "and"}}}
        query_dsl: dict[str, Any] = match
        if where is not None:
            query_dsl = {"bool": {"must": [match], "filter": [compile_where(where)["query"]]}}
        response = self._client.search(index=self._index, query=query_dsl, size=max(0, int(top_k)))
        return [
            (str(hit["_id"]), round(float(hit["_score"]), 4)) for hit in response["hits"]["hits"]
        ]

    def search_semantic(
        self,
        query_text: str,
        *,
        top_k: int = 5,
        where: WhereNode | None = None,
        num_candidates: int | None = None,
    ) -> list[tuple[str, float]]:
        """Vector channel: kNN over the indexed ``dense_vector`` field."""
        if top_k <= 0:
            return []
        vector = embed_text(query_text)
        if not any(vector):  # cosine similarity rejects zero-magnitude vectors
            return []
        body: dict[str, Any] = {
            "field": "semantic_vector",
            "query_vector": vector,
            "k": int(top_k),
            "num_candidates": int(num_candidates or max(50, int(top_k) * 10)),
        }
        if where is not None:
            body["filter"] = compile_where(where)["query"]
        response = self._client.search(index=self._index, knn=body, size=int(top_k))
        return [
            (str(hit["_id"]), round(float(hit["_score"]), 4)) for hit in response["hits"]["hits"]
        ]

    def hybrid_search(
        self,
        query_text: str,
        *,
        top_k: int = 5,
        where: WhereNode | None = None,
        branch_k: int = 20,
        rank_constant: int = 60,
    ) -> list[dict[str, Any]]:
        """Recall funnel: BM25 + kNN fused with reciprocal rank fusion (RRF).

        ``score = sum(1 / (rank_constant + rank))`` over the branches that returned the
        hit; every row keeps the per-branch rank and score, so a caller can explain
        where a candidate came from.
        """
        branches = (
            ("bm25", self.search_text(query_text, top_k=branch_k, where=where)),
            ("knn", self.search_semantic(query_text, top_k=branch_k, where=where)),
        )
        merged: dict[str, dict[str, float]] = {}
        for name, hits in branches:
            for rank, (doc_id, score) in enumerate(hits, start=1):
                row = merged.setdefault(doc_id, {})
                row[f"{name}_rank"] = rank
                row[f"{name}_score"] = float(score)
                row["score"] = row.get("score", 0.0) + 1.0 / (rank_constant + rank)
        ranked = sorted(merged.items(), key=lambda entry: (-entry[1]["score"], entry[0]))
        return [
            {
                "docId": doc_id,
                "score": round(float(values["score"]), 6),
                "bm25Rank": values.get("bm25_rank"),
                "bm25Score": values.get("bm25_score"),
                "knnRank": values.get("knn_rank"),
                "knnScore": values.get("knn_score"),
            }
            for doc_id, values in ranked[: max(0, int(top_k))]
        ]

    # -- WhereNode -> DSL ----------------------------------------------------

    def compile_where(self, node: WhereNode | None) -> dict[str, Any]:
        """Expose the DSL the backend would run for an intent (debugging aid)."""
        return compile_where(node)

    # -- SearchTool protocol -------------------------------------------------

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        started = time.perf_counter()
        excluded = set(req.exclude_doc_ids or [])
        compiled = compile_where(req.intent.where)
        filters: list[dict[str, Any]] = [compiled["query"]]
        if excluded:
            filters.append({"bool": {"must_not": [{"terms": {"doc_id": sorted(excluded)}}]}})
        response = self._client.search(
            index=self._index,
            query={"bool": {"filter": filters}},
            size=max(1, self.size),
        )
        # The DSL is at worst an over-approximation: the shared Python evaluator
        # stays the single source of truth for constraint semantics.
        filtered = [
            self._by_id[str(hit["_id"])]
            for hit in response["hits"]["hits"]
            if str(hit["_id"]) in self._by_id
            and where_matches(req.intent.where, self._by_id[str(hit["_id"])])
        ]
        filtered_ms = int((time.perf_counter() - started) * 1000)

        score_started = time.perf_counter()
        leaves = collect_leaves(req.intent.where)
        scored: list[tuple[float, list[dict[str, Any]], list[dict[str, Any]], CandidateItem]] = []
        for item in filtered:
            blob = candidate_text(item)
            hard = hard_facts(leaves, item)
            score, soft = soft_score(req.intent.semantic, blob)
            scored.append((score, hard, soft, item))
        score_ms = int((time.perf_counter() - score_started) * 1000)

        scored.sort(key=lambda row: (-row[0], row[3].docId))
        total = len(scored)
        page_size = int(req.recall_pool_size or req.top_k)
        page = scored[: max(0, page_size)]
        items = [build_tool_candidate(score, hard, soft, item) for score, hard, soft, item in page]
        quality = quality_label(len(items), total, page_size)

        es_query: dict[str, Any] | None = None
        if req.details:
            es_query = {
                "backend": "elasticsearch",
                "url": self._url,
                "index": self._index,
                "query": {"bool": {"filter": filters}},
                "exact": compiled["exact"],
                "filteredCount": total,
                "excludedCount": len(excluded),
                "semanticConditions": [s.text for s in req.intent.semantic or []],
            }

        return SearchToolResult(
            items=items,
            total=total,
            total_relation="eq",
            quality=quality,
            es_query=es_query,
            timings_ms={
                "filter": filtered_ms,
                "score": score_ms,
                "total": int((time.perf_counter() - started) * 1000),
            },
        )

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        started = time.perf_counter()
        abilities = [str(a) for a in (req.job_context.get("abilities") or [])]
        job_hard: list[StructuredConditionIR] = []
        for raw in req.job_context.get("hard_constraints") or []:
            try:
                job_hard.append(StructuredConditionIR.model_validate(raw))
            except Exception:  # noqa: BLE001 - ignore malformed job facts
                continue

        scored: list[tuple[float, list[dict[str, Any]], ToolCandidate]] = []
        for candidate in req.candidates:
            blob = candidate_text(candidate.item)
            hits = ability_hits(abilities, blob)
            hard = hard_facts(job_hard, candidate.item)
            score = round(1.0 + 0.5 * hits + 0.2 * sum(1 for h in hard if h["matched"]), 4)
            scored.append((score, hard, candidate))

        scored.sort(key=lambda row: (-row[0], row[2].doc_id))
        top = scored[: max(0, int(req.top_k))]
        items = [
            build_tool_candidate(score, hard, [], candidate.item) for score, hard, candidate in top
        ]
        return SearchToolResult(
            items=items,
            total=len(scored),
            total_relation="eq",
            quality="rich" if items else "empty",
            timings_ms={"total": int((time.perf_counter() - started) * 1000)},
        )
