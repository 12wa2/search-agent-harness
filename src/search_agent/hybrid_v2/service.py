"""Standalone Hybrid V2 ``SearchTool`` implementation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from search_agent.contract.tool import (
    RerankToolRequest,
    SearchToolRequest,
    SearchToolResult,
    ToolCandidate,
)
from search_agent.models.api import CandidateItem
from search_agent.tools.text import quality_label

from .compiler import compile_ir_to_hybrid_result
from .config import HybridV2Config
from .embedding import Embedder, build_embedder
from .es_client import ElasticsearchClient
from .funnel import FunnelLimits
from .hits import extract_hard_conditions, hard_facts_for_candidate, hits_to_items
from .onet_position_normalizer import normalize_onet_position_intent
from .query_builder import build_hybrid_query_plan, sanitize_body_for_trace
from .reranker import HybridV2Reranker
from .work_stats_normalizer import normalize_work_stats_ir


def _load_tag_mapping(path: str) -> dict[str, int]:
    if not path:
        return {}
    target = Path(path).expanduser()
    if not target.is_file():
        raise FileNotFoundError(f"tag dictionary not found: {target}")
    raw = json.loads(target.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        rows = raw.get("items") or raw.get("tags")
        if isinstance(rows, list):
            return _tag_rows_to_mapping(rows)
        mapping: dict[str, int] = {}
        for name, tag_id in raw.items():
            try:
                mapping[str(name)] = int(tag_id)
            except (TypeError, ValueError):
                continue
        return mapping
    if isinstance(raw, list):
        return _tag_rows_to_mapping(raw)
    raise ValueError("tag dictionary must be a JSON object or list")


def _tag_rows_to_mapping(rows: list[Any]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("label") or "").strip()
        try:
            tag_id = int(row.get("tag_id", row.get("tagId", row.get("id"))))
        except (TypeError, ValueError):
            continue
        if name:
            mapping[name] = tag_id
    return mapping


def _response_total(response: dict[str, Any]) -> tuple[int, str]:
    hits = response.get("hits") if isinstance(response, dict) else None
    hits = hits if isinstance(hits, dict) else {}
    raw_total = hits.get("total")
    if isinstance(raw_total, dict):
        try:
            total = int(raw_total.get("value") or 0)
        except (TypeError, ValueError):
            total = 0
        relation = str(raw_total.get("relation") or "eq")
        return total, relation
    try:
        return int(raw_total or 0), "eq"
    except (TypeError, ValueError):
        return 0, "eq"


def _response_hits(response: dict[str, Any]) -> list[dict[str, Any]]:
    hits = response.get("hits") if isinstance(response, dict) else None
    hits = hits if isinstance(hits, dict) else {}
    rows = hits.get("hits")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _with_hard_facts(
    item: CandidateItem,
    hard_conditions: list[dict[str, Any]],
) -> CandidateItem:
    facts = hard_facts_for_candidate(item, hard_conditions)
    explanation = dict(item.matchExplanation or {})
    if facts:
        matched = sum(1 for fact in facts if fact.get("matched"))
        explanation.setdefault("summary", f"命中 {matched}/{len(facts)} 条硬条件")
        explanation["hardMatches"] = facts
        explanation.setdefault("confidence", round(matched / len(facts), 4))
    return item.model_copy(update={"matchExplanation": explanation})


def _as_tool_candidate(item: CandidateItem, *, elapsed_ms: int) -> ToolCandidate:
    explanation = item.matchExplanation if isinstance(item.matchExplanation, dict) else {}
    hard_matches = explanation.get("hardMatches")
    if not isinstance(hard_matches, list):
        hard_matches = getattr(item, "hard_matches", [])
    return ToolCandidate(
        doc_id=item.docId,
        score=float(item.score or 0.0),
        item=item,
        hard_matches=[row for row in hard_matches if isinstance(row, dict)],
        match_explanation=explanation,
        path_timings_ms={"total": elapsed_ms},
    )


class HybridV2SearchService:
    """Hard-filtered, BM25 + kNN recall with local-first reranking.

    Every collaborating component is injectable, which keeps the pipeline
    testable without a live Elasticsearch cluster or API key.
    """

    def __init__(
        self,
        config: HybridV2Config | None = None,
        *,
        es_client: Any | None = None,
        embedder: Embedder | None = None,
        reranker: HybridV2Reranker | Any | None = None,
        tag_name_to_id: dict[str, int] | None = None,
    ) -> None:
        self._config = config or HybridV2Config.from_env()
        self._es = es_client or ElasticsearchClient(self._config)
        self._embedder = embedder or build_embedder(self._config)
        self._reranker = reranker or HybridV2Reranker(self._config)
        self._tag_name_to_id = (
            dict(tag_name_to_id)
            if tag_name_to_id is not None
            else _load_tag_mapping(self._config.tag_dict_path)
        )
        self._tag_id_to_name = {tag_id: name for name, tag_id in self._tag_name_to_id.items()}

    @property
    def config(self) -> HybridV2Config:
        return self._config

    def search_with_intent(self, req: SearchToolRequest) -> SearchToolResult:
        started = time.perf_counter()
        timings: dict[str, int] = {}

        normalize_started = time.perf_counter()
        ir = normalize_work_stats_ir(req.intent, req.query_text)
        ir = normalize_onet_position_intent(ir, req.query_text)
        timings["normalize"] = int((time.perf_counter() - normalize_started) * 1000)

        compile_started = time.perf_counter()
        parsed = compile_ir_to_hybrid_result(
            ir,
            tag_name_to_id=self._tag_name_to_id,
            query_text=req.query_text,
        )
        hard_conditions = extract_hard_conditions(ir)
        timings["compile"] = int((time.perf_counter() - compile_started) * 1000)

        funnel = FunnelLimits.from_config(self._config)
        recall_pool_size = funnel.recall_pool_size(req.top_k, req.recall_pool_size)
        build_started = time.perf_counter()
        plan = build_hybrid_query_plan(
            parsed,
            company_id=req.company_id,
            permission=req.permission,
            top_k=recall_pool_size,
            embedder=self._embedder,
            config=self._config,
            exclude_doc_ids=req.exclude_doc_ids,
        )
        timings["buildQuery"] = int((time.perf_counter() - build_started) * 1000)

        search_started = time.perf_counter()
        response = self._es.search(plan.body)
        timings["esSearch"] = int((time.perf_counter() - search_started) * 1000)
        total, total_relation = _response_total(response)
        raw_hits = _response_hits(response)
        items = hits_to_items(
            raw_hits,
            score_max=plan.score_max,
            soft_clauses=list(plan.soft_clauses),
            match_rate_baseline=self._config.match_rate_baseline,
            tag_id_to_name=self._tag_id_to_name,
        )
        items = [_with_hard_facts(item, hard_conditions) for item in items]

        degraded = list(plan.degraded_reasons)
        if items and not req.skip_rerank:
            scan_n = min(
                len(items),
                max(req.top_k, min(self._config.rerank_scan_cap, self._config.rerank_target_n)),
            )
            rerank_started = time.perf_counter()
            reranked, rerank_meta = self._reranker.rerank(
                items[:scan_n],
                query=req.query_text,
                soft_texts=[condition.text for condition in parsed.soft],
            )
            timings["rerank"] = int((time.perf_counter() - rerank_started) * 1000)
            if reranked:
                items = reranked
            status = str((rerank_meta or {}).get("status") or "unknown")
            if status not in {"ok", "skipped"}:
                degraded.append(f"rerank_{status}")
        else:
            items = items[: req.top_k]

        delivered = items[: req.top_k]
        qualified = sum(
            1
            for item in delivered
            if float(item.score or 0.0) >= self._config.rerank_min_confidence
        )
        quality = quality_label(qualified, max(total, len(delivered)), req.top_k)
        timings["total"] = int((time.perf_counter() - started) * 1000)
        return SearchToolResult(
            items=[_as_tool_candidate(item, elapsed_ms=timings["total"]) for item in delivered],
            total=max(total, len(delivered)),
            total_relation=total_relation,
            quality=quality,
            degraded_reason=";".join(dict.fromkeys(degraded)) or None,
            es_query=sanitize_body_for_trace(plan.body) if req.details else None,
            timings_ms=timings,
        )

    def rerank_pool(self, req: RerankToolRequest) -> SearchToolResult:
        started = time.perf_counter()
        hard_conditions = [
            row
            for row in list(req.job_context.get("hard_constraints") or [])
            if isinstance(row, dict)
        ]
        items = [_with_hard_facts(candidate.item, hard_conditions) for candidate in req.candidates]
        reranked, rerank_meta = self._reranker.rerank(
            items,
            query=req.query_text,
            soft_texts=[req.query_text],
        )
        delivered = reranked[: req.top_k]
        elapsed = int((time.perf_counter() - started) * 1000)
        status = str((rerank_meta or {}).get("status") or "unknown")
        return SearchToolResult(
            items=[_as_tool_candidate(item, elapsed_ms=elapsed) for item in delivered],
            total=len(reranked),
            quality=quality_label(len(delivered), len(reranked), req.top_k),
            degraded_reason=(f"rerank_{status}" if status not in {"ok", "skipped"} else None),
            timings_ms={"rerank": elapsed, "total": elapsed},
        )

    def health(self) -> dict[str, Any]:
        payload = self._es.health()
        return {"engine": "hybrid_v2", **payload, "config": self._config.as_dict()}


__all__ = ["HybridV2SearchService"]
